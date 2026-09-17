"""Baixar e editar em massa: os melhores momentos de vários vídeos, pelo ranking do Chub.

Dois modos:
- "baixar": baixa os blocos de maior potencial de cada vídeo, com 2 s de folga em cada ponta
  e a legenda .srt ao lado, para os editores cortarem no CapCut.
- "editar": em cada um dos melhores blocos o Gemini escolhe um corte curto e escreve o card;
  o vídeo sai com o card e o rodapé, e a legenda vai num arquivo .srt separado (não queimada),
  porque a legenda automática pode ter erros.
Tudo passa pela fila de trabalhos pesados da tela, um por vez.
"""

import os
import queue
import re
import threading
import time
import traceback
import uuid

from . import automacao, config, gemini, render, youtube
from .gemini import GeminiErro

MODOS = ("baixar", "editar")
FOLGA_S = 2.0
MAX_VIDEOS = 30
MAX_POR_VIDEO = 10
ID_YOUTUBE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def escolher_blocos(blocos, quantidade, so_prontos):
    """Os blocos de maior potencial (mesmo ranking da pauta do Chub); só os prontos para short, se pedido."""
    candidatos = [b for b in blocos if b["pronto"]] if so_prontos else list(blocos)
    return sorted(candidatos, key=lambda b: -b["potencial"])[:quantidade]


def um_corte_por_bloco(cortes, quantidade):
    vistos, escolhidos = set(), []
    for corte in cortes:
        if corte["bloco_id"] in vistos:
            continue
        vistos.add(corte["bloco_id"])
        escolhidos.append(corte)
    return escolhidos[:quantidade]


