"""Processador e Cortador Automático de Vídeos Direto do Google Drive.

Permite:
1. Escanear o Google Drive em busca de vídeos fontes (lives gravadas ou uploads brutos).
2. Baixar o vídeo fonte do Drive para o computador local.
3. Transcrever e blocar com a mesma precisão do pipeline oficial.
4. Gerar os cortes sequenciais respeitando a regra estrita do usuário:
   - Raiz / [Nome do Vídeo] / Cortes com headline (FernandoXX_sem_legenda.mp4 + FernandoXX_headlines.txt)
   - Raiz / [Nome do Vídeo] / Cortes originais (FernandoXX_cru.mp4 + FernandoXX_cru.srt)
5. Checar se cada corte já existe no Drive antes de subir, evitando qualquer sobreposição.
6. Excluir os arquivos físicos intermediários locais assim que confirmados no Drive para liberar espaço.
7. Operar em loop contínuo (VigilanteDriveContinuo).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time

from . import config, google_drive, cortador_automatico, blocador, legendas, render, headlines

log = logging.getLogger("indomavel.cortador_drive")

PASTA_DRIVE_TEMP = os.path.join(config.PASTA_DOWNLOADS, "drive_fontes")
os.makedirs(PASTA_DRIVE_TEMP, exist_ok=True)

_processando_lock = threading.Lock()
_videos_em_processamento = set()


def limpar_arquivos_drive_locais():
    """Remove arquivos brutos baixados do Drive para liberar espaço em disco."""
    bytes_removidos = 0
    if os.path.isdir(PASTA_DRIVE_TEMP):
        for f in os.listdir(PASTA_DRIVE_TEMP):
            c = os.path.join(PASTA_DRIVE_TEMP, f)
            try:
                if os.path.isfile(c):
                    bytes_removidos += os.path.getsize(c)
                    os.remove(c)
                elif os.path.isdir(c):
                    shutil.rmtree(c, ignore_errors=True)
            except Exception as e:
                log.warning("Aviso ao remover cache drive %s: %s", c, e)
    return bytes_removidos


def extrair_audio_video(caminho_video, caminho_audio=None):
    """Extrai faixa de áudio em MP3/M4A do vídeo local para transcrição rápida."""
    if not caminho_audio:
        base, _ = os.path.splitext(caminho_video)
        caminho_audio = base + "_audio.mp3"
    
    cmd = [
        config.FFMPEG or "ffmpeg",
        "-y",
        "-v", "error",
        "-i", caminho_video,
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "4",
        caminho_audio,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(caminho_audio):
        log.warning("Falha ffmpeg ao extrair áudio de %s: %s", caminho_video, res.stderr)
        return None
    return caminho_audio


def transcrever_audio_local(caminho_audio, ao_progredir=None):
    """Transcreve áudio usando Whisper local (faster-whisper) gerando lista de frases."""
    try:
        from . import transcricao_local
        palavras = transcricao_local.transcrever(caminho_audio, ao_progredir=ao_progredir)
        if not palavras:
            return None
        frases = legendas.frases_das_palavras(palavras)
        return frases
    except Exception as e:
        log.error("Erro na transcrição local do áudio %s: %s", caminho_audio, e)
        return None


PASTA_DESTINO_CORTES_PADRAO = "1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G"  # Pasta 'Cortes'
PASTA_FONTE_LIVES_PADRAO = "1Rzc1NQ0RDzeId6L_7O8QiP1P13bTooFl"  # Pasta 'Live 24 hrs'


def sanitizar_nome_video(nome):
    """Sanitiza título para uso seguro em pastas do Google Drive e Windows."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', str(nome))
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _extrair_numero_parte(nome_arquivo):
    m = re.search(r"Parte\s*(\d+)", nome_arquivo, re.IGNORECASE)
    return int(m.group(1)) if m else 9999


