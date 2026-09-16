"""Legenda .srt do trecho baixado, feita com as frases do Chub.

As frases do Chub têm tempo por frase, não por palavra. Para a legenda ficar numa
linha só, cada frase é dividida em pedaços curtos e o tempo da frase é repartido
pelo tamanho de cada pedaço. A tela usa a mesma regra para mostrar a legenda por
cima do vídeo.
"""

import re

MAX_CARACTERES = 32
DURACAO_MINIMA_S = 0.25

# Estilo dos cortes da Tropa (Drive): 2 ou 3 palavras por vez, uma delas em destaque.
MAX_PALAVRAS_TRECHO = 3
MAX_CARACTERES_TRECHO = 18
PAUSA_TRECHO_S = 0.7
DURACAO_FINAL_PALAVRA_S = 0.6
PALAVRAS_FRACAS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "na", "no", "nas", "nos",
    "e", "é", "ou", "que", "se", "por", "pra", "para", "com", "sem", "ao", "aos", "à", "às", "eu", "ele", "ela", "eles",
    "elas", "você", "vocês", "nós", "isso", "isto", "esse", "essa", "este", "esta", "aquele", "aquela", "mas", "mais",
    "muito", "tá", "né", "aí", "lá", "já", "não", "sim", "tipo", "assim", "então", "quando", "como", "onde", "também",
    "meu", "minha", "seu", "sua", "gente", "cara", "vai", "vou", "foi", "ser", "ter", "tem", "está", "estão", "era",
}
_PONTUACAO = re.compile(r"^[\"'“”(\[]+|[\"'“”)\].,;:!?…]+$")


def limpar_palavra(palavra):
    return _PONTUACAO.sub("", palavra)


def _destaque(palavras):
    """Índice da palavra mais forte do trecho (a mais longa que não é palavra de ligação), ou None."""
    candidatas = [(len(limpar_palavra(p)), i) for i, p in enumerate(palavras)
                  if len(limpar_palavra(p)) >= 4 and limpar_palavra(p).lower() not in PALAVRAS_FRACAS]
    return max(candidatas)[1] if candidatas else None


def trechos_de_palavras(palavras, inicio, fim, max_palavras=MAX_PALAVRAS_TRECHO, max_caracteres=MAX_CARACTERES_TRECHO):
    """Legenda palavra por palavra do corte [inicio, fim], em pedaços curtos com tempos absolutos."""
    selecionadas = [p for p in palavras if inicio - 0.05 <= p["t"] < fim and not re.fullmatch(r"\[[^\]]*\]", p["p"])]
    grupos, atual = [], []
    for palavra in selecionadas:
        if atual and (
            len(atual) >= max_palavras
            or len(" ".join(limpar_palavra(p["p"]) for p in atual + [palavra])) > max_caracteres
            or palavra.get("troca")
            or palavra["t"] - atual[-1]["t"] > PAUSA_TRECHO_S
        ):
            grupos.append(atual)
            atual = []
        atual.append(palavra)
        if palavra["p"].endswith((".", "?", "!", ",", "…")):
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)

    trechos = []
    for indice, grupo in enumerate(grupos):
        comeco = max(grupo[0]["t"], inicio)
        proximo = grupos[indice + 1][0]["t"] if indice + 1 < len(grupos) else fim
        fim_natural = grupo[-1]["t"] + DURACAO_FINAL_PALAVRA_S
        final = min(proximo, fim, max(fim_natural, comeco + 0.3))
        textos = [p["p"] for p in grupo]
        trechos.append({
            "inicio": round(comeco, 3),
            "fim": round(max(final, comeco + 0.05), 3),
            "palavras": textos,
            "destaque": _destaque(textos),
        })
    return trechos


def pedacos(texto, max_caracteres=MAX_CARACTERES):
    atual, saida = [], []
    for palavra in texto.split():
        if atual and len(" ".join(atual + [palavra])) > max_caracteres:
            saida.append(" ".join(atual))
            atual = [palavra]
        else:
            atual.append(palavra)
    if atual:
        saida.append(" ".join(atual))
    return saida


def deixas(frases, inicio, fim, max_caracteres=MAX_CARACTERES):
    """Deixas (início, fim, texto) com tempos relativos ao começo do trecho [inicio, fim]."""
    resultado = []
    for frase in frases:
        if frase["fim"] <= inicio or frase["inicio"] >= fim:
            continue
        partes = pedacos(frase["texto"], max_caracteres)
        total = sum(len(parte) for parte in partes) or 1
        duracao = frase["fim"] - frase["inicio"]
        cursor = frase["inicio"]
        for parte in partes:
            fatia = duracao * len(parte) / total
            a, b = max(cursor, inicio), min(cursor + fatia, fim)
            cursor += fatia
            if b - a >= DURACAO_MINIMA_S:
                resultado.append((round(a - inicio, 3), round(b - inicio, 3), parte))
    return resultado


def srt_de_trechos(trechos, inicio, fim, maiusculas=False):
    """.srt com os mesmos trechos curtos do vídeo exportado, para importar no CapCut e corrigir lá."""
    blocos = []
    for trecho in trechos:
        a = max(trecho["inicio"], inicio) - inicio
        b = min(trecho["fim"], fim) - inicio
        if b - a < 0.05:
            continue
        texto = " ".join(p for p in (limpar_palavra(palavra) for palavra in trecho["palavras"]) if p)
        if maiusculas:
            texto = texto.upper()
        if texto:
            blocos.append(f"{len(blocos) + 1}\n{_tempo(a)} --> {_tempo(b)}\n{texto}\n")
    return "\n".join(blocos)


def _tempo(segundos):
    ms = int(round(segundos * 1000))
    horas, ms = divmod(ms, 3_600_000)
    minutos, ms = divmod(ms, 60_000)
    segundos, ms = divmod(ms, 1000)
    return f"{horas:02d}:{minutos:02d}:{segundos:02d},{ms:03d}"


def srt(frases, inicio, fim, max_caracteres=MAX_CARACTERES):
    blocos = [
        f"{numero}\n{_tempo(a)} --> {_tempo(b)}\n{texto}\n"
        for numero, (a, b, texto) in enumerate(deixas(frases, inicio, fim, max_caracteres), start=1)
    ]
    return "\n".join(blocos)
