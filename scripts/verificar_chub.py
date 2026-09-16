"""Confere, ao vivo, que o Indomável conversa com o Chub sozinho.

Uso: .venv\\Scripts\\python.exe scripts\\verificar_chub.py
Sai com código 1 se qualquer chamada falhar.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indomavel.chub import Chub, ChubErro  # noqa: E402


def main():
    chub = Chub(validade_cache=0)
    try:
        inicio = time.time()
        agora = chub.agora()
        print(f"1. Chub respondeu em {time.time() - inicio:.2f}s | hora do servidor {agora['agora']} | blocos ativos {agora['blocos_ativos']}")

        videos = chub.videos_recentes(limite=5)
        print(f"2. Vídeos mais recentes ({len(videos)}):")
        for video in videos:
            print(f"   {video['publicado_em'][:16]} | {video['blocos']:>3} blocos | {video['titulo'][:70]}")

        alvo = next((video for video in videos if video["blocos"]), None)
        if not alvo:
            print("3. Nenhum dos 5 vídeos recentes tem blocos ainda.")
            return 1
        blocos = chub.blocos(alvo["youtube_id"])
        prontos = [bloco for bloco in blocos if bloco["pronto"]]
        print(f"3. Blocos de {alvo['youtube_id']}: {len(blocos)} (prontos para short: {len(prontos)})")

        transcricao = chub.transcricao(alvo["youtube_id"])
        frases = transcricao["frases"]
        print(f"4. Legenda do vídeo: {len(frases)} frases, de {frases[0]['inicio']:.1f}s a {frases[-1]['fim']:.1f}s")
    except ChubErro as erro:
        print(f"FALHOU: {erro}")
        return 1
    print("RESULTADO: conexão com o Chub OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
