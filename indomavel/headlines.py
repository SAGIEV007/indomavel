"""Sugestões de etiqueta e headline para o card do corte, no padrão dos editores da Tropa (pasta do Drive).

A headline gira em torno do tema do bloco: o pedido leva a categoria, os temas e os momentos
fortes que o Chub marcou, além do texto do corte.
"""

import hashlib
import json
import os
import re
import threading
import time

from . import config, gemini

# Exemplos padrão caso o banco de treinamento ainda não esteja carregado
EXEMPLOS_DRIVE = [
    ("MANDOU A REAL!!", "Vídeo de Renan Santos viralizou, mostrando que o povo brasileiro parou de ligar para corrupção"),
    ("VIRALIZOU!!", "Renan Santos comenta problemas que encontrou ao visitar o estado do Maranhão"),
    ("EM ALTA!!!", "Porque a Rede Record de Televisão não quer Renan Santos em debates ou entrevistas? será que eles estão com medo de algo??"),
    ("EM ALTA!", "Em discurso, Renan Santos coloca Tóffoli e Moraes na lista negra do impeachment"),
    ("VERGONHOSO!", "Renan Santos detona Flávio Bolsonaro, Lula e Rede Record."),
]

_TRAVA_VEREDITOS = threading.Lock()
_BANCO_CACHE = None


def carregar_banco_treinamento():
    """Carrega o banco de treinamento de 50 headlines com cache em memória."""
    global _BANCO_CACHE
    if _BANCO_CACHE is not None:
        return _BANCO_CACHE
    caminho = os.path.join(config.PASTA_DADOS, "headlines_banco_treinamento.json")
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                dados = json.load(f)
                _BANCO_CACHE = dados.get("exemplos") or []
                return _BANCO_CACHE
        except Exception:
            pass
    _BANCO_CACHE = [{"tag": t, "headline": h, "arquetipo": "geral", "categoria": "geral", "angulo": "noticioso"} for t, h in EXEMPLOS_DRIVE]
    return _BANCO_CACHE


def selecionar_exemplos_dinamicos(categoria="", temas=(), texto="", limite=5):
    """Seleciona os melhores exemplos do banco de treinamento combinando por tema, categoria ou diversidade de ângulos."""
    banco = carregar_banco_treinamento()
    if not banco:
        return EXEMPLOS_DRIVE

    texto_busca = f"{categoria} {' '.join(temas)} {texto[:400]}".lower()

    def score(exemplo):
        pts = 0
        cat_ex = (exemplo.get("categoria") or "").lower()
        if cat_ex and cat_ex in texto_busca:
            pts += 3
        for kw in exemplo.get("palavras_chave") or []:
            if kw.lower() in texto_busca:
                pts += 2
        return pts

    ordenados = sorted(banco, key=score, reverse=True)

    # Garante diversidade de ângulos (noticioso, confronto, citacao)
    selecionados = []
    angulos_vistos = set()
    for item in ordenados:
        ang = item.get("angulo", "noticioso")
        if ang not in angulos_vistos:
            selecionados.append((item["tag"], item["headline"]))
            angulos_vistos.add(ang)
        if len(selecionados) >= 3:
            break

    # Completa até o limite
    for item in ordenados:
        par = (item["tag"], item["headline"])
        if par not in selecionados:
            selecionados.append(par)
        if len(selecionados) >= limite:
            break

    return selecionados or EXEMPLOS_DRIVE


