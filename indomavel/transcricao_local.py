"""Transcrição no próprio computador (faster-whisper), para vídeos que ainda não têm legenda no YouTube.

Roda na CPU: a GTX 1050 deste notebook não carrega o CUDA do faster-whisper
(falta cublas64_12.dll). Um vídeo de 1 hora leva bastante tempo; por isso isto é
só o plano B, depois da legenda automática do YouTube.
"""

import os

import yt_dlp

from . import config
from .legendas import opcoes_ytdlp


def baixar_audio(youtube_id, pasta):
    os.makedirs(pasta, exist_ok=True)
    opcoes = opcoes_ytdlp()
    opcoes.update({
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": os.path.join(pasta, "audio.%(ext)s"),
        "overwrites": True,
    })
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=True)
        caminho = ydl.prepare_filename(info)
    if not os.path.exists(caminho):
        raise RuntimeError("o yt-dlp terminou sem gerar o áudio " + caminho)
    return caminho


def transcrever(caminho_audio, ao_progredir=None, modelo=None):
    """Palavras {t, p, troca} do áudio, no mesmo formato de legendas.palavras_do_json3."""
    from faster_whisper import WhisperModel

    whisper = WhisperModel(modelo or config.WHISPER_MODELO, device="cpu", compute_type="int8", local_files_only=True)
    segmentos, info = whisper.transcribe(caminho_audio, language="pt", word_timestamps=True, vad_filter=True, beam_size=1)
    palavras = []
    for segmento in segmentos:
        for palavra in segmento.words or []:
            texto = palavra.word.strip()
            if texto:
                palavras.append({"t": round(palavra.start, 3), "p": texto, "troca": False})
        if ao_progredir and info.duration:
            ao_progredir(min(1.0, segmento.end / info.duration))
    return palavras