def _ja_possui_cortes_no_drive(titulo_video, pasta_raiz_id, token, mapa_pastas_destino=None):
    """Verifica se já existe a pasta com o nome do vídeo/evento e se ela já possui cortes prontos."""
    # 1. Verifica no histórico persistente local
    caminho_hist = os.path.join(config.PASTA_DADOS, "automacao", "drive_processados.json")
    if os.path.isfile(caminho_hist):
        try:
            with open(caminho_hist, "r", encoding="utf-8") as fh:
                hist = json.load(fh)
                if titulo_video in hist and hist[titulo_video].get("total_cortes", 0) > 0:
                    return True
        except Exception:
            pass

    nome_evento = google_drive.extrair_nome_evento_principal(titulo_video)

    # 2. Verifica se localmente já existem cortes para o evento
    pasta_local_evento = os.path.join(config.PASTA_GOOGLE_DRIVE, nome_evento)
    if os.path.isdir(pasta_local_evento):
        for r, _, files in os.walk(pasta_local_evento):
            if any(f.endswith(".mp4") for f in files):
                return True

    # 3. Busca na pasta de destino no Drive (via mapa em memória O(1) ou API)
    chave_tit = re.sub(r'\s+', ' ', titulo_video).strip().lower()
    chave_ev = re.sub(r'\s+', ' ', nome_evento).strip().lower()
    id_pasta_video = None
    if mapa_pastas_destino is not None:
        id_pasta_video = mapa_pastas_destino.get(chave_ev) or mapa_pastas_destino.get(chave_tit)
        if not id_pasta_video:
            alt_ev = re.sub(r'\s+', '  ', chave_ev)
            alt_tit = re.sub(r'\s+', '  ', chave_tit)
            id_pasta_video = mapa_pastas_destino.get(alt_ev) or mapa_pastas_destino.get(alt_tit)
    else:
        id_pasta_video = google_drive.buscar_pasta_drive(nome_evento, id_pai=pasta_raiz_id, token=token)
        if not id_pasta_video:
            id_pasta_video = google_drive.buscar_pasta_drive(titulo_video, id_pai=pasta_raiz_id, token=token)

    if not id_pasta_video:
        return False

    # Procura nas subpastas de modelo conhecidas
    pastas_modelo = [
        "Corte com headline", "Cortes com headline",
        "Corte com headline e legenda", "Cortes com headline e legenda",
        "Corte cru", "Cortes originais", "Corte com legenda", "Cortes com legenda"
    ]
    for p_mod in pastas_modelo:
        id_pasta_mod = google_drive.buscar_pasta_drive(p_mod, id_pai=id_pasta_video, token=token)
        if id_pasta_mod:
            arquivos = google_drive.listar_arquivos_subpasta(id_pasta_mod, token=token)
            if any(a.get("name", "").endswith(".mp4") for a in arquivos):
                return True

    # Verifica também se há arquivos diretos ou subpastas legadas
    itens_dentro = google_drive.listar_arquivos_subpasta(id_pasta_video, token=token)
    for it in itens_dentro:
        if it.get("name", "").endswith(".mp4"):
            return True
        if it.get("mimeType") == "application/vnd.google-apps.folder":
            sub_arqs = google_drive.listar_arquivos_subpasta(it.get("id"), token=token)
            if any(a.get("name", "").endswith(".mp4") for a in sub_arqs):
                return True

    return False


