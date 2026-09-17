"""Tempo de cada palavra de um vídeo, para a legenda palavra por palavra do editor e do vídeo exportado.

Vídeos processados aqui já têm palavras.json. Para vídeos do Chub, a legenda
automática do YouTube (a mesma que o Chub usa) é baixada uma vez e guardada em
dados/palavras/<id>.json.
"""

import json
import os
import threading

from . import acervo_local, config, legendas

_travas = {}
_trava_geral = threading.Lock()


def _trava_do_video(youtube_id):
    with _trava_geral:
        return _travas.setdefault(youtube_id, threading.Lock())


def palavras_do_video(youtube_id):
    locais = acervo_local.ler(youtube_id, "palavras.json")
    if locais is not None:
        return locais
    caminho = os.path.join(config.PASTA_DADOS, "palavras", youtube_id + ".json")
    with _trava_do_video(youtube_id):
        if os.path.exists(caminho):
            with open(caminho, encoding="utf-8") as arquivo:
                return json.load(arquivo)
        pasta_legenda = os.path.join(config.PASTA_DADOS, "palavras", "_legendas", youtube_id)
        _, json3 = legendas.baixar_legenda(youtube_id, pasta_legenda)
        if not json3:
            raise RuntimeError("o YouTube não tem legenda automática para este vídeo")
        palavras = legendas.palavras_do_json3(json3)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho + ".tmp", "w", encoding="utf-8") as arquivo:
            json.dump(palavras, arquivo, ensure_ascii=False)
        os.replace(caminho + ".tmp", caminho)
        return palavras
