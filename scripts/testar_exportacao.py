"""Exporta de verdade um bloco pronto do Chub com card, legenda e rodapé, e confere o arquivo.

Uso: .venv\\Scripts\\python.exe scripts\\testar_exportacao.py [formato ...]   (padrão: 4:5 9:16)
Gera também um quadro PNG de cada vídeo em downloads\\_teste\\ para conferir o visual.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indomavel import config, legenda, palavras, render  # noqa: E402
from indomavel.chub import Chub  # noqa: E402

YOUTUBE_ID = "DxN0m8JN94w"
HEADLINE = "Renan Santos propõe transformar cada eleitor em multiplicador para chegar ao segundo turno"


def main():
    formatos = sys.argv[1:] or ["4:5", "9:16"]
    bloco = next(b for b in Chub().blocos(YOUTUBE_ID) if b["pronto"] and b["duracao"] < 32)
    print(f"Bloco: {bloco['titulo']} | {bloco['inicio']:.2f}s a {bloco['fim']:.2f}s")
    inicio = time.time()
    lista_palavras = palavras.palavras_do_video(YOUTUBE_ID)
    print(f"Palavras com tempo: {len(lista_palavras)} ({time.time() - inicio:.1f}s)")
    estilo = render.normalizar_estilo({"tag": "EM ALTA!", "headline": HEADLINE})
    trechos = legenda.trechos_de_palavras(lista_palavras, bloco["inicio"], bloco["fim"], max_palavras=estilo["max_palavras"])
    print(f"Trechos de legenda: {len(trechos)} | primeiros: {[' '.join(t['palavras']) for t in trechos[:4]]}")
    pasta = os.path.join(config.PASTA_DOWNLOADS, "_teste")
    ffprobe = os.path.join(os.path.dirname(config.FFMPEG), "ffprobe.exe")
    ok = True
    for formato in formatos:
        inicio = time.time()
        ultimo = [""]

        def progresso(mensagem):
            if mensagem.split("…")[0] != ultimo[0].split("…")[0] or mensagem.endswith("100%"):
                print("  ", mensagem, flush=True)
            ultimo[0] = mensagem

        caminho = render.exportar(YOUTUBE_ID, bloco["inicio"], bloco["fim"], formato, estilo, trechos, pasta, bloco["titulo"], progresso)
        info = json.loads(subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", caminho],
            capture_output=True, text=True).stdout)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        esperado = render.FORMATOS[formato]
        duracao = float(info["format"]["duration"])
        certo = (video["width"], video["height"]) == esperado and abs(duracao - bloco["duracao"]) < 1.5
        certo = certo and any(s["codec_type"] == "audio" for s in info["streams"])
        quadro = os.path.splitext(caminho)[0] + ".png"
        subprocess.run([config.FFMPEG, "-y", "-loglevel", "error", "-ss", "8", "-i", caminho, "-frames:v", "1",
                        "-vf", "scale=540:-1", quadro], check=True)
        print(f"{formato}: {'OK' if certo else 'FALHOU'} | {video['width']}x{video['height']} | {duracao:.2f}s | "
              f"{time.time() - inicio:.0f}s | quadro: {quadro}")
        ok = ok and certo
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
