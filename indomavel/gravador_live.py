"""Gravador, monitor contínuo 24/7 e fatiador automático de transmissões ao vivo do YouTube.
Unifica o sistema de gravação Zema e o motor de cortes IA Indomável em um único processo.

Funcionalidades:
- Monitoramento contínuo 24/7 de canais e URLs de transmissão ao vivo do YouTube.
- Gravação contínua em blocos de 30 minutos em MP4 sem perda (-c copy) usando Streamlink + FFmpeg pipe.
- Modo Catch-up DVR: inicia a gravação capturando o buffer transmitido ou vai direto ao live edge.
- Função "Cortar Agora": força o fechamento imediato da fatia atual e dispara a esteira de cortes.
- Upload automático do vídeo bruto para a pasta 'Live 24 hrs' no Google Drive (1Rzc1NQ0RDzeId6L_7O8QiP1P13bTooFl).
- Esteira de cortes automáticos (Whisper + Gemini + FFmpeg) gerando as 3 modalidades no Google Drive:
  1. Cortes com headline (FernandoXX_sem_legenda.mp4 + headlines.txt)
  2. Cortes originais (FernandoXX_cru.mp4 + .srt)
  3. Cortes com headline e legenda (FernandoXX_com_legenda.mp4)
- Análise de áudio com Gemini Flash para capítulos e resumos.
- Máquina de estados e persistência resiliente em SQLite com histórico permanente.
- Retenção de disco de 48 horas após upload confirmado.
"""

from collections import deque
import contextlib
import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request
import urllib.error

import yt_dlp

from . import config, cortador_drive, gemini, google_drive
from .cluster_lock import cluster

log = logging.getLogger("indomavel.gravador_live")

DB_NOME = "gravador_lives.sqlite3"
RETENCAO_SEGUNDOS = 48 * 3600  # 48 horas de segurança


class RingLogHandler(logging.Handler):
    """Armazena em memória as últimas mensagens de log para exibição em tempo real na interface."""

    def __init__(self, capacity: int = 100):
        super().__init__()
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._lock:
                self.buffer.append({
                    "timestamp": datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "logger": record.name
                })
        except Exception:
            pass

    def get_logs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.buffer)


# Instância global do buffer de logs
memoria_logs = RingLogHandler(capacity=100)
formatter_mem = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
memoria_logs.setFormatter(formatter_mem)
log.addHandler(memoria_logs)
logging.getLogger("indomavel.cortador_drive").addHandler(memoria_logs)


def _caminho_db() -> str:
    os.makedirs(config.PASTA_DADOS, exist_ok=True)
    return os.path.join(config.PASTA_DADOS, DB_NOME)


