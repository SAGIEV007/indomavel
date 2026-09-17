"""Baixa de verdade o menor bloco pronto de um vídeo do Chub e confere o arquivo com o ffprobe.

Uso: .venv\\Scripts\\python.exe scripts\\testar_download.py [id_do_youtube]
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indomavel import config, legenda, youtube  # noqa: E402
from indomavel.chub import Chub  # noqa: E402


def main():
    youtube_id = sys.argv[1] if len(sys.argv) > 1 else "DxN0m8JN94w"
    chub = Chub()
    blocos = chub.blocos(youtube_id)
    candidatos = [bloco for bloco in blocos if bloco["pronto"]] or blocos
    bloco = min(candidatos, key=lambda b: b["duracao"])
    print(f"Bloco: {bloco['titulo']} | {bloco['inicio']:.2f}s a {bloco['fim']:.2f}s ({bloco['duracao']:.1f}s)")

    pasta = os.path.join(config.PASTA_DOWNLOADS, "_teste")
    inicio = time.time()
    arquivo = youtube.baixar_trecho(youtube_id, bloco["inicio"], bloco["fim"], pasta, bloco["titulo"], silencioso=False)
    print(f"Baixado em {time.time() - inicio:.1f}s: {arquivo}")

    caminho_srt = os.path.splitext(arquivo)[0] + ".srt"
    with open(caminho_srt, "w", encoding="utf-8") as saida:
        saida.write(legenda.srt(chub.transcricao(youtube_id)["frases"], bloco["inicio"], bloco["fim"]))

    ffprobe = os.path.join(os.path.dirname(config.FFMPEG), "ffprobe.exe") if config.FFMPEG else "ffprobe"
    resultado = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
         "-of", "json", arquivo],
        capture_output=True, text=True,
    )
    info = json.loads(resultado.stdout)
    print("ffprobe:", json.dumps(info, ensure_ascii=False))
    duracao = float(info["format"]["duration"])
    tipos = sorted(stream["codec_type"] for stream in info["streams"])
    with open(caminho_srt, encoding="utf-8") as entrada:
        print("Começo da legenda:\n" + entrada.read()[:300])
    ok = abs(duracao - bloco["duracao"]) < 1.5 and tipos == ["audio", "video"]
    print(f"RESULTADO: {'OK' if ok else 'FALHOU'} | duração {duracao:.2f}s, esperada {bloco['duracao']:.2f}s | streams {tipos}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
