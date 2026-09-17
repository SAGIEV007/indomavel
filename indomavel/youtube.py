"""Downloads do YouTube: só o trecho escolhido (editor) ou o vídeo inteiro na maior qualidade, uma vez só."""

import glob
import json
import logging
import os
import re
import subprocess
import tempfile
import threading

log = logging.getLogger("indomavel.youtube")

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
    caminho = os.path.join(pasta, base + ".mp4")
    caminho_bruto = video_em_cache(youtube_id)
    if caminho_bruto:
        duracao = max(0.1, float(fim) - float(inicio))
        cmd = [
            config.FFMPEG or "ffmpeg", "-y", "-ss", f"{float(inicio):.3f}", "-i", caminho_bruto,
            "-t", f"{duracao:.3f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-movflags", "+faststart", caminho
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and os.path.exists(caminho) and os.path.getsize(caminho) > 0:
                return caminho
        except Exception:
            pass
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
    # Primeiro a faixa no idioma original: o YouTube oferece dublagens automáticas (inglês, espanhol) com a mesma
    # taxa da original, e em 16/09/2026 o programa gravou cortes em inglês. O yt-dlp marca a original com
    # language_preference 10 e as dublagens com -1. Depois a nota de qualidade do yt-dlp, que põe a versão "DRC"
    # (volume comprimido) abaixo da normal; depois áudio AAC (m4a), que junta num MP4 sem conversão; por fim a taxa.
    audio = max(audios, key=lambda f: (f.get("language_preference") or 0, f.get("quality") or 0,
                                       (f.get("acodec") or "").startswith("mp4a"), f.get("abr") or 0))
    return video["format_id"], audio["format_id"]


def caminho_do_video(youtube_id):
    return os.path.join(config.PASTA_VIDEOS, f"{youtube_id}.mp4")


def _caminho_ficha(youtube_id):
    return os.path.join(config.PASTA_VIDEOS, f"{youtube_id}.json")


def video_em_cache(youtube_id):
    """Caminho do vídeo inteiro já baixado com o áudio no idioma original, ou None.

    Só vale com a ficha <id>.json ao lado. Vídeos baixados antes de 16/09/2026 não têm ficha e podem estar com a
    dublagem automática: voltam a valer quando baixar_video_maximo troca o áudio deles.
    """
    caminho = caminho_do_video(youtube_id)
    try:
        with open(_caminho_ficha(youtube_id), encoding="utf-8") as arquivo:
            ficha = json.load(arquivo)
    except (OSError, ValueError):
        return None
    return caminho if ficha.get("audio_original") and os.path.exists(caminho) else None


def remover_video_cache(youtube_id):
    """Remove o vídeo bruto baixado de PASTA_VIDEOS/<id>.mp4 e sua ficha para liberar espaço em disco."""
    caminho = caminho_do_video(youtube_id)
    ficha = _caminho_ficha(youtube_id)
    bytes_liberados = 0
    with _trava_geral:
        trava = _travas_de_video.setdefault(youtube_id, threading.Lock())
    with trava:
        for arq in (caminho, ficha):
            if os.path.exists(arq):
                try:
                    bytes_liberados += os.path.getsize(arq)
                    os.remove(arq)
                    log.info("Arquivo de cache removido: %s", arq)
                except Exception as e:
                    log.warning("Falha ao remover arquivo de cache %s: %s", arq, e)
    return bytes_liberados


def _trocar_audio(caminho, endereco, formato_audio, silencioso, ao_progredir):
    """Troca a faixa de áudio do vídeo guardado pela faixa formato_audio do YouTube, sem recomprimir a imagem."""
    with tempfile.TemporaryDirectory(prefix="troca_audio_", dir=os.path.dirname(caminho)) as temporaria:
        opcoes = _opcoes(os.path.join(temporaria, "audio.%(ext)s"), silencioso, ao_progredir)
        opcoes["format"] = formato_audio
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            ydl.download([endereco])
        baixados = glob.glob(os.path.join(temporaria, "audio.*"))
        if not baixados:
            raise RuntimeError("o yt-dlp terminou sem baixar o áudio original")
        novo = os.path.join(temporaria, "video.mp4")
        resultado = subprocess.run(
            [config.FFMPEG or "ffmpeg", "-y", "-v", "error", "-i", caminho, "-i", baixados[0],
             "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", novo],
            capture_output=True, text=True,
        )
        if resultado.returncode != 0 or not os.path.exists(novo):
            raise RuntimeError("o ffmpeg não conseguiu trocar o áudio: " + resultado.stderr[-400:])
        os.replace(novo, caminho)


def baixar_video_maximo(youtube_id, ao_progredir=None, silencioso=True):
    """Vídeo inteiro na maior qualidade que o YouTube oferece, em PASTA_VIDEOS/<id>.mp4, baixado uma vez só.

    O yt-dlp só cria <id>.mp4 no fim (antes são arquivos .part), então um arquivo com esse nome está completo.
    A ficha <id>.json diz qual faixa de áudio foi gravada; sem ela, o vídeo é de antes da correção do idioma e só
    o áudio é baixado de novo.
    """
    caminho = caminho_do_video(youtube_id)
    with _trava_geral:
        trava = _travas_de_video.setdefault(youtube_id, threading.Lock())
    with trava:
        if video_em_cache(youtube_id):
            return caminho
        os.makedirs(config.PASTA_VIDEOS, exist_ok=True)
        endereco = f"https://www.youtube.com/watch?v={youtube_id}"
        opcoes = _opcoes(os.path.join(config.PASTA_VIDEOS, f"{youtube_id}.%(ext)s"), silencioso, ao_progredir)
        with yt_dlp.YoutubeDL({**opcoes, "skip_download": True}) as ydl:
            info = ydl.extract_info(endereco, download=False)
        formatos = info.get("formats") or []
        video, audio = escolher_formatos(formatos)
        if os.path.exists(caminho):
            if audio:
                _trocar_audio(caminho, endereco, audio, silencioso, ao_progredir)
        else:
            opcoes.update({"format": f"{video}+{audio}" if audio else video, "overwrites": False})
            with yt_dlp.YoutubeDL(opcoes) as ydl:
                ydl.download([endereco])
            if not os.path.exists(caminho):
                raise RuntimeError("o yt-dlp terminou sem gerar o arquivo " + caminho)
        faixa = next((f for f in formatos if f.get("format_id") == audio), {})
        with open(_caminho_ficha(youtube_id), "w", encoding="utf-8") as arquivo:
            json.dump({"audio": audio, "idioma_audio": faixa.get("language"), "descricao_audio": faixa.get("format_note"),
                       "audio_original": True}, arquivo, ensure_ascii=False)
    return caminho