def montar_instrucao(categoria="", temas=(), texto="", renan_falando=None, locutor=""):
    """Gera a instrução do Gemini injetando os melhores exemplos Few-Shot e respeitando o locutor real."""
    if renan_falando is None:
        texto_busca = f"{categoria} {' '.join(temas)} {texto}".lower()
        renan_falando = "renan" in texto_busca

    exemplos = selecionar_exemplos_dinamicos(categoria, temas, texto)
    exemplos_txt = "\n".join(f"- {tag} | {headline}" for tag, headline in exemplos)

    if renan_falando:
        intro = "Você escreve o card de topo de cortes do Renan Santos para Instagram, TikTok e Reels, no padrão viral dos editores."
        regra_locutor = "O locutor principal é Renan Santos (Partido Missão). Você pode atribuir as falas ou ações diretamente a Renan Santos (ex.: 'Renan Santos detona...', 'Em discurso, Renan Santos...')."
    elif locutor:
        intro = "Você escreve o card de topo de cortes para Instagram, TikTok e Reels, no padrão viral dos editores."
        regra_locutor = f"O locutor principal é {locutor}. NÃO atribua a fala a Renan Santos! Use {locutor} ou foque no assunto/frase de impacto."
    else:
        intro = "Você escreve o card de topo de cortes para Instagram, TikTok e Reels, no padrão viral dos editores."
        regra_locutor = "ATENÇÃO: O locutor deste trecho NÃO é o Renan Santos (ou não há confirmação). É ESTRITAMENTE PROIBIDO usar o nome 'Renan Santos' nas headlines! Crie manchetes sobre o acontecimento, o debate ou use aspas da frase (ex.: 'Debate esquenta ao vivo...', 'Participante confronta...', '“Isso aqui é uma vergonha”, dispara participante ao vivo')."

    return f"""{intro}

O card tem duas partes:
- etiqueta: 1 a 3 palavras em CAIXA ALTA terminando com exclamação (ex.: EM ALTA!, VIRALIZOU!!, MANDOU A REAL!!, VERGONHOSO!, URGENTE!, DEBATE AO VIVO!, DETONOU!!).
- headline: uma frase de até 110 caracteres, em terceira pessoa, magnética e fiel ao conteúdo.

Regra de Locutor:
{regra_locutor}

Arquétipos Virais de Alto Desempenho (Use para maximizar retenção e CTR):
1. Confronto Direto: Embate claro, enfrentamento a autoridades/narrativas (verbos de ação: detona, desmascara, confronta, rebate, expõe, desafia).
2. Curiosidade Irresistível / Gap de Informação: Lacuna psicológica que impele o clique sem sensacionalismo vazio (ex.: "O detalhe na fala que ninguém percebeu...", "A verdade que tentaram esconder sobre...").
3. Alerta / Urgência: Impacto direto e imediato na vida do cidadão (Tags: URGENTE!, ALERTA MÁXIMO!, ATENÇÃO!).
4. Frase de Efeito com Verbo Ativo e Contexto Dramático: Citação contundente combinada ao momento de tensão do debate.

Auto-Avaliação e Ranking de CTR:
Para cada headline gerada, auto-avalie o potencial de retenção dos primeiros 3 segundos atribuindo uma nota 'score_ctr' de 1 a 10 e informe brevemente o 'gatilho' psicológico utilizado (ex.: "gap de curiosidade", "embate direto", "urgência").
Ordene as opções da maior nota para a menor, para que a primeira opção seja sempre a mais irresistível para o corte.

Regras Editoriais:
- Construa a headline em torno do tema principal do bloco: use a categoria, os temas e os momentos fortes informados.
- Fiel ao trecho: não invente fatos, números, nomes ou citações que não estejam na transcrição.
- Citação entre aspas só com palavras que aparecem na transcrição.
- Sem hashtags e sem emojis.
- Devolva entre 3 e 8 opções cobrindo ângulos variados: "confronto", "curiosidade", "alerta", "noticioso", "citacao", "impacto".

Exemplos reais selecionados para este tema:
{exemplos_txt}"""


def registrar_veredito(youtube_id, bloco_id, headline, tag, angulo=None, acao="aprovado", motivo=None):
    """Loop de Active Learning: registra vereditos de editores para fine-tuning contínuo."""
    caminho_dir = os.path.join(config.PASTA_DADOS, "automacao")
    os.makedirs(caminho_dir, exist_ok=True)
    caminho_arquivo = os.path.join(caminho_dir, "vereditos.jsonl")
    registro = {
        "timestamp": time.time(),
        "youtube_id": youtube_id,
        "bloco_id": bloco_id,
        "tag": tag,
        "headline": headline,
        "angulo": angulo,
        "acao": acao,
        "motivo": motivo,
    }
    with _TRAVA_VEREDITOS:
        with open(caminho_arquivo, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    return registro


INSTRUCAO = montar_instrucao()

ESQUEMA = {
    "type": "object",
    "properties": {
        "opcoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "angulo": {"type": "string"},
                    "tag": {"type": "string"},
                    "headline": {"type": "string"},
                    "score_ctr": {"type": "integer"},
                    "gatilho": {"type": "string"},
                },
                "required": ["angulo", "tag", "headline"],
            },
        }
    },
    "required": ["opcoes"],
}


