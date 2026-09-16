"""Sugestões de etiqueta e headline para o card do corte, no padrão dos editores da Tropa (pasta do Drive).

A headline gira em torno do tema do bloco: o pedido leva a categoria, os temas e os momentos
fortes que o Chub marcou, além do texto do corte.
"""

import hashlib
import json
import os

from . import config, gemini

# Tirados dos cortes aprovados e publicados na pasta do Drive dos editores (conferidos em 15/09/2026).
EXEMPLOS_DRIVE = [
    ("MANDOU A REAL!!", "Vídeo de Renan Santos viralizou, mostrando que o povo brasileiro parou de ligar para corrupção"),
    ("VIRALIZOU!!", "Renan Santos comenta problemas que encontrou ao visitar o estado do Maranhão"),
    ("EM ALTA!!!", "Porque a Rede Record de Televisão não quer Renan Santos em debates ou entrevistas? será que eles estão com medo de algo??"),
    ("EM ALTA!", "Em discurso, Renan Santos coloca Tóffoli e Moraes na lista negra do impeachment"),
    ("VERGONHOSO!", "Renan Santos detona Flávio Bolsonaro, Lula e Rede Record."),
]

INSTRUCAO = """Você escreve o card de topo de cortes do Renan Santos (Partido Missão) para Instagram, TikTok e Reels, no padrão dos editores da Tropa do Renan.

O card tem duas partes:
- etiqueta: 1 a 3 palavras em CAIXA ALTA terminando com exclamação (ex.: EM ALTA!, VIRALIZOU!!, MANDOU A REAL!!, VERGONHOSO!).
- headline: uma frase de até 110 caracteres, em terceira pessoa, que diz o que o Renan faz ou diz no corte (ex.: "Renan Santos detona..."), no tom dos exemplos.

Regras:
- Construa a headline em torno do tema principal do bloco: use a categoria, os temas e os momentos fortes informados. Quem lê o card precisa saber de que assunto o corte trata.
- Fiel ao trecho: não invente fatos, números, nomes ou citações que não estejam na transcrição.
- Citação entre aspas só com palavras que aparecem na transcrição.
- Sem hashtags e sem emojis.
- Devolva 3 opções com ângulos diferentes: "noticioso", "confronto" e "citacao".

Exemplos reais do padrão:
""" + "\n".join(f"- {tag} | {headline}" for tag, headline in EXEMPLOS_DRIVE)

ESQUEMA = {
    "type": "object",
    "properties": {
        "opcoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "angulo": {"type": "string", "enum": ["noticioso", "confronto", "citacao"]},
                    "tag": {"type": "string"},
                    "headline": {"type": "string"},
                },
                "required": ["angulo", "tag", "headline"],
            },
        }
    },
    "required": ["opcoes"],
}


def sugerir(titulo, resumo, texto_trecho, categoria="", temas=(), destaques=(), gerar=None):
    """Três opções {angulo, tag, headline}. Guarda em dados/headlines para não gastar o Gemini de novo."""
    conteudo = (
        f"CATEGORIA DO BLOCO: {categoria}\n"
        f"TEMAS DO BLOCO: {', '.join(temas)}\n"
        f"TÍTULO DO BLOCO: {titulo}\n"
        f"RESUMO DO BLOCO: {resumo}\n"
        f"MOMENTOS FORTES: {' | '.join(destaques)}\n\n"
        f"TRANSCRIÇÃO DO CORTE:\n{texto_trecho[:6000]}"
    )
    chave = hashlib.sha1(conteudo.encode("utf-8")).hexdigest()
    caminho = os.path.join(config.PASTA_DADOS, "headlines", chave + ".json")
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as arquivo:
            return json.load(arquivo)
    if gerar is None:
        # Pedido da tela: uma tentativa por modelo e sem espera, para a resposta (ou o erro) chegar rápido.
        def gerar(instrucao, texto, esquema):
            return gemini.gerar_json(instrucao, texto, esquema, tentativas_por_modelo=1, espera_s=0)
    dados, modelo = gerar(INSTRUCAO, conteudo, ESQUEMA)
    opcoes = [
        {"angulo": o.get("angulo", ""), "tag": (o.get("tag") or "").strip().upper()[:30], "headline": (o.get("headline") or "").strip()[:160]}
        for o in dados.get("opcoes") or [] if o.get("headline")
    ][:3]
    resultado = {"opcoes": opcoes, "modelo": modelo, "categoria": categoria, "temas": list(temas)}
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(resultado, arquivo, ensure_ascii=False)
    return resultado
