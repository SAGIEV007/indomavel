"""Versão do código: um resumo do conteúdo dos arquivos do programa e da tela.

O .bat compara esta versão com a do servidor que já está aberto e reinicia o servidor quando
elas não batem. Foi o que faltou em 15/09: um servidor antigo ficou aberto, a tela nova
conversou com ele e deu "erro 404" em tudo que era novo.
"""

import hashlib
import os

from . import config

PASTAS = [
    os.path.join(config.RAIZ, "indomavel"),
    os.path.join(config.PASTA_WEB, "templates"),
    os.path.join(config.PASTA_WEB, "static", "js"),
    os.path.join(config.PASTA_WEB, "static", "css"),
]
EXTENSOES = (".py", ".html", ".js", ".css")


def calcular():
    resumo = hashlib.sha1()
    for pasta in PASTAS:
        for raiz, pastas, arquivos in os.walk(pasta):
            pastas[:] = sorted(p for p in pastas if p != "__pycache__")
            for nome in sorted(arquivos):
                if not nome.endswith(EXTENSOES):
                    continue
                caminho = os.path.join(raiz, nome)
                resumo.update(os.path.relpath(caminho, config.RAIZ).replace("\\", "/").encode("utf-8"))
                with open(caminho, "rb") as arquivo:
                    resumo.update(arquivo.read())
    return resumo.hexdigest()[:12]


VERSAO = calcular()


if __name__ == "__main__":
    # Uso pelo .bat: python -m indomavel.versao <arquivo>  (grava a versão no arquivo)
    import sys

    with open(sys.argv[1], "w", encoding="ascii") as saida:
        saida.write(VERSAO)