def escanear_videos_pendentes_drive(pasta_destino_id=None, pasta_fonte_id=None):
    """Varre as pastas de lives no Google Drive em busca de vídeos fontes pendentes de cortes."""
    if not pasta_destino_id:
        pasta_destino_id = google_drive.obter_pasta_id_ativa() or PASTA_DESTINO_CORTES_PADRAO
    if not pasta_fonte_id:
        pasta_fonte_id = PASTA_FONTE_LIVES_PADRAO

    token, err = google_drive.obter_token_acesso()
    if not token:
        log.warning("Scan de vídeos do Drive abortado (sem token): %s", err)
        return []

    # Mapeamento prévio O(1) de pastas já existentes no destino para evitar dezenas de chamadas de rede
    itens_destino = google_drive.listar_arquivos_subpasta(pasta_destino_id, token=token)
    mapa_pastas_destino = {}
    for it in itens_destino:
        if it.get("mimeType") == "application/vnd.google-apps.folder":
            nome_norm = re.sub(r'\s+', ' ', it.get("name", "")).strip().lower()
            mapa_pastas_destino[nome_norm] = it.get("id")

    pendentes = []
    pastas_processadas = set()

    pastas_ignorar = {
        "outros", "cortes crus", "com headline e sem legenda", "headlines",
        "com headline e legenda", "só legenda", "debate econômico especial",
        "cortes", "cortes com headline", "cortes originais",
        "corte com headline", "corte com headline e legenda", "corte cru", "corte com legenda"
    }

    # 1. Varre a pasta de Lives ('Live 24 hrs') e suas subpastas
    pastas_fontes = [pasta_fonte_id]
    itens_raiz_live = google_drive.listar_arquivos_subpasta(pasta_fonte_id, token=token)
    for sub in itens_raiz_live:
        if sub.get("mimeType") == "application/vnd.google-apps.folder":
            pastas_fontes.append(sub.get("id"))

    for fid_pasta in pastas_fontes:
        if fid_pasta in pastas_processadas:
            continue
        pastas_processadas.add(fid_pasta)

        arquivos_pasta = google_drive.listar_arquivos_subpasta(fid_pasta, token=token)
        nome_pasta_fonte = ""
        if fid_pasta != pasta_fonte_id:
            for sub in itens_raiz_live:
                if sub.get("id") == fid_pasta:
                    nome_pasta_fonte = sub.get("name", "")
                    break

        for arq in arquivos_pasta:
            nome = arq.get("name", "")
            mime = arq.get("mimeType", "")
            fid = arq.get("id")

            if "video/" in mime or nome.lower().endswith((".mp4", ".mkv", ".mov", ".avi", ".ts")):
                base_nome = os.path.splitext(nome)[0]
                if nome_pasta_fonte:
                    titulo_bruto = f"{nome_pasta_fonte} - {base_nome}"
                else:
                    titulo_bruto = base_nome

                titulo_limpo = sanitizar_nome_video(titulo_bruto)

                if not _ja_possui_cortes_no_drive(titulo_limpo, pasta_destino_id, token, mapa_pastas_destino=mapa_pastas_destino):
                    pendentes.append({
                        "id": fid,
                        "tipo": "arquivo_fonte",
                        "pasta_fonte_id": fid_pasta,
                        "pasta_fonte_nome": nome_pasta_fonte,
                        "nome_arquivo": nome,
                        "titulo": titulo_limpo,
                        "tamanho": int(arq.get("size", 0)),
                        "numero_parte": _extrair_numero_parte(nome),
                    })

    # 2. Varre também a pasta de destino caso haja uploads diretos
    for item in itens_destino:
        nome = item.get("name", "")
        mime = item.get("mimeType", "")
        fid = item.get("id")

        if nome.lower() in pastas_ignorar or nome.startswith("Fernando"):
            continue

        if "video/" in mime or nome.lower().endswith((".mp4", ".mkv", ".mov", ".avi", ".ts")):
            titulo_limpo = sanitizar_nome_video(os.path.splitext(nome)[0])
            if not _ja_possui_cortes_no_drive(titulo_limpo, pasta_destino_id, token, mapa_pastas_destino=mapa_pastas_destino):
                if not any(p["id"] == fid for p in pendentes):
                    pendentes.append({
                        "id": fid,
                        "tipo": "arquivo_destino_raiz",
                        "pasta_fonte_id": pasta_destino_id,
                        "pasta_fonte_nome": "Destino",
                        "nome_arquivo": nome,
                        "titulo": titulo_limpo,
                        "tamanho": int(item.get("size", 0)),
                        "numero_parte": _extrair_numero_parte(nome),
                    })

    # Ordenação por nome da live e número da parte sequencial
    pendentes.sort(key=lambda x: (x.get("pasta_fonte_nome", ""), x.get("numero_parte", 9999), x.get("nome_arquivo", "")))
    with _processando_lock:
        em_andamento = set(_videos_em_processamento)
    pendentes = [p for p in pendentes if re.sub(r'\s+', ' ', str(p.get("titulo", ""))).strip().lower() not in em_andamento]
    return pendentes


