"""Processa links de fora do Chub: legenda do YouTube (ou transcrição local), frases e blocos pelo Gemini."""

import queue
import threading
import time
import traceback

from . import acervo_local, blocador, legendas, transcricao_local


class FilaDeLinks:
    def __init__(self, ao_concluir_blocagem=None):
        self._fila = queue.Queue()
        self._ativos = set()
        self._trava = threading.Lock()
        self.ao_concluir_blocagem = ao_concluir_blocagem
        threading.Thread(target=self._trabalhar, name="fila-links", daemon=True).start()

    def ativos(self):
        with self._trava:
            return set(self._ativos)

    def adicionar(self, youtube_id, refazer=False):
        """Coloca o vídeo na fila. Devolve False se ele já está sendo processado."""
        with self._trava:
            if youtube_id in self._ativos:
                return False
            self._ativos.add(youtube_id)
        if not acervo_local.ler(youtube_id, "info.json"):
            acervo_local.salvar(youtube_id, "info.json", {"youtube_id": youtube_id, "titulo": f"Vídeo {youtube_id}"})
        acervo_local.salvar_estado(youtube_id, "na_fila", "Aguardando a vez", 0.0, tentativas=0)
        self._fila.put((youtube_id, refazer))
        return True

    def _trabalhar(self):
        while True:
            youtube_id, refazer = self._fila.get()
            try:
                self._processar(youtube_id, refazer)
            except Exception as erro:
                texto_erro = str(erro)
                try:
                    estado_ant = acervo_local.ler(youtube_id, "estado.json", {}) or {}
                    tentativas = int(estado_ant.get("tentativas", 0)) + 1
                    max_tentativas = 5

                    if "transcrição veio vazia" in texto_erro.lower():
                        acervo_local.salvar_estado(
                            youtube_id,
                            "sem_audio",
                            "Nenhuma fala detectada: trecho sem áudio ou tela de descanso/Hollyland.",
                            1.0,
                            tentativas=tentativas,
                        )
                    elif "processing this video" in texto_erro.lower() or "check back later" in texto_erro.lower():
                        espera_s = min(600, 60 * tentativas)
                        proxima = time.time() + espera_s
                        acervo_local.salvar_estado(
                            youtube_id,
                            "aguardando_youtube",
                            f"O YouTube ainda está codificando a live. Nova checagem automática em {max(1, int(espera_s // 60))} min (tentativa {tentativas}/{max_tentativas}).",
                            None,
                            tentativas=tentativas,
                            max_tentativas=max_tentativas,
                            proxima_tentativa=proxima,
                        )
                    else:
                        # Falhas temporárias (Gemini, oscilação de rede, 503, 429)
                        if tentativas <= max_tentativas:
                            espera_s = min(600, 30 * (2 ** (tentativas - 1)))
                            proxima = time.time() + espera_s
                            resumo = "instabilidade do Gemini" if "gemini" in texto_erro.lower() else "oscilação temporária"
                            acervo_local.salvar_estado(
                                youtube_id,
                                "aguardando_retentativa",
                                f"{resumo.capitalize()}. Nova tentativa automática em {max(1, int(espera_s // 60))} min (tentativa {tentativas}/{max_tentativas}).",
                                None,
                                tentativas=tentativas,
                                max_tentativas=max_tentativas,
                                proxima_tentativa=proxima,
                                detalhe=traceback.format_exc()[-2000:],
                            )
                        else:
                            acervo_local.salvar_estado(
                                youtube_id,
                                "falhou",
                                f"Falhou após {max_tentativas} tentativas: {texto_erro[:200]}. Clique em 'Processar de novo'.",
                                None,
                                tentativas=tentativas,
                                max_tentativas=max_tentativas,
                                detalhe=traceback.format_exc()[-2000:],
                            )
                except Exception:
                    # Nem o estado de falha gravou: registra no console e mantém a fila viva para os próximos links.
                    traceback.print_exc()
            finally:
                with self._trava:
                    self._ativos.discard(youtube_id)
                self._fila.task_done()

    def _processar(self, youtube_id, refazer):
        pasta = acervo_local.pasta_do_video(youtube_id)

        def estado(etapa, mensagem, progresso):
            acervo_local.salvar_estado(youtube_id, etapa, mensagem, round(progresso, 3))

        frases = None if refazer else acervo_local.ler(youtube_id, "frases.json")
        if frases is None:
            estado("legenda", "Buscando a legenda automática do YouTube", 0.02)
            info, caminho = legendas.baixar_legenda(youtube_id, pasta)
            acervo_local.salvar(youtube_id, "info.json", info)
            if caminho:
                palavras = legendas.palavras_do_json3(caminho)
                origem = "legenda automática do YouTube"
            else:
                estado("transcrevendo", "O YouTube ainda não tem legenda: baixando o áudio", 0.04)
                audio = transcricao_local.baixar_audio(youtube_id, pasta)
                palavras = transcricao_local.transcrever(
                    audio,
                    ao_progredir=lambda p: estado("transcrevendo", f"Transcrevendo no computador… {p:.0%}", 0.05 + 0.45 * p),
                )
                origem = "transcrição local (Whisper)"
            lista = legendas.frases_das_palavras(palavras)
            if not lista:
                raise RuntimeError("a transcrição veio vazia")
            acervo_local.salvar(youtube_id, "palavras.json", palavras)
            frases = {"origem": origem, "frases": lista}
            acervo_local.salvar(youtube_id, "frases.json", frases)

        info = acervo_local.ler(youtube_id, "info.json", {})
        contexto = (
            f"VÍDEO: {info.get('titulo', '')}\nCANAL: {info.get('canal', '')}\n"
            f"PUBLICADO: {info.get('publicado_em', '')}\nDESCRIÇÃO: {(info.get('descricao') or '')[:600]}"
        )
        estado("blocos", "Dividindo em blocos com o Gemini", 0.5)
        blocos, ignorados, modelos = blocador.dividir(
            frases["frases"], contexto,
            ao_progredir=lambda p: estado("blocos", f"Dividindo em blocos com o Gemini… {p:.0%}", 0.5 + 0.5 * p),
        )
        acervo_local.salvar(youtube_id, "blocos.json", {
            "blocos": blocos, "ignorados": ignorados, "modelos": modelos, "gerado_em": time.time(),
            "origem_frases": frases["origem"],
        })
        estado("pronto", f"{len(blocos)} blocos · {frases['origem']} · {', '.join(modelos)}", 1.0)
        if self.ao_concluir_blocagem:
            try:
                self.ao_concluir_blocagem(youtube_id, info, blocos, frases)
            except Exception as erro:
                print(f"Aviso: erro no pós-processamento de cortes automáticos para {youtube_id}: {erro}")