def headlines_heuristicas(titulo, resumo, categoria="", temas=(), destaques=(), renan_falando=None, locutor="", limite=3):
    """Gera opções com ângulos calibrados sem depender de IA e sem forçar o Renan se ele não estiver falando."""
    if renan_falando is None:
        texto_busca = f"{titulo} {resumo} {categoria} {' '.join(temas)} {' '.join(destaques)}".lower()
        renan_falando = "renan" in texto_busca

    destaque = destaques[0] if destaques else ""
    alvo = destaque or titulo or "Bastidores e discussão política na transmissão"
    alvo_limpo = alvo.replace('"', '').replace('\n', ' ').strip()
    if len(alvo_limpo) > 85:
        alvo_limpo = alvo_limpo[:82].rstrip() + "…"

    if renan_falando:
        h_noticioso = f"Em análise, Renan Santos expõe: {alvo_limpo}"[:110]
        h_confronto = f"Renan Santos confronta e dispara: “{alvo_limpo}”"[:110]
        h_citacao = f"“{alvo_limpo}” — Renan Santos em fala contundente"[:110]
        h_curiosidade = f"O detalhe que Renan Santos expôs sobre {alvo_limpo}"[:110]
        h_alerta = f"Alerta de Renan Santos sobre {alvo_limpo}"[:110]
        h_impacto = f"Renan Santos detona e manda recado: “{alvo_limpo}”"[:110]
    elif locutor:
        h_noticioso = f"Em transmissão ao vivo, {locutor} analisa: {alvo_limpo}"[:110]
        h_confronto = f"{locutor} confronta ao vivo: “{alvo_limpo}”"[:110]
        h_citacao = f"“{alvo_limpo}” — {locutor} em debate ao vivo"[:110]
        h_curiosidade = f"O detalhe imperdível que {locutor} expôs ao vivo: {alvo_limpo}"[:110]
        h_alerta = f"Atenção: {locutor} alerta sobre {alvo_limpo}"[:110]
        h_impacto = f"{locutor} dispara ao vivo: “{alvo_limpo}”"[:110]
    else:
        h_noticioso = f"Em debate na transmissão: {alvo_limpo}"[:110]
        h_confronto = f"Discussão esquenta ao vivo: “{alvo_limpo}”"[:110]
        h_citacao = f"“{alvo_limpo}” — Momento marcante da live"[:110]
        h_curiosidade = f"O detalhe inesperado que chamou atenção: {alvo_limpo}"[:110]
        h_alerta = f"Atenção: participante alerta sobre {alvo_limpo}"[:110]
        h_impacto = f"Clima esquenta na transmissão: “{alvo_limpo}”"[:110]

    # Para manter total compatibilidade com testes que exigem os 3 primeiros ângulos (noticioso, confronto, citacao)
    todas = [
        {"angulo": "noticioso", "tag": "EM ALTA!", "headline": h_noticioso, "score_ctr": 8, "gatilho": "relevância noticiosa"},
        {"angulo": "confronto", "tag": "CONFRONTO!", "headline": h_confronto, "score_ctr": 10, "gatilho": "embate direto e conflito"},
        {"angulo": "citacao", "tag": "MANDOU A REAL!!", "headline": h_citacao, "score_ctr": 9, "gatilho": "frase de efeito contundente"},
        {"angulo": "curiosidade", "tag": "INACREDITÁVEL!", "headline": h_curiosidade, "score_ctr": 9, "gatilho": "gap de curiosidade irresistível"},
        {"angulo": "alerta", "tag": "URGENTE!", "headline": h_alerta, "score_ctr": 9, "gatilho": "senso de urgência e risco"},
        {"angulo": "impacto", "tag": "DETONOU!!", "headline": h_impacto, "score_ctr": 10, "gatilho": "reação visceral e quebra de padrão"},
    ]
    return todas[:limite]


