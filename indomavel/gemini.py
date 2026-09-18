"""Chamadas ao Gemini com resposta em JSON, trocando de modelo quando um está sobrecarregado."""

import json
import threading
import time

from . import config

# Um modelo que respondeu "alta demanda" fica de lado por um tempo, para as próximas chamadas não
# gastarem minutos esperando por ele (na régua de 15/09 isso custou boa parte dos 17 minutos por vídeo).
PAUSA_MODELO_SOBRECARREGADO_S = 120
PAUSA_MODELO_SEM_COTA_S = 60


class GeminiErro(RuntimeError):
    """Nenhum modelo do Gemini respondeu."""


_clientes = {}
_sobrecarregado_ate = {}
_trava = threading.Lock()


def _obter_clientes():
    from google import genai

    chaves = []
    if config.GOOGLE_API_KEY:
        chaves.append(config.GOOGLE_API_KEY)
    stitch = config.valor("STITCH_API_KEY")
    if stitch and stitch not in chaves:
        chaves.append(stitch)
    if not chaves:
        raise GeminiErro("GOOGLE_API_KEY não está no arquivo .env")

    clientes = []
    for k in chaves:
        if k not in _clientes:
            _clientes[k] = genai.Client(api_key=k)
        clientes.append(_clientes[k])
    return clientes


def _obter_cliente():
    return _obter_clientes()[0]


def _passageiro(erro):
    """Erros que passam esperando: sobrecarga, limite por minuto, falha interna."""
    texto = str(erro)
    return any(marca in texto for marca in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE", "INTERNAL"))


def _sem_chave(texto):
    return str(texto).replace(config.GOOGLE_API_KEY, "<chave>") if config.GOOGLE_API_KEY else str(texto)


def motivo_curto(erro):
    """O porquê de o Gemini não ter respondido, em poucas palavras para a tela."""
    texto = str(erro)
    if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
        return "cota do plano gratuito do Gemini esgotada por agora"
    if "503" in texto or "UNAVAILABLE" in texto:
        return "modelos do Gemini sobrecarregados"
    return _sem_chave(texto)[:100]


def ordem_dos_modelos(modelos):
    """Primeiro os modelos que não falharam há pouco; os que estão de lado vão para o fim, como última tentativa.

    Em 15/09 o gemini-2.5-flash ficou de lado por um 503 e os outros estavam sem cota (429): sem essa
    última tentativa, a edição em massa desistiu mesmo com o 2.5 respondendo.
    """
    agora = time.time()
    with _trava:
        disponiveis = [m for m in modelos if _sobrecarregado_ate.get(m, 0) <= agora]
    return disponiveis + [m for m in modelos if m not in disponiveis]


def gerar_json(instrucao, conteudo, esquema, modelos=None, tentativas_por_modelo=2, espera_s=8):
    """Devolve (dados, modelo_usado). Levanta GeminiErro se nenhum modelo da lista responder."""
    from google.genai import types

    clientes = _obter_clientes()
    configuracao = types.GenerateContentConfig(
        system_instruction=instrucao,
        response_mime_type="application/json",
        response_json_schema=esquema,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    falhas = []
    for cliente in clientes:
        for modelo in ordem_dos_modelos(modelos or config.GEMINI_MODELOS):
            for tentativa in range(tentativas_por_modelo):
                try:
                    resposta = cliente.models.generate_content(model=modelo, contents=conteudo, config=configuracao)
                    return json.loads(resposta.text or ""), modelo
                except json.JSONDecodeError:
                    falhas.append(f"{modelo}: JSON incompleto")
                except Exception as erro:
                    falhas.append(f"{modelo}: {_sem_chave(erro)[:160]}")
                    if "503" in str(erro) or "UNAVAILABLE" in str(erro):
                        with _trava:
                            _sobrecarregado_ate[modelo] = time.time() + PAUSA_MODELO_SOBRECARREGADO_S
                        break
                    if "429" in str(erro) or "RESOURCE_EXHAUSTED" in str(erro):
                        # Cota do plano gratuito estourada para este modelo: tenta os outros e volta nele depois.
                        with _trava:
                            _sobrecarregado_ate[modelo] = time.time() + PAUSA_MODELO_SEM_COTA_S
                        break
                    if "404" in str(erro) or "NOT_FOUND" in str(erro):
                        with _trava:
                            _sobrecarregado_ate[modelo] = time.time() + 86400 * 30
                        break
                    if not _passageiro(erro):
                        break
                if tentativa + 1 < tentativas_por_modelo:
                    time.sleep(espera_s * (tentativa + 1))
    raise GeminiErro("nenhum modelo do Gemini respondeu. " + " | ".join(falhas[-4:]))
