"""Sincronizador em tempo real de playlists do YouTube (Lives gravadas).

Monitora continuamente uma playlist do YouTube em segundo plano, detecta novos vídeos
adicionados e dispara automaticamente a extração de legendas e blocagem via Gemini
(mesmo formato editorial do Chub).
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse

import yt_dlp

from . import acervo_local, config

log = logging.getLogger("indomavel.sincronizador_playlist")

ARQUIVO_REGISTRO = "playlist_aovivo.json"
INTERVALO_PADRAO_S = 60
ID_YOUTUBE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extrair_id_playlist(url_ou_id):
    if not url_ou_id:
        return ""
    texto = str(url_ou_id).strip()
    if re.match(r"^[A-Za-z0-9_-]{10,40}$", texto) and not texto.startswith("http"):
        return texto
    try:
        analisado = urllib.parse.urlparse(texto)
        parametros = urllib.parse.parse_qs(analisado.query)
        if "list" in parametros:
            return parametros["list"][0]
    except Exception:
        pass
    return texto


def _caminho_registro():
    os.makedirs(config.PASTA_DADOS, exist_ok=True)
    return os.path.join(config.PASTA_DADOS, ARQUIVO_REGISTRO)


def ler_registro():
    caminho = _caminho_registro()
    if not os.path.exists(caminho):
        return {
            "url": config.valor("PLAYLIST_AOVIVO_URL", "https://youtube.com/playlist?list=PLCyyYEMeI6VE"),
            "playlist_id": "PLCyyYEMeI6VE",
            "titulo": "Lives Gravadas",
            "ultima_checagem": None,
            "sincronizando": False,
            "erro": None,
            "videos": [],
        }
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "url": "https://youtube.com/playlist?list=PLCyyYEMeI6VE",
            "playlist_id": "PLCyyYEMeI6VE",
            "titulo": "Lives Gravadas",
            "ultima_checagem": None,
            "sincronizando": False,
            "erro": None,
            "videos": [],
        }


def salvar_registro(dados):
    caminho = _caminho_registro()
    temp = caminho + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(temp, caminho)


class SincronizadorPlaylist:
    def __init__(self, fila_links=None, intervalo_s=INTERVALO_PADRAO_S):
        self.fila_links = fila_links
        self.intervalo_s = intervalo_s
        self._trava = threading.Lock()
        self._sincronizando = False
        self._thread = None
        self._ativo = True

    def iniciar(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="sincronizador-playlist", daemon=True)
        self._thread.start()
        log.info("Sincronizador de playlist iniciado em segundo plano (intervalo: %ds).", self.intervalo_s)

    def parar(self):
        self._ativo = False

    def status(self):
        registro = ler_registro()
        return {
            "url": registro.get("url"),
            "playlist_id": registro.get("playlist_id"),
            "titulo": registro.get("titulo") or "Lives Gravadas",
            "ultima_checagem": registro.get("ultima_checagem"),
            "sincronizando": self._sincronizando or registro.get("sincronizando", False),
            "erro": registro.get("erro"),
            "total_videos": len(registro.get("videos", [])),
            "intervalo_s": self.intervalo_s,
        }

    def sincronizar_agora(self):
        """Dispara uma sincronização imediata em thread separada se não houver outra em curso."""
        with self._trava:
            if self._sincronizando:
                return {"ok": False, "mensagem": "Sincronização já em andamento"}
        t = threading.Thread(target=self._executar_sincronizacao, name="sync-playlist-imediata", daemon=True)
        t.start()
        return {"ok": True, "mensagem": "Sincronização iniciada"}

    def _loop(self):
        # Pequena espera inicial antes da primeira checagem para dar tempo do servidor subir
        time.sleep(3)
        while self._ativo:
            try:
                self._executar_sincronizacao()
            except Exception as e:
                log.warning("Erro no loop de sincronização da playlist: %s", e)
            for _ in range(max(1, int(self.intervalo_s))):
                if not self._ativo:
                    break
                time.sleep(1)

    def _executar_sincronizacao(self):
        from . import cortador_automatico
        if not cortador_automatico.obter_modo_automatico():
            return

        with self._trava:
            if self._sincronizando:
                return
            self._sincronizando = True

        registro = ler_registro()
        url = registro.get("url") or "https://youtube.com/playlist?list=PLCyyYEMeI6VE"
        registro["sincronizando"] = True
        registro["erro"] = None
        salvar_registro(registro)

        try:
            ydl_opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }
            if config.NODE:
                ydl_opts["js_runtimes"] = {"node": {"path": config.NODE}}

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            titulo_playlist = (info.get("title") or "Lives Gravadas").strip()
            entries = info.get("entries") or []
            registro["titulo"] = titulo_playlist
            registro["playlist_id"] = info.get("id") or extrair_id_playlist(url)

            videos_existentes = {v["youtube_id"]: v for v in registro.get("videos", [])}
            novos_enfileirados = 0

            for entry in entries:
                if not entry:
                    continue
                yid = entry.get("id")
                if not yid or not ID_YOUTUBE.match(yid):
                    continue

                tit = (entry.get("title") or f"Live {yid}").strip()
                dur = entry.get("duration") or 0

                item_video = videos_existentes.get(yid)
                if not item_video:
                    item_video = {
                        "youtube_id": yid,
                        "titulo": tit,
                        "duracao_s": dur,
                        "adicionado_em": time.time(),
                    }
                    videos_existentes[yid] = item_video
                else:
                    if tit and not tit.startswith("Live "):
                        item_video["titulo"] = tit
                    if dur:
                        item_video["duracao_s"] = dur

                # Verifica se o vídeo já tem info local e se já foi blocado
                info_local = acervo_local.ler(yid, "info.json")
                if not info_local:
                    acervo_local.salvar(yid, "info.json", {
                        "youtube_id": yid,
                        "titulo": tit,
                        "duracao_s": dur,
                        "playlist": registro["playlist_id"],
                        "canal": "Playlist Ao Vivo",
                    })
                elif not info_local.get("playlist"):
                    info_local["playlist"] = registro["playlist_id"]
                    acervo_local.salvar(yid, "info.json", info_local)

                estado_local = acervo_local.ler(yid, "estado.json", {})
                estado_nome = estado_local.get("estado")

                # Auto-recuperação e retentativas automáticas inteligentes
                agora = time.time()
                deve_enfileirar = False

                if estado_nome is None or estado_nome == "na_fila":
                    deve_enfileirar = True
                elif estado_nome in ("aguardando_retentativa", "aguardando_youtube"):
                    proxima = float(estado_local.get("proxima_tentativa") or 0)
                    if agora >= proxima:
                        deve_enfileirar = True
                elif estado_nome in ("blocos", "legenda", "transcrevendo"):
                    # Detecta processos órfãos interrompidos por fechamento ou reinicialização anterior do servidor
                    atualizado_em = float(estado_local.get("atualizado_em") or 0)
                    if (agora - atualizado_em) > 600:
                        deve_enfileirar = True
                        log.info("Recuperando vídeo órfão parado no estado '%s': %s (%s)", estado_nome, tit, yid)
                elif estado_nome == "falhou":
                    # Recupera automaticamente falhas anteriores que foram causadas por Gemini, timeout ou YouTube
                    msg = (estado_local.get("mensagem") or "").lower()
                    if any(rec in msg for rec in ("gemini", "processing this video", "timeout", "503", "429")):
                        proxima = float(estado_local.get("proxima_tentativa") or 0)
                        if agora >= proxima:
                            deve_enfileirar = True

                ativos = self.fila_links.ativos() if self.fila_links else set()
                if deve_enfileirar and yid not in ativos and self.fila_links:
                    adicionado = self.fila_links.adicionar(yid)
                    if adicionado:
                        novos_enfileirados += 1
                        log.info("Vídeo enfileirado para processamento/retentativa: %s (%s)", tit, yid)

            # Ordena com os mais recentes primeiro
            lista_ordenada = sorted(
                videos_existentes.values(),
                key=lambda x: x.get("adicionado_em", 0),
                reverse=True
            )
            registro["videos"] = lista_ordenada
            registro["ultima_checagem"] = time.time()
            registro["sincronizando"] = False
            registro["erro"] = None
            salvar_registro(registro)
            log.info("Sincronização da playlist concluída: %d vídeos catalogados (%d novos enfileirados).",
                     len(lista_ordenada), novos_enfileirados)

        except Exception as erro:
            msg_erro = str(erro)
            log.warning("Falha ao sincronizar playlist %s: %s", url, msg_erro)
            registro["sincronizando"] = False
            registro["erro"] = msg_erro
            registro["ultima_checagem"] = time.time()
            salvar_registro(registro)
        finally:
            with self._trava:
                self._sincronizando = False

    def listar_videos(self):
        """Retorna lista de vídeos da playlist com estado completo para exibição no frontend."""
        registro = ler_registro()
        videos_resumo = registro.get("videos", [])
        resultado = []

        for item in videos_resumo:
            yid = item["youtube_id"]
            info = acervo_local.ler(yid, "info.json", {})
            estado = acervo_local.ler(yid, "estado.json", {})
            blocos = acervo_local.ler(yid, "blocos.json")

            qtd_blocos = len(blocos["blocos"]) if blocos and "blocos" in blocos else 0
            qtd_prontos = 0
            if blocos and "blocos" in blocos:
                for b in blocos["blocos"]:
                    dur = float(b.get("duracao", 0) or (float(b.get("fim", 0)) - float(b.get("inicio", 0))))
                    renan = bool(b.get("renan_falando", True))
                    contexto = bool(b.get("precisa_contexto", False))
                    if renan and not contexto and 20.0 <= dur <= 120.0:
                        qtd_prontos += 1

            est_nome = estado.get("estado")
            # Se não há estado mas blocos existem, o vídeo está pronto
            if not est_nome and qtd_blocos > 0:
                est_nome = "pronto"
            elif not est_nome:
                est_nome = "aguardando"

            cortes = acervo_local.ler(yid, "cortes_automaticos.json")

            resultado.append({
                "youtube_id": yid,
                "titulo": info.get("titulo") or item.get("titulo") or f"Vídeo {yid}",
                "publicado_em": info.get("publicado_em") or item.get("adicionado_em"),
                "duracao_s": info.get("duracao_s") or item.get("duracao_s") or 0,
                "fontes": "🔴 Indomável",
                "tem_transcricao": os.path.exists(os.path.join(acervo_local.pasta_do_video(yid), "frases.json")),
                "blocos": qtd_blocos,
                "blocos_qa": qtd_prontos,
                "cortes": cortes,
                "origem": "local",
                "estado": est_nome,
                "mensagem": estado.get("mensagem"),
                "progresso": estado.get("progresso"),
                "atualizado_em": estado.get("atualizado_em") or item.get("adicionado_em") or 0,
            })

        # Incorporar vídeos detectados diretamente na pasta do Google Drive
        try:
            from . import google_drive
            drive_videos = google_drive.listar_videos_pasta_drive()
            for f in drive_videos:
                fid = f.get("id")
                nome = f.get("name", "Vídeo Drive")
                if any(v["youtube_id"] == fid for v in resultado):
                    continue
                resultado.append({
                    "youtube_id": fid,
                    "titulo": nome,
                    "publicado_em": f.get("createdTime"),
                    "duracao_s": 0,
                    "fontes": "☁️ Google Drive",
                    "tem_transcricao": False,
                    "blocos": 0,
                    "blocos_qa": 0,
                    "cortes": None,
                    "origem": "drive",
                    "estado": "pronto",
                    "mensagem": "Vídeo no Google Drive",
                    "progresso": 1.0,
                    "atualizado_em": time.time(),
                })
        except Exception as e:
            log.debug("Aviso ao carregar vídeos do Drive para a lista: %s", e)

        return resultado