def processar_video_drive(video_info, max_cortes=50, ao_progredir=None):
    """Executa o pipeline completo para um vídeo originado do Google Drive."""
    titulo = video_info["titulo"]
    fid = video_info["id"]
    nome_arq = video_info["nome_arquivo"]

    chave_proc = re.sub(r'\s+', ' ', str(titulo)).strip().lower()
    with _processando_lock:
        if chave_proc in _videos_em_processamento:
            log.info("Vídeo '%s' já está em processamento por outra thread/rotina. Ignorando chamada duplicada.", titulo)
            return {"sucesso": True, "duplicado": True, "titulo": titulo, "cortes_gerados": 0}
        _videos_em_processamento.add(chave_proc)

    log.info("Iniciando processamento de vídeo: '%s' (ID/Caminho: %s)...", titulo, fid)
    caminho_local_video = video_info.get("caminho_local")
    caminho_baixado_temporario = False

    if caminho_local_video and os.path.isfile(caminho_local_video):
        log.info("Vídeo fonte já disponível no disco local: '%s'. Pulando download do Drive!", caminho_local_video)
        if ao_progredir:
            ao_progredir(f"Vídeo fonte local identificado: {nome_arq}...", 0.05)
    else:
        if ao_progredir:
            ao_progredir(f"Baixando vídeo fonte do Google Drive: {nome_arq}...", 0.05)
        caminho_local_video = os.path.join(PASTA_DRIVE_TEMP, f"{fid}_{nome_arq}")
        ok_dl, err_dl = google_drive.baixar_arquivo_drive(fid, caminho_local_video)
        if not ok_dl or not os.path.isfile(caminho_local_video):
            log.error("Falha ao baixar vídeo %s do Drive: %s", nome_arq, err_dl)
            return {"sucesso": False, "erro": f"Falha no download: {err_dl}"}
        caminho_baixado_temporario = True

    try:
        # 1. Extração de áudio
        if ao_progredir:
            ao_progredir("Extraindo áudio do vídeo para transcrição...", 0.15)
        caminho_audio = extrair_audio_video(caminho_local_video)
        if not caminho_audio:
            return {"sucesso": False, "erro": "Falha ao extrair áudio"}

        # 2. Transcrição local
        if ao_progredir:
            ao_progredir("Transcrevendo falas e identificando diálogos...", 0.25)
        frases = transcrever_audio_local(caminho_audio)
        if not frases:
            return {"sucesso": False, "erro": "Transcrição veio vazia"}

        # 3. Blocagem com IA / Heurísticas
        if ao_progredir:
            ao_progredir("Dividindo em blocos narrativos e ranqueando trechos virais...", 0.40)
        contexto = f"VÍDEO: {titulo}\nORIGEM: {'Local' if not caminho_baixado_temporario else 'Google Drive'}\nARQUIVO: {nome_arq}"
        blocos, ignorados, modelos = blocador.dividir(frases, contexto)
        if not blocos:
            return {"sucesso": False, "erro": "Nenhum bloco narrativo identificado"}

        top_blocos = cortador_automatico.selecionar_top_blocos(blocos, limite=max_cortes)
        total = len(top_blocos)
        log.info("Vídeo '%s': %d blocos virais selecionados para corte.", titulo, total)

        # 4. Configuração de Cortes Ativa (Regra do Usuário: 3 modalidades completas)
        cfg_cortes = cortador_automatico.carregar_config_cortes()
        cfg_cortes["proporcao"] = "1:1"
        cfg_cortes["variacoes"] = {
            "com_legenda": True,   # Cortes com headline e legenda
            "sem_legenda": True,   # Cortes com headline
            "so_legenda": False,
            "cru": True,           # Cortes originais (+ .srt)
            "headlines": True,     # Arquivo .txt com sugestões adicionais
        }

        # 5. Geração Sequencial de Cortes com Numeração Segura
        pasta_base_drive = config.PASTA_GOOGLE_DRIVE
        id_pasta_raiz_drive = google_drive.obter_pasta_id_ativa() or PASTA_DESTINO_CORTES_PADRAO
        if "drive" not in cfg_cortes or not isinstance(cfg_cortes["drive"], dict):
            cfg_cortes["drive"] = {}
        cfg_cortes["drive"]["pasta_id"] = id_pasta_raiz_drive
        cfg_cortes["drive"]["upload_ativo"] = True
        token, _ = google_drive.obter_token_acesso()

        # Garante pastas hierárquicas no Drive conforme especificação do usuário:
        # Raiz / [Nome do Evento Consolidado] / [Subpastas de Modelo] / Arquivos FernandoXX
        nome_pasta_evento = google_drive.extrair_nome_evento_principal(titulo)
        id_pasta_evento_drive = google_drive.obter_ou_criar_pasta_drive(nome_pasta_evento, id_pai=id_pasta_raiz_drive, token=token)

        maior_fernando = google_drive.obter_maior_numero_fernando(pasta_base_drive, id_pasta_raiz_drive, token=token)
        proximo_numero = max(1, maior_fernando + 1)
        log.info("Processamento Drive '%s' (Evento: '%s'): Maior Fernando existente: %d. Iniciando em: %d", titulo, nome_pasta_evento, maior_fernando, proximo_numero)

        pacotes_gerados = []
        for idx, bloco in enumerate(top_blocos, start=1):
            prefixo = f"Fernando{proximo_numero:02d}"

            # REGRA CRÍTICA: Não sobrepor cortes que já existem no Drive ou localmente!
            while True:
                pasta_local_evento = os.path.join(pasta_base_drive, nome_pasta_evento)
                existe_local = False
                if os.path.isdir(pasta_local_evento):
                    for r, _, files in os.walk(pasta_local_evento):
                        if any(f.startswith(prefixo) for f in files):
                            existe_local = True
                            break
                if not existe_local and os.path.isdir(pasta_base_drive):
                    if os.path.exists(os.path.join(pasta_base_drive, prefixo)):
                        existe_local = True

                existe_drive = False
                if token:
                    if proximo_numero <= maior_fernando:
                        existe_drive = True
                    elif id_pasta_evento_drive:
                        for sub_nome in ["Corte com headline", "Corte com headline e legenda", "Corte cru", "Cortes com headline"]:
                            id_sub = google_drive.buscar_pasta_drive(sub_nome, id_pai=id_pasta_evento_drive, token=token)
                            if id_sub:
                                if google_drive.arquivo_existe_no_drive(f"{prefixo}_sem_legenda.mp4", id_sub, token=token) or \
                                   google_drive.arquivo_existe_no_drive(f"{prefixo}_com_legenda.mp4", id_sub, token=token) or \
                                   google_drive.arquivo_existe_no_drive(f"{prefixo}_cru.mp4", id_sub, token=token):
                                    existe_drive = True
                                    break

                if not existe_local and not existe_drive:
                    break
                log.info("Corte %s já existe no evento '%s'. Incrementando número para não sobrepor...", prefixo, nome_pasta_evento)
                proximo_numero += 1
                prefixo = f"Fernando{proximo_numero:02d}"

            pasta_corte = os.path.join(pasta_base_drive, prefixo)
            os.makedirs(pasta_corte, exist_ok=True)

            if ao_progredir:
                p_atual = 0.40 + (idx / total) * 0.55
                ao_progredir(f"Gerando corte {idx} de {total} ({prefixo}): headline + legenda + cru...", p_atual)

            pacote = cortador_automatico.gerar_pacote_corte(
                youtube_id=fid[:11] if len(fid) >= 11 else fid,
                info={"titulo": titulo, "youtube_id": fid},
                bloco=bloco,
                pasta_corte=pasta_corte,
                prefixo=prefixo,
                frases=frases,
                caminho_fonte=caminho_local_video,
                config_cortes=cfg_cortes,
            )
            pacotes_gerados.append(pacote)
            proximo_numero += 1

            # Libera espaço físico local do corte se upload na nuvem confirmou sucesso
            drive_upload = (pacote or {}).get("drive_upload") or {}
            if drive_upload.get("sucesso") or drive_upload.get("modo") == "nuvem":
                try:
                    for arq_corte in os.listdir(pasta_corte):
                        if arq_corte.endswith((".mp4", ".ts", ".mkv")):
                            c_arq = os.path.join(pasta_corte, arq_corte)
                            if os.path.isfile(c_arq):
                                os.remove(c_arq)
                except Exception as e_del:
                    log.debug("Aviso ao liberar espaço local do corte %s: %s", prefixo, e_del)

                # Também remove arquivos pesados de vídeo do espelho do vídeo em output/google_drive
                try:
                    for p_sub in [nome_pasta_evento, re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(titulo)).strip()]:
                        pasta_espelho_vid = os.path.join(pasta_base_drive, p_sub)
                        if os.path.isdir(pasta_espelho_vid):
                            for r, _, fs in os.walk(pasta_espelho_vid):
                                for f in fs:
                                    if f.startswith(prefixo) and f.endswith((".mp4", ".ts", ".mkv")):
                                        c_esp = os.path.join(r, f)
                                        if os.path.isfile(c_esp):
                                            os.remove(c_esp)
                except Exception as e_esp:
                    log.debug("Aviso ao limpar espelho do vídeo %s: %s", prefixo, e_esp)

        # 6. Liberação Automática de Disco (exclusão dos arquivos físicos intermediários e brutos temporários)
        log.info("Cortes de '%s' concluídos e confirmados no Drive. Liberando cache e arquivos temporários...", titulo)
        try:
            if caminho_baixado_temporario and os.path.exists(caminho_local_video):
                os.remove(caminho_local_video)
            if os.path.exists(caminho_audio):
                os.remove(caminho_audio)
            limpar_arquivos_drive_locais()
        except Exception as e_clean:
            log.warning("Aviso ao limpar temporários de %s: %s", titulo, e_clean)

        # 7. Salva registro histórico persistente
        try:
            caminho_hist = os.path.join(config.PASTA_DADOS, "automacao", "drive_processados.json")
            os.makedirs(os.path.dirname(caminho_hist), exist_ok=True)
            hist = {}
            if os.path.isfile(caminho_hist):
                with open(caminho_hist, "r", encoding="utf-8") as fh:
                    hist = json.load(fh)
            hist[titulo] = {
                "id": fid,
                "concluido_em": time.time(),
                "total_cortes": len(pacotes_gerados),
                "cortes": [p.get("prefixo") for p in pacotes_gerados if p.get("prefixo")],
            }
            with open(caminho_hist, "w", encoding="utf-8") as fh:
                json.dump(hist, fh, indent=2, ensure_ascii=False)
        except Exception as eh:
            log.warning("Aviso ao salvar histórico de drive: %s", eh)

        if ao_progredir:
            ao_progredir(f"Concluído! {len(pacotes_gerados)} cortes de '{titulo}' enviados para o Drive.", 1.0)

        return {
            "sucesso": True,
            "titulo": titulo,
            "cortes_gerados": len(pacotes_gerados),
            "pacotes": pacotes_gerados,
        }

    finally:
        with _processando_lock:
            _videos_em_processamento.discard(chave_proc)
        # Garante remoção de sobras temporárias se houver erro
        try:
            if caminho_baixado_temporario and os.path.exists(caminho_local_video):
                os.remove(caminho_local_video)
            if 'caminho_audio' in locals() and os.path.exists(caminho_audio):
                os.remove(caminho_audio)
        except Exception:
            pass


