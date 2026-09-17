"""Recorte sem perda: copia o trecho do vídeo baixado sem recomprimir, na qualidade exata do YouTube.

Sem recomprimir, um corte só pode começar num quadro-chave (o ponto em que o vídeo pode ser aberto
sozinho). Nas lives do Renan eles vêm a cada 1 a 7 segundos. Por isso cada corte começa no último
quadro-chave antes do bloco, e a legenda .srt é calculada a partir desse começo real.
"""

import os
import subprocess

from . import config


def _ffprobe():
    return os.path.join(os.path.dirname(config.FFMPEG), "ffprobe.exe") if config.FFMPEG else "ffprobe"


def ler_quadros_chave(saida_do_ffprobe):
    """Tempos (s) dos quadros-chave na saída do ffprobe, uma linha por quadro."""
    tempos = []
    for linha in saida_do_ffprobe.splitlines():
        valor = linha.strip().strip(",")
        try:
            tempos.append(float(valor))
        except ValueError:
            continue
    return sorted(tempos)


def ultimo_quadro_chave(fonte, tempo, janela_s=15.0):
    """O último quadro-chave em ou antes de tempo; sem nenhum na janela, o próprio tempo."""
    resultado = subprocess.run(
        [_ffprobe(), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
         "-read_intervals", f"{max(0.0, tempo - janela_s):.3f}%{tempo + 0.05:.3f}",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", fonte],
        capture_output=True, text=True,
    )
    anteriores = [t for t in ler_quadros_chave(resultado.stdout) if t <= tempo + 0.001]
    return anteriores[-1] if anteriores else tempo


def comando_recorte(fonte, inicio, fim, destino):
    """Comando do ffmpeg que copia [inicio, fim] sem recomprimir (inicio deve ser um quadro-chave)."""
    return [
        config.FFMPEG or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        # Um milésimo depois do quadro-chave: a busca para exatamente nele, e não no anterior.
        "-ss", f"{inicio + 0.001:.3f}", "-to", f"{fim:.3f}", "-i", fonte,
        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart", destino,
    ]


def recortar_sem_perda(fonte, inicio, fim, destino):
    """Copia o trecho a partir do último quadro-chave antes de inicio; devolve o tempo em que o arquivo começa."""
    comeco = ultimo_quadro_chave(fonte, inicio)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    resultado = subprocess.run(comando_recorte(fonte, comeco, fim, destino), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
    if resultado.returncode != 0 or not os.path.exists(destino):
        raise RuntimeError("o ffmpeg não conseguiu recortar: " + resultado.stderr[-400:])
    return comeco
