"""Fila de trabalhos pesados, um por vez: trecho cru, todos os blocos sem edição e vídeo pronto para publicar."""

import os
import queue
import shutil
import threading
import time
import traceback
import uuid

from . import config, headlines, legenda, recorte, render, youtube

EM_ANDAMENTO = ("na_fila", "baixando")
FOLGA_FINAL_S = 2.0


def nome_do_bloco(numero, bloco):
    """Nome do arquivo de um bloco: número na ordem do vídeo, título e onde começa (ex.: 03 - Título (0h12m01s))."""
    return f"{numero:02d} - {youtube.nome_seguro(bloco['titulo'] or 'bloco', 80)} ({youtube.marca_tempo(bloco['inicio'])})"


class Fila:
    def __init__(self, obter_frases, obter_trechos, max_trabalhadores=2):
        """obter_frases(id) devolve as frases do vídeo; obter_trechos(id, inicio, fim, estilo) a legenda palavra por palavra."""
        self.obter_frases = obter_frases
        self.obter_trechos = obter_trechos
        self._fila = queue.Queue()
        self._tarefas = {}
        self._trava = threading.Lock()
        for i in range(max_trabalhadores):
            threading.Thread(target=self._trabalhar, name=f"fila-trabalhos-{i+1}", daemon=True).start()

    def _nova(self, tipo, youtube_id, inicio, fim, titulo, **extras):
        tarefa = {
            "id": uuid.uuid4().hex[:10],
            "tipo": tipo,
            "youtube_id": youtube_id,
            "inicio": inicio,
            "fim": fim,
            "titulo": titulo,
            "estado": "na_fila",
            "mensagem": "Aguardando a vez na fila",
            "etapa": "na_fila",
            "etapa_nome": "Aguardando na fila",
            "progresso": 0.0,
            "eta_s": None,
            "criada_em": time.time(),
            "iniciado_em": None,
            "concluido_em": None,
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

    def adicionar_exportacao(self, youtube_id, inicio, fim, titulo, formato, estilo, subpasta=None, srt_separado=False, trechos=None):
        """srt_separado: além (ou em vez) da legenda queimada, grava a legenda palavra por palavra num .srt ao lado."""
        return self._nova("exportacao", youtube_id, inicio, fim, titulo, formato=formato, estilo=estilo,
                          subpasta=subpasta, srt_separado=srt_separado, trechos=trechos)

    def adicionar_exportacao_lote(self, youtube_id, titulo, blocos, formato, estilo,
                                 com_legenda=True, com_headline=True, manter_bruto=False, com_marca=True, subpasta=None):
        """Exporta todos os cortes válidos de um vídeo renderizados no formato com toggles, salvando em output/cortes/.

        com_marca: rodapé da propaganda eleitoral (CNPJ). Desligado para canais não oficiais.
        """
        blocos_validos = []
        for b in blocos:
            duracao = float(b.get("duracao") or (float(b["fim"]) - float(b["inicio"])))
            if 20.0 <= duracao <= 90.0:
                blocos_validos.append(b)
        if not blocos_validos:
            blocos_validos = [b for b in blocos if 15.0 <= float(b.get("duracao") or (float(b["fim"]) - float(b["inicio"]))) <= 120.0]
        if not blocos_validos:
            blocos_validos = list(blocos)[:5]

        # Prioriza cortes na faixa ideal (45s - 75s), mantendo estabilidade cronológica
        def ordem_ideal(b):
            d = float(b.get("duracao") or (float(b["fim"]) - float(b["inicio"])))
            faixa_ideal = 0 if 45.0 <= d <= 75.0 else 1
            return (faixa_ideal, float(b.get("inicio", 0.0)))

        blocos_validos.sort(key=ordem_ideal)

        estilo = render.normalizar_estilo(estilo)
        estilo["rodape"] = bool(com_marca)
        if not subpasta:
            subpasta = os.path.join("cortes", youtube.nome_seguro(titulo or youtube_id, 60))
        return self._nova("exportacao_lote", youtube_id, 0.0, 0.0, titulo,
                          blocos=blocos_validos, formato=formato, estilo=estilo,
                          com_legenda=com_legenda, com_headline=com_headline, com_marca=bool(com_marca),
                          manter_bruto=manter_bruto, subpasta=subpasta, feitos=0)

    def adicionar_blocos_crus(self, youtube_id, titulo, blocos, subpasta=None):
        """Todos os blocos do vídeo, sem edição e na maior qualidade: baixa o vídeo inteiro uma vez e recorta sem perda.

        blocos: dicts com inicio, fim e titulo, na ordem do vídeo.
        """
        blocos = sorted(({"inicio": float(b["inicio"]), "fim": float(b["fim"]), "titulo": b.get("titulo") or ""} for b in blocos),
                        key=lambda b: b["inicio"])
        if not subpasta:
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
                self._atualizar(
                    ident,
                    estado="baixando",
                    etapa="iniciando",
                    etapa_nome="Iniciando exportação",
                    progresso=1.0,
                    iniciado_em=time.time(),
                    mensagem="Começando exportação...",
                )
                pasta = os.path.join(config.PASTA_DOWNLOADS, tarefa.get("subpasta") or time.strftime("%Y-%m-%d"))
                if tarefa["tipo"] == "exportacao":
                    self._exportar(ident, tarefa, pasta)
                elif tarefa["tipo"] == "exportacao_lote":
                    self._exportar_lote(ident, tarefa, pasta)
                elif tarefa["tipo"] == "blocos_crus":
                    self._baixar_blocos_crus(ident, tarefa, pasta)
                else:
                    self._baixar_trecho(ident, tarefa, pasta)
            except Exception as erro:
                self._atualizar(
                    ident,
                    estado="falhou",
                    etapa="falhou",
                    etapa_nome="Falha na exportação",
                    mensagem=str(erro)[:400],
                    detalhe=traceback.format_exc()[-2000:],
                )
            finally:
                self._fila.task_done()

    def _progresso_exportacao(self, ident, msg, progresso=None, etapa=None, etapa_nome=None, eta_s=None):
        campos = {"mensagem": msg}
        if progresso is not None:
            campos["progresso"] = progresso
        if etapa is not None:
            campos["etapa"] = etapa
        if etapa_nome is not None:
            campos["etapa_nome"] = etapa_nome
        if eta_s is not None:
            campos["eta_s"] = eta_s
        self._atualizar(ident, **campos)

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
        self._atualizar(ident, estado="pronta", etapa="concluido", etapa_nome="Pronto", progresso=100.0, eta_s=0,
                        concluido_em=time.time(), mensagem=mensagem, arquivo=arquivo, legenda=caminho_srt)

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
        pasta_cortes = os.path.join(config.PASTA_OUTPUT_CORTES, youtube.nome_seguro(tarefa["titulo"] or tarefa["youtube_id"], 60), "01_crus")
        os.makedirs(pasta_cortes, exist_ok=True)
        for numero, bloco in enumerate(blocos, start=1):
            self._atualizar(ident, mensagem=f"Recortando o bloco {numero} de {len(blocos)}, sem recomprimir", feitos=numero - 1)
            base = os.path.join(pasta, nome_do_bloco(numero, bloco))
            fim = bloco["fim"] + FOLGA_FINAL_S
            comeco = recorte.recortar_sem_perda(fonte, bloco["inicio"], fim, base + ".mp4")
            if frases:
                with open(base + ".srt", "w", encoding="utf-8") as saida:
                    saida.write(legenda.srt(frases, comeco, fim))
            if os.path.abspath(pasta) != os.path.abspath(pasta_cortes):
                try:
                    shutil.copy(base + ".mp4", os.path.join(pasta_cortes, os.path.basename(base + ".mp4")))
                    if frases and os.path.exists(base + ".srt"):
                        shutil.copy(base + ".srt", os.path.join(pasta_cortes, os.path.basename(base + ".srt")))
                except Exception:
                    pass
        mensagem = f"Pronto: {len(blocos)} blocos na qualidade original" + (" com .srt" if frases else "")
        self._atualizar(ident, estado="pronta", etapa="concluido", etapa_nome="Pronto", progresso=100.0, eta_s=0,
                        concluido_em=time.time(), mensagem=mensagem, arquivo=pasta, arquivo_cortes=pasta_cortes, feitos=len(blocos))

    def _exportar(self, ident, tarefa, pasta):
        estilo = tarefa["estilo"]
        trechos = tarefa.get("trechos") or []
        aviso = None
        if not trechos and (estilo["legenda"] or tarefa.get("srt_separado")):
            self._atualizar(ident, mensagem="Buscando o tempo de cada palavra", etapa="processando", etapa_nome="Alinhando legendas")
            try:
                trechos = self.obter_trechos(tarefa["youtube_id"], tarefa["inicio"], tarefa["fim"], estilo)
            except Exception:
                trechos = []
            if not trechos:
                try:
                    frases = self.obter_frases(tarefa["youtube_id"])
                    trechos = legenda.trechos_de_frases(frases, tarefa["inicio"], tarefa["fim"], estilo.get("max_palavras", 3))
                except Exception as erro:
                    aviso = f"Exportado sem legenda: {erro}"

        def _ao_progredir(msg, progresso=None, etapa=None, etapa_nome=None, eta_s=None):
            self._progresso_exportacao(ident, msg, progresso=progresso, etapa=etapa, etapa_nome=etapa_nome, eta_s=eta_s)

        arquivo = render.exportar(
            tarefa["youtube_id"], tarefa["inicio"], tarefa["fim"], tarefa["formato"], estilo, trechos, pasta,
            tarefa["titulo"], ao_progredir=_ao_progredir,
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

        # Garante cópia em output/cortes/ para acesso e organização imediata
        os.makedirs(config.PASTA_OUTPUT_CORTES, exist_ok=True)
        caminho_cortes = os.path.join(config.PASTA_OUTPUT_CORTES, os.path.basename(arquivo))
        if os.path.abspath(arquivo) != os.path.abspath(caminho_cortes):
            try:
                shutil.copy2(arquivo, caminho_cortes)
            except Exception:
                caminho_cortes = arquivo

        mensagem = "Pronto: vídeo com card e legenda num .srt separado" if caminho_srt else "Pronto para publicar"
        self._atualizar(
            ident,
            estado="pronta",
            etapa="concluido",
            etapa_nome="Pronto para publicar",
            progresso=100.0,
            eta_s=0,
            concluido_em=time.time(),
            mensagem=aviso or mensagem,
            arquivo=arquivo,
            arquivo_cortes=caminho_cortes,
            legenda=caminho_srt,
        )

    def _exportar_lote(self, ident, tarefa, pasta):
        blocos = tarefa["blocos"]
        pasta_output = os.path.join(config.PASTA_OUTPUT_CORTES, youtube.nome_seguro(tarefa["titulo"] or tarefa["youtube_id"], 60))
        os.makedirs(pasta_output, exist_ok=True)
        os.makedirs(pasta, exist_ok=True)

        if tarefa.get("manter_bruto"):
            self._atualizar(ident, mensagem="Baixando o vídeo bruto completo na maior qualidade...", etapa="baixando", etapa_nome="Baixando vídeo bruto")
            caminho_bruto = youtube.baixar_video_maximo(
                tarefa["youtube_id"],
                ao_progredir=lambda dados: self._progresso_download(ident, dados, "Baixando o vídeo bruto"),
            )
            try:
                destino_bruto = os.path.join(pasta_output, "00_RAW_COMPLETO.mp4")
                if not os.path.exists(destino_bruto):
                    shutil.copy(caminho_bruto, destino_bruto)
            except Exception as erro:
                print(f"Aviso ao salvar cópia do vídeo bruto: {erro}")

        estilo_base = render.normalizar_estilo(tarefa.get("estilo"))
        formato = tarefa["formato"]
        # Com e sem marca d'água em pastas separadas: exportar o mesmo vídeo das duas formas não sobrescreve nada.
        subpasta_marca = "com marca" if tarefa.get("com_marca", True) else "sem marca"
        pasta_output = os.path.join(pasta_output, subpasta_marca)
        pasta = os.path.join(pasta, subpasta_marca)
        os.makedirs(pasta_output, exist_ok=True)
        os.makedirs(pasta, exist_ok=True)
        total = len(blocos)
        exportados = 0
        ultimo_erro = None
        inicio_lote = time.time()

        for i, bloco in enumerate(blocos, start=1):
            # VAD acoustic safety buffer: +200ms no fim, -150ms no início
            inicio = max(0.0, float(bloco["inicio"]) - 0.15)
            fim = float(bloco["fim"]) + 0.20
            titulo_bloco = bloco.get("titulo") or f"corte_{i:02d}"
            pct = round(100.0 * (i - 1) / total, 1)
            self._atualizar(
                ident,
                mensagem=f"Renderizando corte {i} de {total}: {titulo_bloco[:40]}…",
                etapa="renderizando",
                etapa_nome=f"Renderizando corte {i} de {total}",
                progresso=pct,
                feitos=exportados,
            )

            estilo_corte = dict(estilo_base)
            estilo_corte["card"] = bool(tarefa.get("com_headline", True)) and formato != "16:9"
            if estilo_corte["card"]:
                h_opcoes = headlines.headlines_heuristicas(
                    titulo=titulo_bloco,
                    resumo=bloco.get("resumo") or "",
                    categoria=bloco.get("categoria") or "",
                    temas=bloco.get("temas") or [],
                    destaques=[d.get("texto", "") for d in bloco.get("destaques") or []],
                )
                estilo_corte["tag"] = h_opcoes[0]["tag"]
                estilo_corte["headline"] = h_opcoes[0]["headline"]

            estilo_corte["legenda"] = bool(tarefa.get("com_legenda", True))
            estilo_corte["rodape"] = bool(tarefa.get("com_marca", True))
            trechos = []
            if estilo_corte["legenda"]:
                try:
                    trechos = self.obter_trechos(tarefa["youtube_id"], inicio, fim, estilo_corte)
                except Exception:
                    trechos = []
                if not trechos:
                    try:
                        frases = self.obter_frases(tarefa["youtube_id"])
                        trechos = legenda.trechos_de_frases(frases, inicio, fim, estilo_corte.get("max_palavras", 3))
                    except Exception:
                        trechos = []

            try:
                nome_corte = f"{i:02d} - {youtube.nome_seguro(titulo_bloco, 60)}"
                def _progresso_corte(msg, progresso=None, etapa=None, etapa_nome=None, eta_s=None):
                    campos = {"mensagem": f"Corte {i}/{total}: {msg}"}
                    if etapa and etapa != "concluido":
                        campos["etapa"] = etapa
                    campos["etapa_nome"] = f"Corte {i} de {total} · {etapa_nome}" if etapa_nome else f"Corte {i} de {total}"
                    if progresso is not None:
                        feito = (i - 1 + progresso / 100.0) / total
                        campos["progresso"] = round(100.0 * feito, 1)
                        # Tempo restante do lote inteiro, pelo ritmo dos cortes até aqui.
                        decorrido = time.time() - inicio_lote
                        campos["eta_s"] = round(decorrido / feito * (1.0 - feito)) if feito > 0.02 else None
                    self._atualizar(ident, **campos)

                arquivo_gerado = render.exportar(
                    tarefa["youtube_id"], inicio, fim, formato, estilo_corte, trechos, pasta_output,
                    nome_corte, ao_progredir=_progresso_corte,
                )
                try:
                    shutil.copy(arquivo_gerado, os.path.join(pasta, os.path.basename(arquivo_gerado)))
                except Exception:
                    pass
                exportados += 1
            except Exception as erro:
                ultimo_erro = erro
                print(f"Aviso: corte {i} falhou: {erro}")
                continue

        if total and not exportados:
            raise RuntimeError(f"Nenhum dos {total} cortes foi exportado: {ultimo_erro}")
        marca = "com marca d'água" if tarefa.get("com_marca", True) else "sem marca d'água"
        mensagem = f"Pronto: {exportados} de {total} cortes exportados ({marca}) em output/cortes/"
        self._atualizar(
            ident,
            estado="pronta",
            etapa="concluido",
            etapa_nome="Lote concluído",
            progresso=100.0,
            eta_s=0,
            concluido_em=time.time(),
            mensagem=mensagem,
            arquivo=pasta_output,
            feitos=exportados,
        )