class Massa:
    def __init__(self, obter_video, obter_blocos, obter_frases, fila):
        self.obter_video = obter_video
        self.obter_blocos = obter_blocos
        self.obter_frases = obter_frases
        self.fila = fila
        self._trabalhos = {}
        self._pendentes = queue.Queue()
        self._trava = threading.Lock()
        threading.Thread(target=self._trabalhar, name="massa", daemon=True).start()

    def criar(self, modo, videos, por_video, so_prontos, formato):
        if modo not in MODOS:
            raise ValueError("modo desconhecido")
        ids = [str(v) for v in videos if ID_YOUTUBE.match(str(v))]
        if not ids:
            raise ValueError("escolha pelo menos um vídeo")
        if formato not in render.FORMATOS:
            raise ValueError("formato desconhecido")
        trabalho = {
            "id": uuid.uuid4().hex[:10],
            "modo": modo,
            "videos": list(dict.fromkeys(ids))[:MAX_VIDEOS],
            "por_video": max(1, min(MAX_POR_VIDEO, int(por_video))),
            "so_prontos": bool(so_prontos),
            "formato": formato,
            "estado": "na_fila",
            "mensagem": "Aguardando a vez",
            "criado_em": time.time(),
            "pasta": None,
            "itens": [],
        }
        with self._trava:
            self._trabalhos[trabalho["id"]] = trabalho
        self._pendentes.put(trabalho["id"])
        return self.ver(trabalho["id"])

    def ver(self, ident):
        with self._trava:
            trabalho = self._trabalhos.get(ident)
            return {**trabalho, "itens": [dict(i) for i in trabalho["itens"]]} if trabalho else None

    def listar(self):
        with self._trava:
            ids = sorted(self._trabalhos, key=lambda i: self._trabalhos[i]["criado_em"], reverse=True)
        return [self.ver(ident) for ident in ids]

    def _atualizar(self, trabalho, **campos):
        with self._trava:
            trabalho.update(campos)

    def _trabalhar(self):
        while True:
            ident = self._pendentes.get()
            trabalho = self._trabalhos[ident]
            try:
                self._executar(trabalho)
            except Exception as erro:
                self._atualizar(trabalho, estado="falhou", mensagem=str(erro)[:300])
                traceback.print_exc()
            finally:
                self._pendentes.task_done()

    def _executar(self, trabalho):
        editar = trabalho["modo"] == "editar"
        base = os.path.join("em_massa", time.strftime("%Y-%m-%d %Hh%M") + (" - editados" if editar else " - baixados"))
        self._atualizar(trabalho, estado="rodando", pasta=os.path.join(config.PASTA_DOWNLOADS, base))
        avisos = []
        for numero, youtube_id in enumerate(trabalho["videos"], start=1):
            video = self.obter_video(youtube_id)
            self._atualizar(trabalho, mensagem=f"Vídeo {numero} de {len(trabalho['videos'])}: {video['titulo'][:60]}")
            blocos = escolher_blocos(self.obter_blocos(youtube_id), trabalho["por_video"], trabalho["so_prontos"])
            if not blocos:
                avisos.append(f"“{video['titulo'][:40]}” não tem blocos que sirvam")
                continue
            subpasta = os.path.join(base, youtube.nome_seguro(video["titulo"], 60))
            if editar:
                self._editar(trabalho, video, blocos, subpasta, avisos)
            else:
                for bloco in blocos:
                    item = self._novo_item(trabalho, video, bloco["titulo"], max(0.0, bloco["inicio"] - FOLGA_S), bloco["fim"] + FOLGA_S)
                    tarefa = self.fila.adicionar(youtube_id, item["inicio"], item["fim"], bloco["titulo"], True, subpasta=subpasta)
                    self._esperar(item, tarefa)
        prontos = sum(1 for item in trabalho["itens"] if item["estado"] == "pronta")
        resumo = f"Terminado: {prontos} de {len(trabalho['itens'])} arquivo(s) prontos"
        self._atualizar(trabalho, estado="pronto", mensagem=resumo + (". " + "; ".join(avisos[:3]) if avisos else ""))

    def _editar(self, trabalho, video, blocos, subpasta, avisos):
        frases = self.obter_frases(video["youtube_id"])
        if trabalho.get("sem_gemini"):
            # O Gemini já falhou neste trabalho: não espera por ele de novo em cada vídeo.
            cortes = automacao.cortes_sem_gemini(video, blocos, frases)
        else:
            try:
                dados, _ = gemini.gerar_json(
                    automacao.INSTRUCAO, automacao.conteudo_do_pedido(video, blocos, frases, cortes_por_bloco=1), automacao.ESQUEMA,
                )
                cortes = automacao.validar_cortes(dados.get("cortes") or [], video, blocos, frases)
            except GeminiErro as erro:
                self._atualizar(trabalho, sem_gemini=True)
                cortes = automacao.cortes_sem_gemini(video, blocos, frases)
                avisos.append(f"{gemini.motivo_curto(erro)}: cortes pelos momentos fortes do Chub, com o título do bloco no card")
        for corte in um_corte_por_bloco(cortes, trabalho["por_video"]):
            item = self._novo_item(trabalho, video, corte["headline"], corte["inicio"], corte["fim"], corte["tag"])
            estilo = render.normalizar_estilo({"tag": corte["tag"], "headline": corte["headline"], "legenda": False})
            tarefa = self.fila.adicionar_exportacao(
                video["youtube_id"], corte["inicio"], corte["fim"], corte["headline"], trabalho["formato"], estilo,
                subpasta=subpasta, srt_separado=True,
            )
            self._esperar(item, tarefa)

    def _novo_item(self, trabalho, video, titulo, inicio, fim, tag=""):
        item = {"youtube_id": video["youtube_id"], "video_titulo": video["titulo"], "titulo": titulo, "tag": tag,
                "inicio": round(inicio, 2), "fim": round(fim, 2), "estado": "na_fila", "mensagem": "Na fila",
                "arquivo": None, "legenda": None}
        with self._trava:
            trabalho["itens"].append(item)
        return item

    def _esperar(self, item, tarefa):
        while True:
            atual = self.fila.ver(tarefa["id"])
            with self._trava:
                item.update(estado=atual["estado"], mensagem=atual["mensagem"], arquivo=atual.get("arquivo"), legenda=atual.get("legenda"))
            if atual["estado"] in ("pronta", "falhou"):
                return
            time.sleep(1.5)