class VigilanteDriveContinuo:
    """Monitor contínuo que varre o Google Drive, baixa novos vídeos, gera cortes e sobe na nuvem."""

    def __init__(self, intervalo_s=30):
        self.intervalo_s = intervalo_s
        self._thread = None
        self._ativo = True
        self._em_processamento = None

    def iniciar(self):
        if self._thread and self._thread.is_alive():
            return
        self._ativo = True
        self._thread = threading.Thread(target=self._loop, name="vigilante-drive-continuo", daemon=True)
        self._thread.start()
        log.info("Vigilante contínuo do Google Drive iniciado com sucesso (intervalo: %ds).", self.intervalo_s)

    def parar(self):
        self._ativo = False

    def status(self):
        return {
            "ativo": self._ativo and self._thread is not None and self._thread.is_alive(),
            "em_processamento": self._em_processamento,
            "intervalo_s": self.intervalo_s,
        }

    def executar_uma_vez(self):
        """Executa uma rodada imediata de verificação e processamento de vídeos do Drive."""
        try:
            pendentes = escanear_videos_pendentes_drive()
            resultados = []
            if pendentes:
                log.info("Vigilante Drive (execução imediata): %d vídeos pendentes.", len(pendentes))
                for vid in pendentes:
                    self._em_processamento = vid["titulo"]
                    res = processar_video_drive(vid)
                    resultados.append({"titulo": vid["titulo"], "resultado": res})
                    self._em_processamento = None
                    if res.get("sucesso"):
                        try:
                            cortador_automatico.definir_modo_automatico(False)
                        except Exception:
                            pass
            return {"pendentes": len(pendentes), "processados": resultados}
        except Exception as e:
            self._em_processamento = None
            log.error("Erro na execução imediata do Vigilante Drive: %s", e)
            return {"erro": str(e)}

    def _loop(self):
        time.sleep(5)
        while self._ativo:
            try:
                pendentes = escanear_videos_pendentes_drive()
                if pendentes:
                    log.info("Vigilante Drive: %d vídeos pendentes encontrados no Google Drive.", len(pendentes))
                    for vid in pendentes:
                        if not self._ativo:
                            break
                        self._em_processamento = vid["titulo"]
                        res = processar_video_drive(vid)
                        log.info("Vigilante Drive: resultado para %s: %s", vid["titulo"], res.get("sucesso"))
                        self._em_processamento = None
                        
                        # Se processou um vídeo do Drive com sucesso, desativa modo YouTube conforme instrução do operador
                        if res.get("sucesso"):
                            try:
                                cortador_automatico.definir_modo_automatico(False)
                                log.info("Modo automático do YouTube desativado com sucesso (prioridade: Google Drive).")
                            except Exception:
                                pass
            except Exception as e:
                log.warning("Erro no loop do Vigilante Drive: %s", e)
                self._em_processamento = None

            for _ in range(max(1, int(self.intervalo_s))):
                if not self._ativo:
                    break
                time.sleep(1)


# Instância global do vigilante do Drive
vigilante_drive = VigilanteDriveContinuo()