def sugerir(titulo, resumo, texto_trecho, categoria="", temas=(), destaques=(),
            renan_falando=None, locutor="", limite_sugestoes=3, gerar=None):
    """Gera de 3 a 8 opções {angulo, tag, headline, score_ctr, gatilho}. Guarda em dados/headlines para economizar cotas."""
    if not (texto_trecho or "").strip():
        return {
            "opcoes": [{"angulo": "neutro", "tag": "TRANSMISSÃO", "headline": "Trecho sem falas identificadas na live", "score_ctr": 5, "gatilho": "neutro"}],
            "modelo": "vazio", "categoria": categoria, "temas": list(temas),
        }

    if renan_falando is None:
        texto_busca = f"{titulo} {resumo} {categoria} {' '.join(temas)} {texto_trecho[:400]}".lower()
        renan_falando = "renan" in texto_busca

    conteudo = (
        f"LOCUTOR CONFIRMADO RENAN: {'SIM' if renan_falando else 'NÃO'}\n"
        f"LOCUTOR IDENTIFICADO: {locutor or 'Não especificado / Outro participante'}\n"
        f"CATEGORIA DO BLOCO: {categoria}\n"
        f"TEMAS DO BLOCO: {', '.join(temas)}\n"
        f"TÍTULO DO BLOCO: {titulo}\n"
        f"RESUMO DO BLOCO: {resumo}\n"
        f"MOMENTOS FORTES: {' | '.join(destaques)}\n\n"
        f"TRANSCRIÇÃO DO CORTE:\n{texto_trecho[:6000]}"
    )
    chave = hashlib.sha1(f"{conteudo}_renan={renan_falando}_limite={limite_sugestoes}_v2".encode("utf-8")).hexdigest()
    caminho = os.path.join(config.PASTA_DADOS, "headlines", chave + ".json")
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as arquivo:
            return json.load(arquivo)
    if gerar is None:
        # Pedido da tela: uma tentativa por modelo e sem espera, para a resposta chegar rápido.
        def gerar(instrucao, texto, esquema):
            return gemini.gerar_json(instrucao, texto, esquema, tentativas_por_modelo=1, espera_s=0)
    try:
        instrucao = montar_instrucao(categoria, temas, texto_trecho, renan_falando=renan_falando, locutor=locutor)
        dados, modelo = gerar(instrucao, conteudo, ESQUEMA)
        opcoes = [
            {
                "angulo": o.get("angulo", "noticioso"),
                "tag": (o.get("tag") or "").strip().upper()[:30],
                "headline": (o.get("headline") or "").strip()[:160],
                "score_ctr": int(o.get("score_ctr") or 8),
                "gatilho": (o.get("gatilho") or "").strip()[:80],
            }
            for o in dados.get("opcoes") or [] if o.get("headline")
        ]
        # Auto-avaliação e ranking: ordena pelo score de CTR decrescente
        opcoes.sort(key=lambda x: x.get("score_ctr", 0), reverse=True)
        opcoes = opcoes[:limite_sugestoes]
    except Exception:
        opcoes = headlines_heuristicas(titulo, resumo, categoria, temas, destaques, renan_falando=renan_falando, locutor=locutor, limite=limite_sugestoes)
        modelo = "heuristicas_locais"
    if not opcoes:
        opcoes = headlines_heuristicas(titulo, resumo, categoria, temas, destaques, renan_falando=renan_falando, locutor=locutor, limite=limite_sugestoes)
        modelo = modelo or "heuristicas_locais"

    # Higienização estrita de locutor: se renan_falando for False, NUNCA permite "Renan Santos" nas headlines
    if not renan_falando:
        padrao_renan = re.compile(r"\brenan(\s+santos)?\b", re.IGNORECASE)
        for item in opcoes:
            if padrao_renan.search(item["headline"]):
                substituto = locutor if locutor else "Participante"
                item["headline"] = padrao_renan.sub(substituto, item["headline"])
            if padrao_renan.search(item["tag"]):
                item["tag"] = "EM ALTA!"

    resultado = {"opcoes": opcoes, "modelo": modelo, "categoria": categoria, "temas": list(temas)}
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(resultado, arquivo, ensure_ascii=False)
    return resultado
