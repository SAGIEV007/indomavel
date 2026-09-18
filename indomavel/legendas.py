"""Legenda automática do YouTube: a mesma matéria-prima que o Chub usa.

Conferido em 15/09/2026 no Anúncio Especial: o texto das frases do Chub é a
legenda automática do YouTube (pt-orig), com os mesmos tempos (99,8% das palavras
iguais). O arquivo json3 ainda traz o tempo de cada palavra, que o Chub não expõe.
"""

import glob
import json
import os
import re
from datetime import datetime, timezone

import yt_dlp

from . import config

IDIOMAS = ["pt-orig", "pt", "pt-BR"]
# O Chub abre frase nova depois de pontuação final, na troca de locutor e em pausas acima de 1 s.
PAUSA_QUEBRA_S = 1.0
FIM_DE_FRASE = (".", "?", "!", "…")
MARCACAO = re.compile(r"^\[[^\]]*\]$")
ID_YOUTUBE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LINK_YOUTUBE = re.compile(r"(?:[?&]v=|/shorts/|/live/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")


def id_do_link(texto):
    """Id do vídeo a partir de um link do YouTube (ou do próprio id); None se não reconhecer."""
    texto = (texto or "").strip()
    if ID_YOUTUBE.match(texto):
        return texto
    achado = LINK_YOUTUBE.search(texto)
    return achado.group(1) if achado else None


def opcoes_ytdlp():
    opcoes = {"quiet": True, "no_warnings": True, "noprogress": True}
    if config.NODE:
        opcoes["js_runtimes"] = {"node": {"path": config.NODE}}
    if config.FFMPEG:
        opcoes["ffmpeg_location"] = config.FFMPEG
    candidatos_cookies = [
        config.valor("COOKIES_YOUTUBE"),
        os.path.join(config.RAIZ, "cookies.txt"),
        os.path.join(config.PASTA_DADOS, "cookies.txt"),
    ]
    for c in candidatos_cookies:
        if c and os.path.isfile(c):
            opcoes["cookiefile"] = c
            break
    return opcoes


def _data_publicacao(info):
    carimbo = info.get("release_timestamp") or info.get("timestamp")
    if carimbo:
        return datetime.fromtimestamp(carimbo, tz=timezone.utc).isoformat()
    dia = info.get("upload_date")
    return f"{dia[:4]}-{dia[4:6]}-{dia[6:]}T00:00:00+00:00" if dia else None


def resumo_info(info):
    return {
        "youtube_id": info.get("id"),
        "titulo": info.get("title") or "",
        "canal": info.get("channel") or info.get("uploader") or "",
        "duracao_s": int(info.get("duration") or 0),
        "publicado_em": _data_publicacao(info),
        "descricao": (info.get("description") or "")[:1500],
        "ao_vivo": info.get("live_status"),
    }


def baixar_legenda(youtube_id, pasta):
    """Metadados do vídeo e o caminho do json3 da legenda automática (None se o YouTube não tiver)."""
    os.makedirs(pasta, exist_ok=True)
    for antigo in glob.glob(os.path.join(pasta, "legenda.*.json3")):
        os.remove(antigo)
    opcoes = opcoes_ytdlp()
    opcoes.update({
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": IDIOMAS,
        "subtitlesformat": "json3",
        "outtmpl": os.path.join(pasta, "legenda.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=True)
    for idioma in IDIOMAS:
        candidato = os.path.join(pasta, f"legenda.{idioma}.json3")
        if os.path.exists(candidato):
            return resumo_info(info), candidato
    return resumo_info(info), None


def palavras_do_json3(caminho):
    """Palavras com o instante em que começam e a marca de troca de locutor."""
    with open(caminho, encoding="utf-8") as arquivo:
        eventos = json.load(arquivo).get("events", [])
    palavras = []
    for evento in eventos:
        for seg in evento.get("segs") or []:
            texto = seg.get("utf8", "")
            if not texto.strip():
                continue
            inicio = (evento.get("tStartMs", 0) + seg.get("tOffsetMs", 0)) / 1000
            troca = bool(seg.get("isSpeakerChange"))
            for token in texto.split():
                if token == ">>":
                    troca = True
                    continue
                palavras.append({"t": round(inicio, 3), "p": token, "troca": troca})
                troca = False
    return palavras


def frases_das_palavras(palavras, pausa_quebra=PAUSA_QUEBRA_S):
    """Frases no formato do Chub: {i, inicio, fim, texto, troca, conferir}."""
    frases, atual = [], []

    def fechar():
        if atual:
            frases.append({"inicio": atual[0]["t"], "texto": " ".join(p["p"] for p in atual), "troca": atual[0]["troca"]})
            atual.clear()

    anterior = None
    for palavra in palavras:
        if MARCACAO.match(palavra["p"]):
            continue
        if atual and (palavra["troca"] or (anterior is not None and palavra["t"] - anterior > pausa_quebra)):
            fechar()
        atual.append(palavra)
        anterior = palavra["t"]
        if palavra["p"].endswith(FIM_DE_FRASE):
            fechar()
    fechar()
    for indice, frase in enumerate(frases):
        frase["i"] = indice
        frase["fim"] = frases[indice + 1]["inicio"] if indice + 1 < len(frases) else round(frase["inicio"] + 2.0, 3)
        frase["conferir"] = False
    return frases
