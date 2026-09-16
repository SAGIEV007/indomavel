"""Downloads do YouTube: só o trecho escolhido (editor) ou o vídeo inteiro na maior qualidade, uma vez só."""

import os
import re
import threading

import yt_dlp
from yt_dlp.utils import download_range_func

from . import config

FORMATO = "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
# Na mesma resolução, H.264 primeiro: é o que qualquer editor (CapCut, Premiere) abre sem sofrer.
ORDEM_CODECS = ("avc1", "vp09", "vp9", "av01")

_travas_de_video = {}
_trava_geral = threading.Lock()


def nome_seguro(texto, limite=70):
    texto = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", texto or "")
    texto = re.sub(r"\s+", " ", texto).strip().rstrip(".")
    return texto[:limite].strip() or "trecho"


def marca_tempo(segundos):
    total = int(segundos)
    return f"{total // 3600:d}h{total % 3600 // 60:02d}m{total % 60:02d}s"


def _opcoes(modelo_saida, silencioso, ao_progredir):
    opcoes = {
        "merge_output_format": "mp4",
        "outtmpl": modelo_saida,
        "overwrites": True,
        "quiet": silencioso,
        "no_warnings": silencioso,
        "noprogress": True,
    }
    if config.FFMPEG:
        opcoes["ffmpeg_location"] = config.FFMPEG
    if config.NODE:
        opcoes["js_runtimes"] = {"node": {"path": config.NODE}}
    if ao_progredir:
        opcoes["progress_hooks"] = [ao_progredir]
    return opcoes


def baixar_trecho(youtube_id, inicio, fim, pasta, titulo="", ao_progredir=None, silencioso=True):
    """Baixa [inicio, fim] em MP4 (até 1080p) e devolve o caminho do arquivo."""
    os.makedirs(pasta, exist_ok=True)
    base = f"{nome_seguro(titulo or youtube_id)} - {marca_tempo(inicio)} a {marca_tempo(fim)}"
    opcoes = _opcoes(os.path.join(pasta, base + ".%(ext)s"), silencioso, ao_progredir)
    opcoes.update({
        "format": FORMATO,
        "download_ranges": download_range_func(None, [(float(inicio), float(fim))]),
        "force_keyframes_at_cuts": True,
    })
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={youtube_id}"])
    caminho = os.path.join(pasta, base + ".mp4")
    if not os.path.exists(caminho):
        raise RuntimeError("o yt-dlp terminou sem gerar o arquivo " + caminho)
    return caminho


def escolher_formatos(formatos):
    """(id do vídeo, id do áudio) de maior qualidade: a maior resolução; nela, H.264 antes de VP9 e AV1."""
    videos = [f for f in formatos if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none") and f.get("height")]
    audios = [f for f in formatos if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    if not videos:
        raise RuntimeError("o YouTube não ofereceu nenhum formato de vídeo")

    def ordem_codec(formato):
        codec = (formato.get("vcodec") or "").split(".")[0]
        return ORDEM_CODECS.index(codec) if codec in ORDEM_CODECS else len(ORDEM_CODECS)

    maior = max(f["height"] for f in videos)
    video = min((f for f in videos if f["height"] == maior),
                key=lambda f: (ordem_codec(f), -(f.get("fps") or 0), -(f.get("tbr") or 0)))
    if not audios:
        return video["format_id"], None
    # Áudio AAC (m4a) junta num MP4 sem conversão; entre iguais, a maior taxa.
    audio = max(audios, key=lambda f: ((f.get("acodec") or "").startswith("mp4a"), f.get("abr") or 0))
    return video["format_id"], audio["format_id"]


def caminho_do_video(youtube_id):
    return os.path.join(config.PASTA_VIDEOS, f"{youtube_id}.mp4")


def baixar_video_maximo(youtube_id, ao_progredir=None, silencioso=True):
    """Vídeo inteiro na maior qualidade que o YouTube oferece, em PASTA_VIDEOS/<id>.mp4, baixado uma vez só.

    O yt-dlp só cria <id>.mp4 no fim (antes são arquivos .part), então um arquivo com esse nome está completo.
    """
    caminho = caminho_do_video(youtube_id)
    with _trava_geral:
        trava = _travas_de_video.setdefault(youtube_id, threading.Lock())
    with trava:
        if os.path.exists(caminho):
            return caminho
        os.makedirs(config.PASTA_VIDEOS, exist_ok=True)
        endereco = f"https://www.youtube.com/watch?v={youtube_id}"
        opcoes = _opcoes(os.path.join(config.PASTA_VIDEOS, f"{youtube_id}.%(ext)s"), silencioso, ao_progredir)
        with yt_dlp.YoutubeDL({**opcoes, "skip_download": True}) as ydl:
            info = ydl.extract_info(endereco, download=False)
        video, audio = escolher_formatos(info.get("formats") or [])
        opcoes.update({"format": f"{video}+{audio}" if audio else video, "overwrites": False})
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            ydl.download([endereco])
        if not os.path.exists(caminho):
            raise RuntimeError("o yt-dlp terminou sem gerar o arquivo " + caminho)
    return caminho