@contextlib.contextmanager
def _conectar():
    conn = sqlite3.connect(_caminho_db(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
    finally:
        conn.close()


def _migrar_colunas_se_necessario(conn):
    try:
        colunas = {r[1] for r in conn.execute("PRAGMA table_info(partes_live)").fetchall()}
        novas = {
            "drive_file_id": "TEXT",
            "drive_status": "TEXT DEFAULT 'pendente'",
            "drive_url": "TEXT",
            "cortes_status": "TEXT DEFAULT 'pendente'",
            "cortes_total": "INTEGER DEFAULT 0",
            "cortes_detalhes": "TEXT",
        }
        for col, tipo in novas.items():
            if col not in colunas:
                conn.execute(f"ALTER TABLE partes_live ADD COLUMN {col} {tipo}")
        conn.commit()
    except Exception as e:
        log.debug("Aviso de migração de schema partes_live: %s", e)


def iniciar_banco():
    with _conectar() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessoes_live (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_id TEXT NOT NULL,
            url_live TEXT NOT NULL,
            titulo TEXT,
            playlist_id TEXT,
            pasta_destino TEXT NOT NULL,
            modo_dvr INTEGER DEFAULT 1,
            duracao_chunk_s INTEGER DEFAULT 1800,
            estado TEXT DEFAULT 'gravando',
            pid_processo INTEGER,
            mensagem TEXT,
            criado_em REAL,
            atualizado_em REAL
        );

        CREATE TABLE IF NOT EXISTS partes_live (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sessao_id INTEGER NOT NULL,
            youtube_id TEXT NOT NULL,
            numero_parte INTEGER NOT NULL,
            nome_arquivo TEXT NOT NULL,
            caminho_video TEXT NOT NULL,
            caminho_audio TEXT,
            inicio_s REAL DEFAULT 0,
            fim_s REAL DEFAULT 0,
            duracao_s REAL DEFAULT 0,
            tamanho_bytes INTEGER DEFAULT 0,
            estado TEXT DEFAULT 'gravando',
            drive_file_id TEXT,
            drive_status TEXT DEFAULT 'pendente',
            drive_url TEXT,
            cortes_status TEXT DEFAULT 'pendente',
            cortes_total INTEGER DEFAULT 0,
            cortes_detalhes TEXT,
            resumo TEXT,
            capitulos TEXT,
            transcricao TEXT,
            youtube_video_id TEXT,
            youtube_url TEXT,
            erro_mensagem TEXT,
            criado_em REAL,
            atualizado_em REAL,
            enviado_em REAL,
            limpo_disco INTEGER DEFAULT 0,
            FOREIGN KEY (sessao_id) REFERENCES sessoes_live(id)
        );

        CREATE INDEX IF NOT EXISTS idx_partes_sessao ON partes_live(sessao_id);
        CREATE INDEX IF NOT EXISTS idx_partes_estado ON partes_live(estado);

        CREATE TABLE IF NOT EXISTS config_gravador (
            chave TEXT PRIMARY KEY,
            valor TEXT
        );
        """)
        _migrar_colunas_se_necessario(conn)


def extrair_id(url_ou_id: str) -> str:
    texto = (url_ou_id or "").strip()
    if re.match(r"^[A-Za-z0-9_-]{11}$", texto):
        return texto
    padroes = [
        r"(?:v=|\/embed\/|\/shorts\/|youtu\.be\/|\/live\/)([A-Za-z0-9_-]{11})",
        r"youtube\.com\/live\/([A-Za-z0-9_-]{11})",
    ]
    for padrao in padroes:
        m = re.search(padrao, texto)
        if m:
            return m.group(1)
    return texto


def normalize_youtube_url(url: str) -> str:
    m = re.search(r"youtube\.com/live/([a-zA-Z0-9_-]{11})", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url


def resolve_youtube_stream_info(url_ou_id: str, cookies_path: Optional[str] = None, timeout: int = 15) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Resolve URL ou ID do YouTube utilizando yt-dlp android player client ou web parser.
    Retorna (is_live, video_id, title, hls_url).
    """
    youtube_id = extrair_id(url_ou_id)
    clean_url = f"https://www.youtube.com/watch?v={youtube_id}" if len(youtube_id) == 11 else (url_ou_id or "").strip()
    if not clean_url:
        return False, None, "Live Stream", None

    cookies = cookies_path or getattr(config, "COOKIES_TXT", None)

    # 1. yt-dlp android player client
    cmd = [
        "yt-dlp",
        "--force-ipv4",
        "--extractor-args", "youtube:player_client=android",
        "--print", "%(id)s",
        "--print", "%(title)s",
        "-g",
        clean_url
    ]
    if cookies and os.path.exists(cookies):
        cmd.extend(["--cookies", str(cookies)])

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        if res.returncode == 0 and res.stdout:
            lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
            if len(lines) >= 3:
                vid_id = lines[0]
                title = lines[1]
                hls_url = lines[2]
                return True, vid_id, title, hls_url
            elif len(lines) == 1 and lines[0].startswith("http"):
                return True, youtube_id, "Live Stream", lines[0]
    except Exception as e:
        log.debug("Aviso yt-dlp android resolver: %s", e)

    # 2. Fallback urllib com cookies
    try:
        import http.cookiejar
        cj = http.cookiejar.MozillaCookieJar()
        if cookies and os.path.exists(cookies):
            try:
                cj.load(cookies, ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        req = urllib.request.Request(clean_url, headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});(?:var\s|const\s|</script>)", html, re.DOTALL)
        if not match:
            match = re.search(r"var\s+ytInitialPlayerResponse\s*=\s*({.+?});", html)
        if match:
            player_data = json.loads(match.group(1))
            playability = player_data.get("playabilityStatus", {})
            status = playability.get("status", "")
            video_details = player_data.get("videoDetails", {})
            video_id = video_details.get("videoId")
            title = video_details.get("title")
            is_live = video_details.get("isLive", False) or video_details.get("isLiveContent", False)
            streaming_data = player_data.get("streamingData", {})
            hls_url = streaming_data.get("hlsManifestUrl")
            if not is_live and "liveStreamability" in playability:
                is_live = True
            is_online = (status == "OK") and (is_live or bool(hls_url))
            return is_online, video_id, title or "Live Stream", hls_url
    except Exception as e:
        log.debug("Aviso urllib resolver: %s", e)

    return False, None, "Live Stream", None


def check_streamlink_online(stream_url: str, streamlink_path: Optional[str] = None, quality: str = "best", timeout_seconds: int = 15) -> bool:
    """Verifica se a live stream está online usando yt-dlp ou Streamlink."""
    if any(domain in stream_url for domain in ("youtube.com", "youtu.be", "@")):
        is_live, _, _, _ = resolve_youtube_stream_info(stream_url)
        if is_live:
            return True

    sl = streamlink_path or getattr(config, "STREAMLINK", None) or shutil.which("streamlink")
    if not sl:
        return False

    cmd = [sl, "--ipv4"]
    cookies = getattr(config, "COOKIES_TXT", None)
    if cookies and os.path.exists(cookies):
        cmd.extend(["--http-cookies-file", str(cookies)])
    cmd.extend(["--stream-url", stream_url, quality])
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.strip().splitlines():
                if line.strip().startswith(("http://", "https://")):
                    return True
    except Exception:
        pass
    return False


def obter_informacoes_live(url_ou_id: str) -> Dict[str, Any]:
    """Extrai metadados e URLs HLS da transmissão ao vivo."""
    is_live, vid_id, title, hls_url = resolve_youtube_stream_info(url_ou_id)
    if hls_url:
        return {
            "youtube_id": vid_id or extrair_id(url_ou_id),
            "url_origem": url_ou_id,
            "titulo": title or "Live Stream",
            "is_live": is_live,
            "url_video": hls_url,
            "url_audio": None,
            "altura": 1080,
        }

    youtube_id = extrair_id(url_ou_id)
    url = f"https://www.youtube.com/watch?v={youtube_id}" if len(youtube_id) == 11 else url_ou_id

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
    }
    if config.NODE:
        ydl_opts["js_runtimes"] = {"node": {"path": config.NODE}}
    if config.FFMPEG:
        ydl_opts["ffmpeg_location"] = config.FFMPEG

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    titulo = info.get("title") or f"Live {youtube_id}"
    is_live_dlp = bool(info.get("is_live") or info.get("live_status") == "is_live")
    formats = info.get("formats") or []

    videos_hls = [
        f for f in formats
        if f.get("url") and f.get("resolution") != "audio only" and f.get("vcodec") not in (None, "none")
    ]
    audios_hls = [
        f for f in formats
        if f.get("url") and (f.get("resolution") == "audio only" or (f.get("vcodec") in (None, "none") and f.get("height") in (None, 0)))
    ]
    if not videos_hls:
        videos_hls = [f for f in formats if f.get("url")]

    def pontuacao_video(f):
        altura = f.get("height") or 0
        codec = f.get("vcodec") or ""
        avc = 1 if "avc" in codec.lower() else 0
        return (altura <= 1080, altura, avc)

    def pontuacao_audio(f):
        fid = f.get("format_id", "")
        num = int(fid) if str(fid).isdigit() else 0
        return (f.get("language_preference") or 0, num, f.get("abr") or 0)

    melhor_video = max(videos_hls, key=pontuacao_video) if videos_hls else None
    melhor_audio = max(audios_hls, key=pontuacao_audio) if audios_hls else None

    return {
        "youtube_id": youtube_id,
        "url_origem": url,
        "titulo": titulo,
        "is_live": is_live_dlp,
        "url_video": melhor_video["url"] if melhor_video else None,
        "url_audio": melhor_audio["url"] if melhor_audio else None,
        "altura": melhor_video.get("height") if melhor_video else None,
    }


class GerenciadorGravacaoLive:
    """
    Gerencia o ciclo de vida completo da gravação, fatiamento, monitoramento 24/7,
    upload no Google Drive e esteira de cortes IA no Indomável.
    """

    def __init__(self):
        iniciar_banco()
        self._processo_ffmpeg = None
        self._processo_streamlink = None
        self._sessao_ativa_id = None
        self._thread_monitor = None
        self._thread_ia = None
        self._thread_monitor_247 = None
        self._parar_evento = threading.Event()
        self._corte_solicitado = threading.Event()
        self._trava = threading.Lock()

        # Telemetria em tempo real
        # Regra Crítica: Safe Boot - o programa NUNCA inicia gravando ou monitorando sozinho
        self._monitor_247_ativo = False
        self._url_monitorada = getattr(config, "URL_LIVE_PADRAO", "https://www.youtube.com/@PartidoMissao/live")
        self._qualidade = "best"
        self._duracao_chunk_s = 1800
        self._dvr = True
        self._auto_cortar = True
        self._is_live_online = False
        self._stream_title = "Live Stream"
        self._session_start_time: Optional[float] = None
        self._chunk_start_time: Optional[float] = None
        self._bloco_atual_numero: int = 1

        self._carregar_config_persistida()
        # Força Standby Seguro na inicialização para proteger múltiplos notebooks
        self._monitor_247_ativo = False
        self.recuperar_ao_iniciar()

        # Inicia loop de supervisão contínua 24/7 (em modo Standby até ativação pelo usuário)
        self._iniciar_thread_monitor_247()

    def _carregar_config_persistida(self):
        try:
            with _conectar() as conn:
                rows = conn.execute("SELECT chave, valor FROM config_gravador").fetchall()
                cfg = {r["chave"]: r["valor"] for r in rows}
                if "url_live" in cfg and cfg["url_live"].strip():
                    self._url_monitorada = cfg["url_live"].strip()
                if "duracao_chunk_s" in cfg:
                    self._duracao_chunk_s = int(cfg["duracao_chunk_s"])
                if "qualidade" in cfg:
                    self._qualidade = cfg["qualidade"]
                if "dvr" in cfg:
                    self._dvr = cfg["dvr"] == "1"
                if "auto_cortar" in cfg:
                    self._auto_cortar = cfg["auto_cortar"] == "1"
        except Exception as e:
            log.debug("Aviso ao carregar config_gravador: %s", e)

    def salvar_config(self, url: Optional[str] = None, duracao_chunk_s: Optional[int] = None,
                      qualidade: Optional[str] = None, dvr: Optional[bool] = None,
                      auto_cortar: Optional[bool] = None, monitor_ativo: Optional[bool] = None):
        with self._trava:
            if url is not None:
                self._url_monitorada = url.strip()
            if duracao_chunk_s is not None:
                self._duracao_chunk_s = duracao_chunk_s
            if qualidade is not None:
                self._qualidade = qualidade
            if dvr is not None:
                self._dvr = bool(dvr)
            if auto_cortar is not None:
                self._auto_cortar = bool(auto_cortar)
            if monitor_ativo is not None:
                self._monitor_247_ativo = bool(monitor_ativo)

            try:
                with _conectar() as conn:
                    conn.executemany("""
                        INSERT INTO config_gravador (chave, valor) VALUES (?, ?)
                        ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor
                    """, [
                        ("url_live", self._url_monitorada),
                        ("duracao_chunk_s", str(self._duracao_chunk_s)),
                        ("qualidade", self._qualidade),
                        ("dvr", "1" if self._dvr else "0"),
                        ("auto_cortar", "1" if self._auto_cortar else "0"),
                        ("monitor_ativo", "1" if self._monitor_247_ativo else "0"),
                    ])
                    conn.commit()
            except Exception as e:
                log.warning("Erro ao salvar config_gravador: %s", e)

    def obter_status(self) -> Dict[str, Any]:
        with _conectar() as conn:
            sessao = None
            if self._sessao_ativa_id:
                row = conn.execute("SELECT * FROM sessoes_live WHERE id = ?", (self._sessao_ativa_id,)).fetchone()
                if row:
                    sessao = dict(row)
            if not sessao:
                row = conn.execute("SELECT * FROM sessoes_live WHERE estado = 'gravando' ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    sessao = dict(row)

            partes = []
            if sessao:
                rows = conn.execute("SELECT * FROM partes_live WHERE sessao_id = ? ORDER BY numero_parte DESC", (sessao["id"],)).fetchall()
                partes = [dict(r) for r in rows]
            else:
                rows = conn.execute("SELECT * FROM partes_live ORDER BY id DESC LIMIT 20").fetchall()
                partes = [dict(r) for r in rows]

            sessoes_recentes = conn.execute("SELECT * FROM sessoes_live ORDER BY id DESC LIMIT 8").fetchall()

        gravando = bool(self._processo_ffmpeg and self._processo_ffmpeg.poll() is None)
        now = time.time()
        tempo_sessao = round(now - self._session_start_time, 1) if (gravando and self._session_start_time) else 0.0
        tempo_bloco = round(now - self._chunk_start_time, 1) if (gravando and self._chunk_start_time) else 0.0

        drive_conectado = False
        try:
            token, _ = google_drive.obter_token_acesso()
            drive_conectado = bool(token)
        except Exception:
            pass

        return {
            "monitor_ativo": self._monitor_247_ativo,
            "online": self._is_live_online,
            "gravando": gravando,
            "url": self._url_monitorada,
            "titulo": self._stream_title,
            "duracao_total_s": tempo_sessao,
            "duracao_chunk_s": self._duracao_chunk_s,
            "bloco_atual_numero": self._bloco_atual_numero if gravando else 0,
            "bloco_atual_segundos": tempo_bloco,
            "auto_cortar": self._auto_cortar,
            "qualidade": self._qualidade,
            "dvr": self._dvr,
            "pasta_destino_cortes": cortador_drive.PASTA_DESTINO_CORTES_PADRAO,
            "pasta_fonte_raw": config.PASTA_FONTE_LIVES_PADRAO,
            "drive_conectado": drive_conectado,
            "cluster": cluster.get_status_dict(),
            "cluster_role": cluster.role_name,
            "is_leader": cluster.is_leader,
            "sessao_ativa": sessao,
            "partes": partes,
            "sessoes_recentes": [dict(s) for s in sessoes_recentes],
            "logs": memoria_logs.get_logs()[-30:],
        }

    def obter_logs(self, limite: int = 50) -> List[Dict[str, Any]]:
        return memoria_logs.get_logs()[-limite:]

    def obter_todas_partes(self, limite: int = 100) -> List[Dict[str, Any]]:
        """Retorna histórico completo de fatias gravadas com metadados para listagem no painel."""
        with _conectar() as conn:
            rows = conn.execute("""
                SELECT p.*, s.titulo as sessao_titulo, s.url_live, s.criado_em as sessao_criado_em
                FROM partes_live p
                LEFT JOIN sessoes_live s ON p.sessao_id = s.id
                ORDER BY p.id DESC
                LIMIT ?
            """, (limite,)).fetchall()
            return [dict(r) for r in rows]

    def iniciar_gravacao(self, url_ou_id: Optional[str] = None, playlist_id: Optional[str] = None,
                         dvr: bool = True, duracao_chunk_s: int = 1800, pasta_base: Optional[str] = None,
                         qualidade: Optional[str] = None, auto_cortar: bool = True) -> Dict[str, Any]:
        with self._trava:
            if self._processo_ffmpeg and self._processo_ffmpeg.poll() is None:
                raise RuntimeError("Já existe uma gravação de live em andamento.")

            url_alvo = (url_ou_id or self._url_monitorada).strip()
            if not url_alvo:
                raise RuntimeError("Informe a URL ou canal da transmissão ao vivo.")

            info = obter_informacoes_live(url_alvo)
            youtube_id = info.get("youtube_id") or extrair_id(url_alvo)
            titulo = info.get("titulo") or f"Live {youtube_id}"
            self._stream_title = titulo

            # Trava de Segurança Multi-Notebook: Apenas o LÍDER pode gravar
            if not cluster.evaluate_leadership(titulo):
                leader_host = cluster.get_status_dict().get("leader_hostname", "outro notebook")
                log.warning("[Cluster] Gravação bloqueada. Máquina '%s' já é o Líder ativo no Google Drive.", leader_host)
                raise RuntimeError(f"Gravação bloqueada: O notebook '{leader_host}' já está gravando ativamente como Líder.")

            self._is_live_online = True
            self._duracao_chunk_s = duracao_chunk_s
            self._dvr = dvr
            self._qualidade = qualidade or self._qualidade
            self._auto_cortar = auto_cortar

            timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            nome_pasta = f"live_{youtube_id}_{timestamp_str}"
            base_dir = pasta_base or config.PASTA_LIVES
            pasta_destino = os.path.join(base_dir, nome_pasta)
            os.makedirs(pasta_destino, exist_ok=True)

            agora = time.time()
            with _conectar() as conn:
                cursor = conn.execute("""
                    INSERT INTO sessoes_live (
                        youtube_id, url_live, titulo, playlist_id, pasta_destino,
                        modo_dvr, duracao_chunk_s, estado, criado_em, atualizado_em, mensagem
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'gravando', ?, ?, 'Gravando...')
                """, (
                    youtube_id, url_alvo, titulo, playlist_id or "", pasta_destino,
                    1 if dvr else 0, duracao_chunk_s, agora, agora
                ))
                sessao_id = cursor.lastrowid
                conn.commit()

            self._sessao_ativa_id = sessao_id
            self._session_start_time = agora
            self._chunk_start_time = agora
            self._bloco_atual_numero = 1
            self._parar_evento.clear()
            self._corte_solicitado.clear()

            self._iniciar_processos_pipeline(sessao_id, url_alvo, pasta_destino, duracao_chunk_s, dvr, self._qualidade, info)

            # Inicia thread de monitoramento da sessão e watcher de segmentos
            self._thread_monitor = threading.Thread(
                target=self._loop_monitorar,
                args=(sessao_id, pasta_destino, duracao_chunk_s, titulo),
                daemon=True,
                name=f"live-monitor-{sessao_id}"
            )
            self._thread_monitor.start()

            # Inicia thread de processamento e IA
            self._iniciar_thread_ia()

            log.info("🔴 Gravação 24/7 iniciada com sucesso: '%s' (Sessão #%d)", titulo, sessao_id)
            return {"sessao_id": sessao_id, "youtube_id": youtube_id, "titulo": titulo, "pasta": pasta_destino}

    def _iniciar_processos_pipeline(self, sessao_id: int, url_alvo: str, pasta_destino: str,
                                    duracao_chunk_s: int, dvr: bool, qualidade: str, info: Dict[str, Any]):
        """Inicializa pipeline: Streamlink | FFmpeg (prioridade máxima) ou FFmpeg direto (fallback)."""
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        streamlink_bin = getattr(config, "STREAMLINK", None) or shutil.which("streamlink")
        cookies = getattr(config, "COOKIES_TXT", None)

        if streamlink_bin:
            try:
                streamlink_cmd = [
                    streamlink_bin,
                    "--ipv4",
                ]
                if cookies and os.path.exists(cookies):
                    streamlink_cmd.extend(["--http-cookies-file", str(cookies)])
                streamlink_cmd.extend([
                    "--http-header", "User-Agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "--http-header", "Accept-Language=pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                    "--stdout",
                ])
                if dvr:
                    streamlink_cmd.extend(["--hls-live-edge", "2"])
                streamlink_cmd.extend([
                    "--stream-segment-threads", "2",
                    "--retry-streams", "5",
                    "--retry-max", "10",
                    url_alvo,
                    qualidade or "best"
                ])

                ffmpeg_cmd = [
                    config.FFMPEG or "ffmpeg",
                    "-hide_banner",
                    "-loglevel", "warning",
                    "-i", "pipe:0",
                    "-c", "copy",
                    "-f", "segment",
                    "-segment_time", str(duracao_chunk_s),
                    "-segment_format", "mp4",
                    "-segment_format_options", "movflags=+faststart",
                    "-reset_timestamps", "1",
                    "-segment_list", os.path.join(pasta_destino, "segmentos.csv"),
                    "-segment_list_type", "csv",
                    os.path.join(pasta_destino, "parte_%03d.mp4")
                ]

                log.info("Iniciando Streamlink + FFmpeg pipe: %s", " ".join(streamlink_cmd[:6]))
                self._processo_streamlink = subprocess.Popen(
                    streamlink_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags
                )
                self._processo_ffmpeg = subprocess.Popen(
                    ffmpeg_cmd,
                    stdin=self._processo_streamlink.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags
                )
                self._processo_streamlink.stdout.close()

                with _conectar() as conn:
                    conn.execute("UPDATE sessoes_live SET pid_processo = ?, mensagem = 'Gravando (Streamlink+FFmpeg)' WHERE id = ?",
                                 (self._processo_ffmpeg.pid, sessao_id))
                    conn.commit()
                return
            except Exception as err_sl:
                log.warning("Falha ao iniciar Streamlink, tentando fallback FFmpeg direto: %s", err_sl)

        # Fallback: FFmpeg direto
        url_video = info.get("url_video") or url_alvo
        cmd = [config.FFMPEG or "ffmpeg", "-y"]
        if dvr:
            cmd += ["-live_start_index", "0"]
        cmd += ["-i", url_video]
        if info.get("url_audio"):
            if dvr:
                cmd += ["-live_start_index", "0"]
            cmd += ["-i", info["url_audio"]]

        cmd += ["-c:v", "copy"]
        if info.get("url_audio"):
            cmd += ["-c:a", "copy"]
        cmd += [
            "-f", "segment",
            "-segment_time", str(duracao_chunk_s),
            "-segment_format", "mp4",
            "-segment_format_options", "movflags=+faststart",
            "-reset_timestamps", "1",
            "-segment_list", os.path.join(pasta_destino, "segmentos.csv"),
            "-segment_list_type", "csv",
            os.path.join(pasta_destino, "parte_%03d.mp4")
        ]

        log.info("Iniciando FFmpeg direto: %s", " ".join(cmd[:6]))
        self._processo_ffmpeg = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=creationflags
        )
        with _conectar() as conn:
            conn.execute("UPDATE sessoes_live SET pid_processo = ?, mensagem = 'Gravando (FFmpeg direto)' WHERE id = ?",
                         (self._processo_ffmpeg.pid, sessao_id))
            conn.commit()

    def parar_gravacao(self, sessao_id: Optional[int] = None) -> Dict[str, Any]:
        with self._trava:
            alvo_id = sessao_id or self._sessao_ativa_id
            if not alvo_id:
                return {"mensagem": "Nenhuma sessão ativa."}

            self._parar_evento.set()

            if self._processo_streamlink and self._processo_streamlink.poll() is None:
                try:
                    self._processo_streamlink.terminate()
                    self._processo_streamlink.wait(timeout=3)
                except Exception:
                    pass

            if self._processo_ffmpeg and self._processo_ffmpeg.poll() is None:
                try:
                    if self._processo_ffmpeg.stdin and not self._processo_ffmpeg.stdin.closed:
                        self._processo_ffmpeg.stdin.write("q\n")
                        self._processo_ffmpeg.stdin.flush()
                    self._processo_ffmpeg.wait(timeout=6)
                except Exception:
                    try:
                        self._processo_ffmpeg.terminate()
                        self._processo_ffmpeg.wait(timeout=3)
                    except Exception:
                        self._processo_ffmpeg.kill()

            self._processo_streamlink = None
            self._processo_ffmpeg = None

            self._reconciliar_arquivos_da_sessao(alvo_id)

            with _conectar() as conn:
                conn.execute(
                    "UPDATE sessoes_live SET estado = 'concluido', mensagem = 'Gravação finalizada', atualizado_em = ? WHERE id = ?",
                    (time.time(), alvo_id)
                )
                conn.commit()

            if self._sessao_ativa_id == alvo_id:
                self._sessao_ativa_id = None
                self._session_start_time = None
                self._chunk_start_time = None

            try:
                cluster.release_leadership()
            except Exception as e_rel:
                log.debug("Aviso ao liberar cluster lock: %s", e_rel)

            log.info("⏹ Gravação finalizada para a sessão #%d", alvo_id)
            return {"sessao_id": alvo_id, "status": "concluido"}

    def cortar_agora(self) -> Dict[str, Any]:
        """
        Força o fechamento imediato do bloco atual, dispara o upload para o Drive e
        esteira de cortes IA, e continua gravando o próximo bloco sem parar a transmissão.
        """
        with self._trava:
            if not self._processo_ffmpeg or self._processo_ffmpeg.poll() is not None:
                return {"ok": False, "mensagem": "Nenhuma gravação ativa no momento para cortar."}

            log.info("[Gravador 24/7] ✂️ COMANDO CORTAR BLOCO AGORA RECEBIDO!")
            self._corte_solicitado.set()

            if self._processo_streamlink and self._processo_streamlink.poll() is None:
                try:
                    self._processo_streamlink.terminate()
                except Exception:
                    pass
            elif self._processo_ffmpeg and self._processo_ffmpeg.stdin and not self._processo_ffmpeg.stdin.closed:
                try:
                    self._processo_ffmpeg.stdin.write("q\n")
                    self._processo_ffmpeg.stdin.flush()
                except Exception:
                    pass

            return {"ok": True, "mensagem": "Bloco atual cortado com sucesso! Upload e cortes disparados."}

    def _loop_monitorar(self, sessao_id: int, pasta_destino: str, duracao_chunk_s: int, titulo_sessao: str = ""):
        """Monitora novos arquivos de 30 minutos gerados pelo segmenter do FFmpeg."""
        csv_path = os.path.join(pasta_destino, "segmentos.csv")
        partes_registradas = set()

        with _conectar() as conn:
            rows = conn.execute("SELECT nome_arquivo FROM partes_live WHERE sessao_id = ?", (sessao_id,)).fetchall()
            partes_registradas.update(r["nome_arquivo"] for r in rows)

        while not self._parar_evento.is_set():
            # Checa se houve solicitação de corte manual ("Cortar Agora")
            if self._corte_solicitado.is_set():
                time.sleep(2)
                self._reconciliar_arquivos_da_sessao(sessao_id, titulo_sessao)
                self._corte_solicitado.clear()
                self._chunk_start_time = time.time()
                self._bloco_atual_numero += 1

                # Se a sessão ainda está ativa e o processo encerrou por causa do corte, reinicia o pipeline
                if not self._parar_evento.is_set():
                    log.info("[Gravador 24/7] Reiniciando pipeline para próximo bloco (Bloco #%d)...", self._bloco_atual_numero)
                    with self._trava:
                        info = obter_informacoes_live(self._url_monitorada)
                        self._iniciar_processos_pipeline(sessao_id, self._url_monitorada, pasta_destino,
                                                        duracao_chunk_s, self._dvr, self._qualidade, info)

            if self._processo_ffmpeg and self._processo_ffmpeg.poll() is not None:
                # Se não foi corte manual e encerrou inesperadamente, encerra o loop
                if not self._corte_solicitado.is_set():
                    break

            # 1. Verifica arquivo segmentos.csv
            if os.path.exists(csv_path):
                try:
                    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
                        for linha in f:
                            linha = linha.strip()
                            if not linha:
                                continue
                            campos = linha.split(",")
                            nome_arq = os.path.basename(campos[0])
                            if nome_arq not in partes_registradas:
                                caminho_completo = os.path.join(pasta_destino, nome_arq)
                                if os.path.exists(caminho_completo) and os.path.getsize(caminho_completo) > 1024:
                                    inicio_s = float(campos[1]) if len(campos) > 1 else 0.0
                                    fim_s = float(campos[2]) if len(campos) > 2 else inicio_s + duracao_chunk_s
                                    self._cadastrar_parte(sessao_id, nome_arq, caminho_completo, inicio_s, fim_s, titulo_sessao)
                                    partes_registradas.add(nome_arq)
                                    self._chunk_start_time = time.time()
                                    self._bloco_atual_numero += 1
                except Exception as e:
                    log.warning("Erro lendo segmentos.csv: %s", e)

            # 2. Varredura direta de arquivos terminados parte_XXX.mp4
            try:
                arquivos = sorted([
                    f for f in os.listdir(pasta_destino)
                    if f.startswith("parte_") and f.endswith(".mp4")
                ])
                if len(arquivos) >= 2:
                    for arq in arquivos[:-1]:
                        if arq not in partes_registradas:
                            caminho_completo = os.path.join(pasta_destino, arq)
                            idx = int(re.search(r"parte_(\d+)", arq).group(1)) if re.search(r"parte_(\d+)", arq) else 0
                            inicio_s = idx * duracao_chunk_s
                            fim_s = inicio_s + duracao_chunk_s
                            self._cadastrar_parte(sessao_id, arq, caminho_completo, inicio_s, fim_s, titulo_sessao)
                            partes_registradas.add(arq)
                            self._chunk_start_time = time.time()
                            self._bloco_atual_numero += 1
            except Exception:
                pass

            time.sleep(3)

        self._reconciliar_arquivos_da_sessao(sessao_id, titulo_sessao)

    def _cadastrar_parte(self, sessao_id: int, nome_arquivo: str, caminho_video: str,
                         inicio_s: float, fim_s: float, titulo_sessao: str = ""):
        with _conectar() as conn:
            sessao = conn.execute("SELECT youtube_id, titulo FROM sessoes_live WHERE id = ?", (sessao_id,)).fetchone()
            if not sessao:
                return
            youtube_id = sessao["youtube_id"]
            titulo_base = titulo_sessao or sessao["titulo"] or f"Live {youtube_id}"
            num_match = re.search(r"parte_(\d+)", nome_arquivo)
            num_parte = int(num_match.group(1)) + 1 if num_match else 1
            tamanho = os.path.getsize(caminho_video) if os.path.exists(caminho_video) else 0
            duracao = max(0.0, fim_s - inicio_s)
            agora = time.time()

            cursor = conn.execute("""
                INSERT INTO partes_live (
                    sessao_id, youtube_id, numero_parte, nome_arquivo, caminho_video,
                    inicio_s, fim_s, duracao_s, tamanho_bytes, estado, drive_status, cortes_status,
                    criado_em, atualizado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'gravado', 'pendente', 'pendente', ?, ?)
            """, (sessao_id, youtube_id, num_parte, nome_arquivo, caminho_video, inicio_s, fim_s, duracao, tamanho, agora, agora))
            parte_id = cursor.lastrowid
            conn.commit()

        titulo_completo = f"{titulo_base} - Parte {num_parte}"
        log.info("📦 Nova fatia gravada com sucesso: %s (%s, %.1f MB)", titulo_completo, nome_arquivo, tamanho / (1024 * 1024))

        # Dispara em segundo plano: Upload no Google Drive + Esteira de Cortes IA (3 Modalidades)
        self._despachar_upload_e_cortes(parte_id, caminho_video, nome_arquivo, titulo_completo, sessao_id)

    def _reconciliar_arquivos_da_sessao(self, sessao_id: int, titulo_sessao: str = ""):
        with _conectar() as conn:
            sessao = conn.execute("SELECT * FROM sessoes_live WHERE id = ?", (sessao_id,)).fetchone()
            if not sessao:
                return
            pasta = sessao["pasta_destino"]
            duracao_chunk = sessao["duracao_chunk_s"] or 1800
            if not os.path.exists(pasta):
                return

            existentes = {r["nome_arquivo"] for r in conn.execute("SELECT nome_arquivo FROM partes_live WHERE sessao_id = ?", (sessao_id,)).fetchall()}
            arquivos = sorted([f for f in os.listdir(pasta) if f.startswith("parte_") and f.endswith(".mp4")])

            for arq in arquivos:
                if arq not in existentes:
                    caminho = os.path.join(pasta, arq)
                    if os.path.exists(caminho) and os.path.getsize(caminho) > 50000:
                        num_match = re.search(r"parte_(\d+)", arq)
                        idx = int(num_match.group(1)) if num_match else 0
                        inicio_s = idx * duracao_chunk
                        fim_s = inicio_s + duracao_chunk
                        self._cadastrar_parte(sessao_id, arq, caminho, inicio_s, fim_s, titulo_sessao)
                        existentes.add(arq)

    def _despachar_upload_e_cortes(self, parte_id: int, caminho_video: str, nome_arquivo: str,
                                   titulo_completo: str, sessao_id: int):
        """Worker assíncrono: envia para pasta 'Live 24 hrs' no Drive e gera cortes com IA."""
        def _worker():
            # 1. Upload do vídeo bruto para o Google Drive na pasta 'Live 24 hrs'
            try:
                id_pasta_raw = config.PASTA_FONTE_LIVES_PADRAO
                token, _ = google_drive.obter_token_acesso()
                if token and os.path.exists(caminho_video):
                    log.info("[Google Drive] Enviando vídeo bruto '%s' para pasta 'Live 24 hrs'...", nome_arquivo)
                    with _conectar() as conn:
                        conn.execute("UPDATE partes_live SET drive_status = 'enviando' WHERE id = ?", (parte_id,))
                        conn.commit()

                    res_up = google_drive.enviar_arquivo_drive(caminho_video, nome_arquivo, id_pasta_raw, token=token)
                    if res_up and res_up.get("id"):
                        file_id = res_up["id"]
                        drive_url = f"https://drive.google.com/file/d/{file_id}/view"
                        with _conectar() as conn:
                            conn.execute("""
                                UPDATE partes_live SET
                                    drive_status = 'enviado',
                                    drive_file_id = ?,
                                    drive_url = ?,
                                    enviado_em = ?
                                WHERE id = ?
                            """, (file_id, drive_url, time.time(), parte_id))
                            conn.commit()
                        log.info("[Google Drive] ✅ Vídeo bruto '%s' enviado com sucesso: %s", nome_arquivo, drive_url)
                    else:
                        with _conectar() as conn:
                            conn.execute("UPDATE partes_live SET drive_status = 'erro' WHERE id = ?", (parte_id,))
                            conn.commit()
            except Exception as err_drive:
                log.warning("[Google Drive] Aviso no upload de '%s': %s", nome_arquivo, err_drive)
                try:
                    with _conectar() as conn:
                        conn.execute("UPDATE partes_live SET drive_status = 'erro' WHERE id = ?", (parte_id,))
                        conn.commit()
                except Exception:
                    pass

            # 2. Motor de Cortes com IA (3 Modalidades no Drive)
            if self._auto_cortar:
                try:
                    log.info("[Motor IA 24/7] ✂️ Iniciando geração automática de cortes para '%s'...", titulo_completo)
                    with _conectar() as conn:
                        conn.execute("UPDATE partes_live SET cortes_status = 'processando' WHERE id = ?", (parte_id,))
                        conn.commit()

                    video_info = {
                        "id": f"local_{os.path.splitext(nome_arquivo)[0]}",
                        "nome_arquivo": nome_arquivo,
                        "titulo": titulo_completo,
                        "caminho_local": caminho_video,
                        "tamanho": os.path.getsize(caminho_video) if os.path.exists(caminho_video) else 0,
                        "numero_parte": 1,
                        "origem": "local"
                    }
                    res_corte = cortador_drive.processar_video_drive(video_info, max_cortes=50)
                    cortes_gerados = res_corte.get("cortes_gerados", 0)
                    sucesso = res_corte.get("sucesso", False)
                    with _conectar() as conn:
                        conn.execute("""
                            UPDATE partes_live SET
                                cortes_status = ?,
                                cortes_total = ?,
                                cortes_detalhes = ?
                            WHERE id = ?
                        """, (
                            'concluido' if sucesso else 'erro',
                            cortes_gerados,
                            json.dumps({"sucesso": sucesso, "cortes_gerados": cortes_gerados}, ensure_ascii=False),
                            parte_id
                        ))
                        conn.commit()
                    log.info("[Motor IA 24/7] ✅ Cortes de '%s' finalizados com sucesso! %d cortes enviados ao Drive nas 3 modalidades.", titulo_completo, cortes_gerados)
                except Exception as err_corte:
                    log.error("[Motor IA 24/7] Erro ao processar cortes de '%s': %s", titulo_completo, err_corte)
                    try:
                        with _conectar() as conn:
                            conn.execute("UPDATE partes_live SET cortes_status = 'erro' WHERE id = ?", (parte_id,))
                            conn.commit()
                    except Exception:
                        pass

        t = threading.Thread(target=_worker, daemon=True, name=f"parte-proc-{parte_id}")
        t.start()

    def disparar_cortes_manuais(self, parte_id: int) -> Dict[str, Any]:
        """Dispara manualmente a esteira de cortes para uma parte já gravada."""
        with _conectar() as conn:
            parte = conn.execute("SELECT p.*, s.titulo as sessao_titulo FROM partes_live p JOIN sessoes_live s ON p.sessao_id = s.id WHERE p.id = ?", (parte_id,)).fetchone()
            if not parte:
                return {"ok": False, "erro": "Parte não encontrada"}
            p_dict = dict(parte)

        titulo_base = p_dict.get("sessao_titulo") or f"Live {p_dict['youtube_id']}"
        titulo_completo = f"{titulo_base} - Parte {p_dict['numero_parte']}"
        self._despachar_upload_e_cortes(parte_id, p_dict["caminho_video"], p_dict["nome_arquivo"], titulo_completo, p_dict["sessao_id"])
        return {"ok": True, "mensagem": f"Cortes de '{titulo_completo}' disparados em background!"}

    def _iniciar_thread_monitor_247(self):
        """Inicia a thread contínua de supervisão que monitora o canal e grava quando a live abrir."""
        if self._thread_monitor_247 and self._thread_monitor_247.is_alive():
            return
        self._thread_monitor_247 = threading.Thread(target=self._loop_monitor_continuo, daemon=True, name="live-monitor-247")
        self._thread_monitor_247.start()

    def _loop_monitor_continuo(self):
        """Loop contínuo 24/7: se a live estiver online e não estiver gravando, inicia gravação automática."""
        while True:
            try:
                if self._monitor_247_ativo:
                    is_rec = bool(self._processo_ffmpeg and self._processo_ffmpeg.poll() is None)
                    is_online = check_streamlink_online(self._url_monitorada, quality=self._qualidade)
                    self._is_live_online = is_online

                    if is_online:
                        if not is_rec:
                            # Trava de Segurança Multi-Notebook: se outro notebook já for Líder, fica em Standby
                            if not cluster.evaluate_leadership(self._stream_title):
                                log.info("[Cluster] Live online detectada, mas máquina em STANDBY (Líder ativo: '%s'). Gravação não iniciada.",
                                         cluster.current_leader_id)
                                time.sleep(20)
                                continue

                            log.info("🔴 Live Stream DETECTADA AO VIVO em '%s'! Máquina é Líder. Iniciando gravação automática...", self._url_monitorada)
                            try:
                                self.iniciar_gravacao(
                                    url_ou_id=self._url_monitorada,
                                    duracao_chunk_s=self._duracao_chunk_s,
                                    qualidade=self._qualidade,
                                    dvr=self._dvr,
                                    auto_cortar=self._auto_cortar
                                )
                            except Exception as e_start:
                                log.error("Erro ao iniciar gravação automática: %s", e_start)
                    else:
                        if is_rec:
                            log.info("Live OFFLINE. Finalizando sessão de gravação atual...")
                            try:
                                self.parar_gravacao()
                            except Exception as e_stop:
                                log.warning("Aviso ao parar gravação após live offline: %s", e_stop)

            except Exception as e:
                log.debug("Aviso no ciclo do monitor 24/7: %s", e)

            time.sleep(20)

    def _iniciar_thread_ia(self):
        if self._thread_ia and self._thread_ia.is_alive():
            return
        self._thread_ia = threading.Thread(target=self._loop_processamento_ia, daemon=True, name="live-ia-worker")
        self._thread_ia.start()

    def _loop_processamento_ia(self):
        """Processa partes pendentes de IA ou de YouTube."""
        while True:
            try:
                parte_ia = None
                with _conectar() as conn:
                    row = conn.execute("SELECT * FROM partes_live WHERE estado = 'gravado' AND resumo IS NULL ORDER BY id ASC LIMIT 1").fetchone()
                    if row:
                        parte_ia = dict(row)

                if parte_ia:
                    self._processar_parte_ia(parte_ia)
                    continue

                self.limpar_retencao_48h()
            except Exception as e:
                log.debug("Aviso loop IA: %s", e)

            time.sleep(5)

    def _processar_parte_ia(self, parte: Dict[str, Any]):
        parte_id = parte["id"]
        caminho_video = parte["caminho_video"]
        if not os.path.exists(caminho_video):
            self._atualizar_estado_parte(parte_id, "erro", erro="Arquivo de vídeo não encontrado")
            return

        caminho_audio = os.path.splitext(caminho_video)[0] + ".mp3"
        if not os.path.exists(caminho_audio):
            cmd_audio = [
                config.FFMPEG or "ffmpeg", "-y", "-i", caminho_video,
                "-vn", "-c:a", "libmp3lame", "-b:a", "64k", "-ac", "1", "-ar", "16000",
                caminho_audio
            ]
            res = subprocess.run(cmd_audio, capture_output=True, text=True)
            if res.returncode != 0 or not os.path.exists(caminho_audio):
                log.debug("Falha extraindo áudio para IA: %s", res.stderr[:200])
                return

        try:
            resultado_ia = self._analisar_audio_gemini(caminho_audio, parte)
            with _conectar() as conn:
                conn.execute("""
                    UPDATE partes_live SET
                        caminho_audio = ?,
                        resumo = ?,
                        capitulos = ?,
                        transcricao = ?,
                        atualizado_em = ?
                    WHERE id = ?
                """, (
                    caminho_audio,
                    resultado_ia.get("resumo", ""),
                    resultado_ia.get("capitulos", ""),
                    resultado_ia.get("transcricao", ""),
                    time.time(),
                    parte_id
                ))
                conn.commit()
            log.info("Capítulos e resumo gerados para Parte #%d", parte_id)
        except Exception as err:
            log.debug("Aviso análise IA Gemini: %s", err)

    def _analisar_audio_gemini(self, caminho_audio: str, parte: Dict[str, Any]) -> Dict[str, Any]:
        cliente = gemini._obter_cliente()
        arquivo_gemini = cliente.files.upload(file=caminho_audio)
        nome_arquivo_gemini = arquivo_gemini.name
        duracao_min = int(parte.get("duracao_s", 1800) / 60)
        prompt = f"""Você é o editor oficial e produtor de conteúdo.
Analise este áudio de aproximadamente {duracao_min} minutos transmitido ao vivo.
Gere uma resposta estritamente estruturada em JSON com o seguinte schema:
{{
  "resumo": "Um resumo jornalístico e envolvente de 3 a 5 parágrafos dos principais acontecimentos deste trecho.",
  "capitulos": "Capítulos com marcação de tempo compatíveis com o YouTube no formato exato:\\n00:00 Início\\n05:12 Assunto A\\n14:30 Debate sobre B\\n22:15 Conclusão",
  "transcricao": "Transcrição dos pontos principais ou síntese fiel das falas dos participantes."
}}
"""
        from google.genai import types
        config_gen = types.GenerateContentConfig(
            response_mime_type="application/json",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        modelo = config.GEMINI_MODELOS[0] if config.GEMINI_MODELOS else "gemini-2.5-flash"
        try:
            resp = cliente.models.generate_content(
                model=modelo,
                contents=[arquivo_gemini, prompt],
                config=config_gen
            )
            dados = json.loads(resp.text or "{}")
            return dados
        finally:
            try:
                cliente.files.delete(name=nome_arquivo_gemini)
            except Exception:
                pass

    def _atualizar_estado_parte(self, parte_id: int, estado: str, erro: Optional[str] = None):
        with _conectar() as conn:
            conn.execute("""
                UPDATE partes_live SET estado = ?, erro_mensagem = ?, atualizado_em = ? WHERE id = ?
            """, (estado, erro or "", time.time(), parte_id))
            conn.commit()

    def limpar_retencao_48h(self) -> Dict[str, Any]:
        """Remove o arquivo MP4 local após 48h com upload e cortes confirmados no Drive."""
        limite_tempo = time.time() - RETENCAO_SEGUNDOS
        removidos = 0
        bytes_liberados = 0

        with _conectar() as conn:
            candidatos = conn.execute("""
                SELECT id, caminho_video, tamanho_bytes FROM partes_live
                WHERE limpo_disco = 0 AND ((enviado_em IS NOT NULL AND enviado_em <= ?) OR (drive_status = 'enviado' AND atualizado_em <= ?))
            """, (limite_tempo, limite_tempo)).fetchall()

            for cand in candidatos:
                caminho = cand["caminho_video"]
                if caminho and os.path.exists(caminho):
                    try:
                        tam = os.path.getsize(caminho)
                        os.remove(caminho)
                        bytes_liberados += tam
                        removidos += 1
                        conn.execute("UPDATE partes_live SET limpo_disco = 1, atualizado_em = ? WHERE id = ?",
                                     (time.time(), cand["id"]))
                    except Exception as e:
                        log.warning("Falha ao remover arquivo de retenção %s: %s", caminho, e)
                else:
                    conn.execute("UPDATE partes_live SET limpo_disco = 1, atualizado_em = ? WHERE id = ?",
                                 (time.time(), cand["id"]))
            conn.commit()

        return {"removidos": removidos, "bytes_liberados": bytes_liberados}

    def reprocessar_parte(self, parte_id: int) -> Dict[str, Any]:
        with _conectar() as conn:
            conn.execute("UPDATE partes_live SET estado = 'gravado', erro_mensagem = '', atualizado_em = ? WHERE id = ?",
                         (time.time(), parte_id))
            conn.commit()
        self._iniciar_thread_ia()
        return {"ok": True, "parte_id": parte_id}

    def abrir_navegador_login(self) -> Dict[str, Any]:
        uploader_script = os.path.join(config.RAIZ, "scripts", "youtube_uploader.js")
        cmd = [
            config.NODE or "node",
            uploader_script,
            "--login",
            "--profile", config.PASTA_YOUTUBE_PERFIL,
        ]
        proc = subprocess.Popen(cmd)
        return {"ok": True, "pid": proc.pid}

    def recuperar_ao_iniciar(self):
        try:
            with _conectar() as conn:
                sessoes = conn.execute("SELECT * FROM sessoes_live WHERE estado = 'gravando'").fetchall()
                for sessao in sessoes:
                    sessao_id = sessao["id"]
                    pid = sessao["pid_processo"]
                    esta_rodando = False
                    if pid:
                        try:
                            import psutil
                            esta_rodando = psutil.pid_exists(pid)
                        except Exception:
                            pass
                    if not esta_rodando:
                        self._reconciliar_arquivos_da_sessao(sessao_id)
                        conn.execute("UPDATE sessoes_live SET estado = 'interrompido', mensagem = 'Interrompido por reinício', atualizado_em = ? WHERE id = ?",
                                     (time.time(), sessao_id))
                conn.commit()

                pendentes = conn.execute("SELECT COUNT(*) as c FROM partes_live WHERE estado IN ('gravado', 'analisando_ia', 'pronto_upload')").fetchone()
                if pendentes and pendentes["c"] > 0:
                    self._iniciar_thread_ia()
        except Exception as e:
            log.warning("Aviso durante recuperação do gravador de live: %s", e)


# Instância única global
gravador = GerenciadorGravacaoLive()
