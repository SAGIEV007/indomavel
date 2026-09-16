"""Fila de trabalhos pesados, um por vez: trecho cru, todos os blocos sem edição e vídeo pronto para publicar."""

import os
import queue
import threading
import time
import traceback
import uuid

from . import config, legenda, recorte, render, youtube

EM_ANDAMENTO = ("na_fila", "baixando")
FOLGA_FINAL_S = 2.0


def nome_do_bloco(numero, bloco):
    """Nome do arquivo de um bloco: número na ordem do vídeo, título e onde começa (ex.: 03 - Título (0h12m01s))."""
    return f"{numero:02d} - {youtube.nome_seguro(bloco['titulo'] or 'bloco', 80)} ({youtube.marca_tempo(bloco['inicio'])})"


class Fila:
    def __init__(self, obter_frases, obter_trechos):
        """obter_frases(id) devolve as frases do vídeo; obter_trechos(id, inicio, fim, estilo) a legenda palavra por palavra."""
        self.obter_frases = obter_frases
        self.obter_trechos = obter_trechos
        self._fila = queue.Queue()
        self._tarefas = {}
        self._trava = threading.Lock()
        threading.Thread(target=self._trabalhar, name="fila-trabalhos", daemon=True).start()

    def _nova(self, tipo, youtube_id, inicio, fim, titulo, **extras):
        tarefa = {
            "id": uuid.uuid4().hex[:10],
            "tipo": tipo,
            "youtube_id": youtube_id,
            "inicio": inicio,
            "fim": fim,
            "titulo": titulo,
            "estado": "na_fila",
            "mensagem": "Aguardando a vez",
            "criada_em": time.time(),
            "arquivo": None,
            "legenda": None,
            **extras,
        }
        with self._trava:
            self._tarefas[tarefa["id"]] = tarefa
        self._fila.put(tarefa["id"])
        return dict(tarefa)

    def adicionar(self, youtube_id, inicio, fim, titulo, com_legenda=True, subpasta=None):
        return self._nova("trecho", youtube_id, inicio, fim, titulo, com_legenda=com_legenda, subpasta=subpasta)

    def adicionar_exportacao(self, youtube_id, inicio, fim, titulo, formato, estilo, subpasta=None, srt_separado=False):
        """srt_separado: além (ou em vez) da legenda queimada, grava a legenda palavra por palavra num .srt ao lado."""
        return self._nova("exportacao", youtube_id, inicio, fim, titulo, formato=formato, estilo=estilo,
                          subpasta=subpasta, srt_separado=srt_separado)

    def adicionar_blocos_crus(self, youtube_id, titulo, blocos):
        """Todos os blocos do vídeo, sem edição e na maior qualidade: baixa o vídeo inteiro uma vez e recorta sem perda.

        blocos: dicts com inicio, fim e titulo, na ordem do vídeo.
        """
        blocos = sorted(({"inicio": float(b["inicio"]), "fim": float(b["fim"]), "titulo": b.get("titulo") or ""} for b in blocos),
                        key=lambda b: b["inicio"])
        subpasta = os.path.join("blocos sem edição", youtube.nome_seguro(titulo or youtube_id, 60))
        return self._nova("blocos_crus", youtube_id, blocos[0]["inicio"] if blocos else 0.0, blocos[-1]["fim"] if blocos else 0.0,
                          titulo, blocos=blocos, subpasta=subpasta, feitos=0)

    def ver(self, ident):
        with self._trava:
            tarefa = self._tarefas.get(ident)
            return dict(tarefa) if tarefa else None

    def listar(self):
        with self._trava:
            return sorted((dict(t) for t in self._tarefas.values()), key=lambda t: t["criada_em"], reverse=True)

    def _atualizar(self, ident, **campos):
        with self._trava:
            self._tarefas[ident].update(campos)

    def _progresso_download(self, ident, dados, prefixo="Baixando do YouTube"):
        if dados.get("status") == "downloading":
            total = dados.get("total_bytes") or dados.get("total_bytes_estimate")
            baixado = dados.get("downloaded_bytes")
            if total and baixado:
                self._atualizar(ident, mensagem=f"{prefixo}… {100 * baixado / total:.0f}%")
        elif dados.get("status") == "finished":
            self._atualizar(ident, mensagem="Juntando áudio e vídeo")

    def _trabalhar(self):
        while True:
            ident = self._fila.get()
            tarefa = self.ver(ident)
            try:
                self._atualizar(ident, estado="baixando", mensagem="Começando")
                pasta = os.path.join(config.PASTA_DOWNLOADS, tarefa.get("subpasta") or time.strftime("%Y-%m-%d"))
                if tarefa["tipo"] == "exportacao":
                    self._exportar(ident, tarefa, pasta)
                elif tarefa["tipo"] == "blocos_crus":
                    self._baixar_blocos_crus(ident, tarefa, pasta)
                else:
                    self._baixar_trecho(ident, tarefa, pasta)
            except Exception as erro:
                self._atualizar(ident, estado="falhou", mensagem=str(erro)[:400], detalhe=traceback.format_exc()[-2000:])
            finally:
                self._fila.task_done()

    def _baixar_trecho(self, ident, tarefa, pasta):
        arquivo = youtube.baixar_trecho(
            tarefa["youtube_id"], tarefa["inicio"], tarefa["fim"], pasta, tarefa["titulo"],
            ao_progredir=lambda dados: self._progresso_download(ident, dados),
        )
        caminho_srt, mensagem = None, "Pronto"
        if tarefa.get("com_legenda"):
            self._atualizar(ident, mensagem="Gerando a legenda .srt")
            try:
                frases = self.obter_frases(tarefa["youtube_id"])
                caminho_srt = os.path.splitext(arquivo)[0] + ".srt"
                with open(caminho_srt, "w", encoding="utf-8") as saida:
                    saida.write(legenda.srt(frases, tarefa["inicio"], tarefa["fim"]))
            except Exception as erro:
                caminho_srt = None
                mensagem = f"Vídeo baixado, mas sem legenda: {erro}"
        self._atualizar(ident, estado="pronta", mensagem=mensagem, arquivo=arquivo, legenda=caminho_srt)

    def _baixar_blocos_crus(self, ident, tarefa, pasta):
        blocos = tarefa["blocos"]
        fonte = youtube.baixar_video_maximo(
            tarefa["youtube_id"],
            ao_progredir=lambda dados: self._progresso_download(ident, dados, "Baixando o vídeo inteiro na maior qualidade"),
        )
        try:
            frases = self.obter_frases(tarefa["youtube_id"])
        except Exception:
            frases = None
        os.makedirs(pasta, exist_ok=True)
        for numero, bloco in enumerate(blocos, start=1):
            self._atualizar(ident, mensagem=f"Recortando o bloco {numero} de {len(blocos)}, sem recomprimir", feitos=numero - 1)
            base = os.path.join(pasta, nome_do_bloco(numero, bloco))
            fim = bloco["fim"] + FOLGA_FINAL_S
            comeco = recorte.recortar_sem_perda(fonte, bloco["inicio"], fim, base + ".mp4")
            if frases:
                with open(base + ".srt", "w", encoding="utf-8") as saida:
                    saida.write(legenda.srt(frases, comeco, fim))
        mensagem = f"Pronto: {len(blocos)} blocos na qualidade original" + (" com .srt" if frases else "")
        self._atualizar(ident, estado="pronta", mensagem=mensagem, arquivo=pasta, feitos=len(blocos))

    def _exportar(self, ident, tarefa, pasta):
        estilo = tarefa["estilo"]
        trechos, aviso = [], None
        if estilo["legenda"] or tarefa.get("srt_separado"):
            self._atualizar(ident, mensagem="Buscando o tempo de cada palavra")
            try:
                trechos = self.obter_trechos(tarefa["youtube_id"], tarefa["inicio"], tarefa["fim"], estilo)
            except Exception as erro:
                aviso = f"Exportado sem legenda: {erro}"
        arquivo = render.exportar(
            tarefa["youtube_id"], tarefa["inicio"], tarefa["fim"], tarefa["formato"], estilo, trechos, pasta,
            tarefa["titulo"], ao_progredir=lambda mensagem: self._atualizar(ident, mensagem=mensagem),
        )
        base = os.path.splitext(arquivo)[0]
        caminho_srt = None
        if tarefa.get("srt_separado") and trechos:
            caminho_srt = base + ".srt"
            with open(caminho_srt, "w", encoding="utf-8") as saida:
                saida.write(legenda.srt_de_trechos(trechos, tarefa["inicio"], tarefa["fim"], estilo["maiusculas"]))
        if estilo["card"] and (estilo["tag"] or estilo["headline"]):
            with open(base + ".txt", "w", encoding="utf-8") as saida:
                saida.write(f"{estilo['tag']}\n{estilo['headline']}\n")
        mensagem = "Pronto: vídeo com card e legenda num .srt separado" if caminho_srt else "Pronto para publicar"
        self._atualizar(ident, estado="pronta", mensagem=aviso or mensagem, arquivo=arquivo, legenda=caminho_srt)
