"""Vídeos de fora do Chub processados aqui. Cada um fica em dados/videos/<id>/:
info.json, frases.json, palavras.json, blocos.json e estado.json."""

import json
import os
import threading
import time

from . import config

ESTADOS_FINAIS = ("pronto", "falhou", "sem_audio")
_trava = threading.RLock()


def _com_retentativas(funcao, tentativas=10, espera_s=0.05):
    """No Windows, trocar um arquivo que outro leitor tem aberto dá "Acesso negado" por um instante."""
    for tentativa in range(tentativas):
        try:
            return funcao()
        except PermissionError:
            if tentativa == tentativas - 1:
                raise
            time.sleep(espera_s * (tentativa + 1))


def pasta_do_video(youtube_id):
    return os.path.join(config.PASTA_DADOS, "videos", youtube_id)


def salvar(youtube_id, nome, dados):
    pasta = pasta_do_video(youtube_id)
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(pasta, nome)
    temporario = destino + ".tmp"
    with _trava:
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False)
        _com_retentativas(lambda: os.replace(temporario, destino))


def ler(youtube_id, nome, padrao=None):
    caminho = os.path.join(pasta_do_video(youtube_id), nome)

    def abrir():
        with open(caminho, encoding="utf-8") as arquivo:
            return json.load(arquivo)

    with _trava:
        if not os.path.exists(caminho):
            return padrao
        return _com_retentativas(abrir)


def salvar_estado(youtube_id, estado, mensagem, progresso=None, **extras):
    salvar(youtube_id, "estado.json", {
        "estado": estado, "mensagem": mensagem, "progresso": progresso, "atualizado_em": time.time(), **extras,
    })


def listar():
    base = os.path.join(config.PASTA_DADOS, "videos")
    if not os.path.isdir(base):
        return []
    videos = []
    for youtube_id in os.listdir(base):
        info = ler(youtube_id, "info.json")
        if not info:
            continue
        estado = ler(youtube_id, "estado.json", {})
        blocos = ler(youtube_id, "blocos.json")
        cortes = ler(youtube_id, "cortes_automaticos.json")
        videos.append({
            "youtube_id": youtube_id,
            "titulo": info.get("titulo") or youtube_id,
            "publicado_em": info.get("publicado_em"),
            "duracao_s": info.get("duracao_s") or 0,
            "fontes": info.get("canal") or "",
            "tem_transcricao": os.path.exists(os.path.join(pasta_do_video(youtube_id), "frases.json")),
            "blocos": len(blocos["blocos"]) if blocos else 0,
            "cortes": cortes,
            "origem": "local",
            "estado": estado.get("estado"),
            "mensagem": estado.get("mensagem"),
            "progresso": estado.get("progresso"),
            "atualizado_em": estado.get("atualizado_em") or 0,
        })
    return sorted(videos, key=lambda v: v["atualizado_em"], reverse=True)
