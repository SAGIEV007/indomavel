"""Gravador e fatiador automático de transmissões ao vivo do YouTube (30 min por bloco).

Funcionalidades:
- Gravação contínua em blocos de 30 minutos em MP4 sem perda (-c copy).
- Modo Catch-up DVR: baixa todo o buffer transmitido até agora e continua gravando o ao vivo.
- Extração de áudio e geração automática de resumo, transcrição e capítulos do YouTube com Gemini Flash.
- Envio automatizado para o YouTube (Não Listado / Playlist) via sessão persistente do Playwright.
- Máquina de estados resiliente em SQLite: reinício do notebook não perde dados.
- Retenção de disco de 48 horas após upload, preservando metadados permanentemente.
"""

import datetime
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time

import yt_dlp

from . import config, gemini

log = logging.getLogger("indomavel.gravador_live")

DB_NOME = "gravador_lives.sqlite3"
RETENCAO_SEGUNDOS = 48 * 3600  # 48 horas de segurança


def _caminho_db():
    os.makedirs(config.PASTA_DADOS, exist_ok=True)
    return os.path.join(config.PASTA_DADOS, DB_NOME)


import contextlib


@contextlib.contextmanager
def _conectar():
    conn = sqlite3.connect(_caminho_db(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
    finally:
        conn.close()


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
        """)


def extrair_id(url_ou_id):
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


def obter_informacoes_live(url_ou_id):
    """Extrai metadados e URLs dos streams HLS da transmissão ao vivo."""
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
    is_live = bool(info.get("is_live") or info.get("live_status") == "is_live")
    formats = info.get("formats") or []

    # Localiza o melhor video H.264 (avc1) e melhor audio AAC
    videos_hls = [
        f for f in formats
        if f.get("url") and f.get("resolution") != "audio only" and f.get("vcodec") not in (None, "none")
    ]
    audios_hls = [
        f for f in formats
        if f.get("url") and (f.get("resolution") == "audio only" or (f.get("vcodec") in (None, "none") and f.get("height") in (None, 0)))
    ]

    # Se nao houver separado, tenta formatos combinados
    if not videos_hls:
        videos_hls = [f for f in formats if f.get("url")]

    def pontuacao_video(f):
        altura = f.get("height") or 0
        codec = f.get("vcodec") or ""
        avc = 1 if "avc" in codec.lower() else 0
        return (altura <= 1080, altura, avc)

    def pontuacao_audio(f):
        # Faixa no idioma original antes das dublagens automáticas do YouTube (language_preference 10 contra -1).
        fid = f.get("format_id", "")
        num = int(fid) if str(fid).isdigit() else 0
        return (f.get("language_preference") or 0, num, f.get("abr") or 0)

    melhor_video = max(videos_hls, key=pontuacao_video) if videos_hls else None
    melhor_audio = max(audios_hls, key=pontuacao_audio) if audios_hls else None

    return {
        "youtube_id": youtube_id,
        "url_origem": url,
        "titulo": titulo,
        "is_live": is_live,
        "url_video": melhor_video["url"] if melhor_video else None,
        "url_audio": melhor_audio["url"] if melhor_audio else None,
        "altura": melhor_video.get("height") if melhor_video else None,
    }


class GerenciadorGravacaoLive:
    """Gerencia o ciclo de vida completo da gravação, fatiamento e automação de lives."""

    def __init__(self):
        iniciar_banco()
        self._processo_ffmpeg = None
        self._sessao_ativa_id = None
        self._thread_monitor = None
        self._thread_ia = None
        self._parar_evento = threading.Event()
        self._trava = threading.Lock()
        self.recuperar_ao_iniciar()

    def obter_status(self):
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
                rows = conn.execute("SELECT * FROM partes_live WHERE sessao_id = ? ORDER BY numero_parte ASC", (sessao["id"],)).fetchall()
                partes = [dict(r) for r in rows]

            sessoes_recentes = conn.execute("SELECT * FROM sessoes_live ORDER BY id DESC LIMIT 5").fetchall()

        gravando = bool(self._processo_ffmpeg and self._processo_ffmpeg.poll() is None)
        return {
            "gravando": gravando,
            "sessao_ativa": sessao,
            "partes": partes,
            "sessoes_recentes": [dict(s) for s in sessoes_recentes],
        }

    def iniciar_gravacao(self, url_ou_id, playlist_id=None, dvr=True, duracao_chunk_s=1800, pasta_base=None):
        with self._trava:
            if self._processo_ffmpeg and self._processo_ffmpeg.poll() is None:
                raise RuntimeError("Já existe uma gravação de live em andamento.")

            info = obter_informacoes_live(url_ou_id)
            if not info["url_video"]:
                raise RuntimeError("Não foi possível obter o fluxo de vídeo da live.")

            youtube_id = info["youtube_id"]
            titulo = info["titulo"]
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'gravando', ?, ?, 'Iniciando captura...')
                """, (
                    youtube_id, info["url_origem"], titulo, playlist_id or "", pasta_destino,
                    1 if dvr else 0, duracao_chunk_s, agora, agora
                ))
                sessao_id = cursor.lastrowid
                conn.commit()

            self._sessao_ativa_id = sessao_id
            self._parar_evento.clear()

            # Monta o comando do FFmpeg para gravação contínua segmentada em 30 min
            cmd = [config.FFMPEG or "ffmpeg", "-y"]
            if dvr:
                # Catch-up DVR: inicia a partir do primeiro segmento do buffer HLS
                cmd += ["-live_start_index", "0"]
            cmd += ["-i", info["url_video"]]

            if info["url_audio"]:
                if dvr:
                    cmd += ["-live_start_index", "0"]
                cmd += ["-i", info["url_audio"]]

            cmd += ["-c:v", "copy"]
            if info["url_audio"]:
                cmd += ["-c:a", "copy"]
            cmd += [
                "-f", "segment",
                "-segment_time", str(duracao_chunk_s),
                "-reset_timestamps", "1",
                "-segment_list", os.path.join(pasta_destino, "segmentos.csv"),
                "-segment_list_type", "csv",
                os.path.join(pasta_destino, "parte_%03d.mp4")
            ]

            log.info("Iniciando FFmpeg live: %s", " ".join(cmd[:8]))
            self._processo_ffmpeg = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            with _conectar() as conn:
                conn.execute("UPDATE sessoes_live SET pid_processo = ?, mensagem = 'Gravando' WHERE id = ?",
                             (self._processo_ffmpeg.pid, sessao_id))
                conn.commit()

            # Inicia thread de monitoramento da sessão
            self._thread_monitor = threading.Thread(
                target=self._loop_monitorar,
                args=(sessao_id, pasta_destino, duracao_chunk_s),
                daemon=True,
                name=f"live-monitor-{sessao_id}"
            )
            self._thread_monitor.start()

            # Inicia thread processadora de IA e upload
            self._iniciar_thread_ia()

            return {"sessao_id": sessao_id, "youtube_id": youtube_id, "titulo": titulo, "pasta": pasta_destino}

    def parar_gravacao(self, sessao_id=None):
        with self._trava:
            alvo_id = sessao_id or self._sessao_ativa_id
            if not alvo_id:
                return {"mensagem": "Nenhuma sessão ativa."}

            self._parar_evento.set()

            if self._processo_ffmpeg and self._processo_ffmpeg.poll() is None:
                try:
                    # Envia 'q' para o FFmpeg finalizar os arquivos graciosamente
                    if self._processo_ffmpeg.stdin:
                        self._processo_ffmpeg.stdin.write("q\n")
                        self._processo_ffmpeg.stdin.flush()
                    self._processo_ffmpeg.wait(timeout=8)
                except Exception:
                    try:
                        self._processo_ffmpeg.terminate()
                        self._processo_ffmpeg.wait(timeout=4)
                    except Exception:
                        self._processo_ffmpeg.kill()

            self._processo_ffmpeg = None

            # Reconcilia o último pedaço
            self._reconciliar_arquivos_da_sessao(alvo_id)

            with _conectar() as conn:
                conn.execute(
                    "UPDATE sessoes_live SET estado = 'concluido', mensagem = 'Gravação finalizada', atualizado_em = ? WHERE id = ?",
                    (time.time(), alvo_id)
                )
                conn.commit()

            if self._sessao_ativa_id == alvo_id:
                self._sessao_ativa_id = None

            return {"sessao_id": alvo_id, "status": "concluido"}

    def _loop_monitorar(self, sessao_id, pasta_destino, duracao_chunk_s):
        """Monitora novos arquivos de 30 minutos gerados pelo segmenter do FFmpeg."""
        csv_path = os.path.join(pasta_destino, "segmentos.csv")
        partes_registradas = set()

        # Carrega partes já registradas
        with _conectar() as conn:
            rows = conn.execute("SELECT nome_arquivo FROM partes_live WHERE sessao_id = ?", (sessao_id,)).fetchall()
            partes_registradas.update(r["nome_arquivo"] for r in rows)

        while not self._parar_evento.is_set():
            if self._processo_ffmpeg and self._processo_ffmpeg.poll() is not None:
                # Processo encerrou
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
                                    self._cadastrar_parte(sessao_id, nome_arq, caminho_completo, inicio_s, fim_s)
                                    partes_registradas.add(nome_arq)
                except Exception as e:
                    log.warning("Erro lendo segmentos.csv: %s", e)

            # 2. Varredura direta de arquivos terminados parte_XXX.mp4
            # Se parte_001.mp4 já existe e tem tamanho crescente, parte_000.mp4 está pronta.
            arquivos = sorted([
                f for f in os.listdir(pasta_destino)
                if f.startswith("parte_") and f.endswith(".mp4")
            ])
            if len(arquivos) >= 2:
                for arq in arquivos[:-1]:  # Todos exceto o último em gravação ativa
                    if arq not in partes_registradas:
                        caminho_completo = os.path.join(pasta_destino, arq)
                        idx = int(re.search(r"parte_(\d+)", arq).group(1)) if re.search(r"parte_(\d+)", arq) else 0
                        inicio_s = idx * duracao_chunk_s
                        fim_s = inicio_s + duracao_chunk_s
                        self._cadastrar_parte(sessao_id, arq, caminho_completo, inicio_s, fim_s)
                        partes_registradas.add(arq)

            time.sleep(3)

        # Ao sair do loop, reconcilia arquivos finais
        self._reconciliar_arquivos_da_sessao(sessao_id)

    def _cadastrar_parte(self, sessao_id, nome_arquivo, caminho_video, inicio_s, fim_s):
        with _conectar() as conn:
            sessao = conn.execute("SELECT youtube_id FROM sessoes_live WHERE id = ?", (sessao_id,)).fetchone()
            if not sessao:
                return
            youtube_id = sessao["youtube_id"]
            num_match = re.search(r"parte_(\d+)", nome_arquivo)
            num_parte = int(num_match.group(1)) + 1 if num_match else 1
            tamanho = os.path.getsize(caminho_video) if os.path.exists(caminho_video) else 0
            duracao = max(0.0, fim_s - inicio_s)
            agora = time.time()

            conn.execute("""
                INSERT INTO partes_live (
                    sessao_id, youtube_id, numero_parte, nome_arquivo, caminho_video,
                    inicio_s, fim_s, duracao_s, tamanho_bytes, estado, criado_em, atualizado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'gravado', ?, ?)
            """, (sessao_id, youtube_id, num_parte, nome_arquivo, caminho_video, inicio_s, fim_s, duracao, tamanho, agora, agora))
            conn.commit()
            log.info("Parte cadastrada: %s (Sessão %d, Parte %d)", nome_arquivo, sessao_id, num_parte)

    def _reconciliar_arquivos_da_sessao(self, sessao_id):
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
                    if os.path.exists(caminho) and os.path.getsize(caminho) > 50000:  # > 50KB
                        num_match = re.search(r"parte_(\d+)", arq)
                        idx = int(num_match.group(1)) if num_match else 0
                        inicio_s = idx * duracao_chunk
                        fim_s = inicio_s + duracao_chunk
                        self._cadastrar_parte(sessao_id, arq, caminho, inicio_s, fim_s)
                        existentes.add(arq)

    def _iniciar_thread_ia(self):
        if self._thread_ia and self._thread_ia.is_alive():
            return
        self._thread_ia = threading.Thread(target=self._loop_processamento_ia, daemon=True, name="live-ia-worker")
        self._thread_ia.start()

    def _loop_processamento_ia(self):
        """Varre e processa partes gravadas: extração de áudio, IA do Gemini e upload."""
        while True:
            # 1. Busca parte pendente de IA ('gravado')
            parte_ia = None
            with _conectar() as conn:
                row = conn.execute("""
                    SELECT * FROM partes_live WHERE estado = 'gravado' ORDER BY id ASC LIMIT 1
                """).fetchone()
                if row:
                    parte_ia = dict(row)

            if parte_ia:
                self._processar_parte_ia(parte_ia)
                continue

            # 2. Busca parte pronta para upload ('pronto_upload')
            parte_up = None
            with _conectar() as conn:
                row = conn.execute("""
                    SELECT p.*, s.playlist_id, s.titulo as sessao_titulo
                    FROM partes_live p
                    JOIN sessoes_live s ON p.sessao_id = s.id
                    WHERE p.estado = 'pronto_upload'
                    ORDER BY p.id ASC LIMIT 1
                """).fetchone()
                if row:
                    parte_up = dict(row)

            if parte_up:
                self._processar_upload(parte_up)
                continue

            # 3. Executa limpeza de retenção de 48h
            self.limpar_retencao_48h()

            time.sleep(5)

    def _processar_parte_ia(self, parte):
        parte_id = parte["id"]
        caminho_video = parte["caminho_video"]
        if not os.path.exists(caminho_video):
            self._atualizar_estado_parte(parte_id, "erro", erro="Arquivo de vídeo não encontrado")
            return

        self._atualizar_estado_parte(parte_id, "analisando_ia")
        caminho_audio = os.path.splitext(caminho_video)[0] + ".mp3"

        # 1. Extração de áudio leve (MP3 64k mono)
        if not os.path.exists(caminho_audio):
            cmd_audio = [
                config.FFMPEG or "ffmpeg", "-y", "-i", caminho_video,
                "-vn", "-c:a", "libmp3lame", "-b:a", "64k", "-ac", "1", "-ar", "16000",
                caminho_audio
            ]
            res = subprocess.run(cmd_audio, capture_output=True, text=True)
            if res.returncode != 0 or not os.path.exists(caminho_audio):
                self._atualizar_estado_parte(parte_id, "erro", erro=f"Falha extraindo áudio: {res.stderr[:200]}")
                return

        # 2. Análise com Gemini Flash
        try:
            resultado_ia = self._analisar_audio_gemini(caminho_audio, parte)
            with _conectar() as conn:
                conn.execute("""
                    UPDATE partes_live SET
                        caminho_audio = ?,
                        resumo = ?,
                        capitulos = ?,
                        transcricao = ?,
                        estado = 'pronto_upload',
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
            log.info("IA concluída para Parte ID %d", parte_id)
        except Exception as err:
            log.error("Erro na análise IA (Parte %d): %s", parte_id, err)
            # Permite seguir para pronto_upload mesmo sem IA se esgotar cotas
            with _conectar() as conn:
                conn.execute("""
                    UPDATE partes_live SET
                        caminho_audio = ?,
                        resumo = 'Análise IA pendente ou cota esgotada.',
                        capitulos = '00:00 Início da Parte',
                        estado = 'pronto_upload',
                        erro_mensagem = ?,
                        atualizado_em = ?
                    WHERE id = ?
                """, (caminho_audio, str(err)[:250], time.time(), parte_id))
                conn.commit()

    def _analisar_audio_gemini(self, caminho_audio, parte):
        """Usa Gemini Flash para gerar resumo, transcrição e capítulos formatados."""
        cliente = gemini._obter_cliente()
        log.info("Fazendo upload do áudio para API Gemini: %s", caminho_audio)
        arquivo_gemini = cliente.files.upload(file=caminho_audio)
        nome_arquivo_gemini = arquivo_gemini.name

        duracao_min = int(parte.get("duracao_s", 1800) / 60)
        prompt = f"""Você é o editor oficial e produtor de conteúdo do canal.
Analise este áudio de aproximadamente {duracao_min} minutos transmitido ao vivo.
Gere uma resposta estritamente estruturada em JSON com o seguinte schema:
{{
  "resumo": "Um resumo jornalístico e envolvente de 3 a 5 parágrafos dos principais acontecimentos deste trecho.",
  "capitulos": "Capítulos com marcação de tempo compatíveis com o YouTube no formato exato:\\n00:00 Início\\n05:12 Assunto A\\n14:30 Debate sobre B\\n22:15 Conclusão",
  "transcricao": "Transcrição dos pontos principais ou síntese fiel das falas dos participantes."
}}

Regras:
1. O primeiro capítulo DEVE começar em 00:00.
2. Formato dos tempos sempre mm:ss ou hh:mm:ss.
3. Seja preciso, objetivo e no tom enérgico do canal.
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

    def _processar_upload(self, parte):
        parte_id = parte["id"]
        caminho_video = parte["caminho_video"]
        if not os.path.exists(caminho_video):
            self._atualizar_estado_parte(parte_id, "erro", erro="Arquivo de vídeo não encontrado para upload")
            return

        self._atualizar_estado_parte(parte_id, "enviando")

        # Prepara título e descrição para o YouTube
        titulo_sessao = parte.get("sessao_titulo") or f"Live {parte['youtube_id']}"
        num_parte = parte["numero_parte"]
        inicio_m = int(parte["inicio_s"] // 60)
        fim_m = int(parte["fim_s"] // 60)
        titulo_video = f"{titulo_sessao} - Parte {num_parte:02d} [{inicio_m:02d}m-{fim_m:02d}m]"

        capitulos = parte.get("capitulos") or "00:00 Início"
        resumo = parte.get("resumo") or ""
        descricao = f"{resumo}\n\nCapítulos:\n{capitulos}\n\nGravado automaticamente pelo Indomável."

        uploader_script = os.path.join(config.RAIZ, "scripts", "youtube_uploader.js")
        if not os.path.exists(uploader_script):
            self._atualizar_estado_parte(parte_id, "pronto_upload", erro="Script uploader não encontrado")
            return

        cmd = [
            config.NODE or "node",
            uploader_script,
            "--video", caminho_video,
            "--title", titulo_video,
            "--description", descricao,
            "--profile", config.PASTA_YOUTUBE_PERFIL,
        ]
        if parte.get("playlist_id"):
            cmd += ["--playlist", parte["playlist_id"]]

        log.info("Executando upload Playwright para parte %d...", parte_id)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            saida = res.stdout.strip()
            log.info("Saída uploader: %s", saida[:300])

            # Tenta decodificar JSON da saída
            resultado = None
            for linha in saida.splitlines():
                linha = linha.strip()
                if linha.startswith("{") and linha.endswith("}"):
                    try:
                        resultado = json.loads(linha)
                        break
                    except Exception:
                        pass

            if resultado and resultado.get("sucesso"):
                youtube_url = resultado.get("video_url") or "https://studio.youtube.com"
                video_id = resultado.get("video_id") or ""
                with _conectar() as conn:
                    conn.execute("""
                        UPDATE partes_live SET
                            estado = 'enviado',
                            youtube_video_id = ?,
                            youtube_url = ?,
                            enviado_em = ?,
                            atualizado_em = ?
                        WHERE id = ?
                    """, (video_id, youtube_url, time.time(), time.time(), parte_id))
                    conn.commit()
                log.info("Parte %d enviada com sucesso: %s", parte_id, youtube_url)
            elif resultado and resultado.get("precisa_login"):
                self._atualizar_estado_parte(parte_id, "pronto_upload",
                                             erro="Aguardando login no YouTube Studio. Conecte sua conta no painel.")
            else:
                erro_msg = (resultado and resultado.get("erro")) or res.stderr or "Falha no upload"
                self._atualizar_estado_parte(parte_id, "pronto_upload", erro=str(erro_msg)[:250])
        except Exception as e:
            log.error("Exceção no upload da parte %d: %s", parte_id, e)
            self._atualizar_estado_parte(parte_id, "pronto_upload", erro=str(e)[:250])

    def _atualizar_estado_parte(self, parte_id, estado, erro=None):
        with _conectar() as conn:
            conn.execute("""
                UPDATE partes_live SET estado = ?, erro_mensagem = ?, atualizado_em = ? WHERE id = ?
            """, (estado, erro or "", time.time(), parte_id))
            conn.commit()

    def limpar_retencao_48h(self):
        """Remove o arquivo MP4 pesado do disco após 48 horas do envio bem-sucedido, mantendo metadados no SQLite."""
        limite_tempo = time.time() - RETENCAO_SEGUNDOS
        removidos = 0
        bytes_liberados = 0

        with _conectar() as conn:
            candidatos = conn.execute("""
                SELECT id, caminho_video, tamanho_bytes FROM partes_live
                WHERE estado = 'enviado' AND limpo_disco = 0 AND enviado_em IS NOT NULL AND enviado_em <= ?
            """, (limite_tempo,)).fetchall()

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

    def reprocessar_parte(self, parte_id):
        with _conectar() as conn:
            conn.execute("UPDATE partes_live SET estado = 'gravado', erro_mensagem = '', atualizado_em = ? WHERE id = ?",
                         (time.time(), parte_id))
            conn.commit()
        self._iniciar_thread_ia()
        return {"ok": True, "parte_id": parte_id}

    def abrir_navegador_login(self):
        """Abre navegador visível para o usuário autenticar uma única vez no YouTube Studio."""
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
        """Recupera sessões anteriores em caso de reinício do notebook ou queda de energia."""
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
                        # Reconcilia o que ficou salvo na pasta
                        self._reconciliar_arquivos_da_sessao(sessao_id)
                        conn.execute("UPDATE sessoes_live SET estado = 'interrompido', mensagem = 'Interrompido por reinício', atualizado_em = ? WHERE id = ?",
                                     (time.time(), sessao_id))
                conn.commit()

                # Se houver partes gravadas ou com análise pendente, ativa a thread de IA
                pendentes = conn.execute("SELECT COUNT(*) as c FROM partes_live WHERE estado IN ('gravado', 'analisando_ia', 'pronto_upload')").fetchone()
                if pendentes and pendentes["c"] > 0:
                    self._iniciar_thread_ia()
        except Exception as e:
            log.warning("Aviso durante recuperação do gravador de live: %s", e)


# Instância única global do gravador
gravador = GerenciadorGravacaoLive()
