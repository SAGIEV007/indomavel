"""Sistema de cortes sequenciais inteligentes: pacotes quádruplos FernandoXX,
organização para Google Drive, limpeza automática de arquivos corrompidos e controle manual/automático.
"""

import json
import logging
import os
import queue
import re
import shutil
import threading
import time

from . import acervo_local, config, google_drive, headlines, legenda, recorte, render, youtube

log = logging.getLogger("indomavel.cortador_automatico")


def caminho_config_cortes():
    return os.path.join(config.PASTA_DADOS, "automacao", "config_cortes.json")


CONFIG_CORTES_PADRAO = {
    "proporcao": "1:1",
    "video_inicial_id": "",
    "variacoes": {
        "com_legenda": True,
        "sem_legenda": False,
        "so_legenda": True,
        "cru": True,
        "headlines": True,
    },
    "visual": {
        "marca_dagua": False,
        "cortar_topo": 140,
        "estilo_card": "padrao",
        "tamanho_headline": 44,
        "tamanho_tag": 60,
    },
    "drive": {
        "pasta_id": google_drive.ID_PASTA_PADRAO,
        "upload_ativo": True,
    },
    "max_cortes_por_video": 4,
    "modo_automatico": True,
}


def carregar_config_cortes():
    """Carrega as preferências de cortes automáticos e Google Drive."""
    caminho = caminho_config_cortes()
    if not os.path.exists(caminho):
        return dict(CONFIG_CORTES_PADRAO)
    try:
        with open(caminho, encoding="utf-8") as f:
            carregado = json.load(f)
        resultado = dict(CONFIG_CORTES_PADRAO)
        resultado.update({k: v for k, v in carregado.items() if k not in ("variacoes", "visual", "drive")})
        if "variacoes" in carregado and isinstance(carregado["variacoes"], dict):
            resultado["variacoes"] = dict(CONFIG_CORTES_PADRAO["variacoes"])
            resultado["variacoes"].update(carregado["variacoes"])
        if "visual" in carregado and isinstance(carregado["visual"], dict):
            resultado["visual"] = dict(CONFIG_CORTES_PADRAO["visual"])
            resultado["visual"].update(carregado["visual"])
        if "drive" in carregado and isinstance(carregado["drive"], dict):
            resultado["drive"] = dict(CONFIG_CORTES_PADRAO["drive"])
            resultado["drive"].update(carregado["drive"])
        if "modo_automatico" in carregado:
            resultado["modo_automatico"] = bool(carregado["modo_automatico"])
        return resultado
    except Exception as e:
        log.warning("Erro ao ler %s: %s", caminho, e)
        return dict(CONFIG_CORTES_PADRAO)


def salvar_config_cortes(dados):
    """Salva as preferências em dados/automacao/config_cortes.json."""
    atual = carregar_config_cortes()
    if isinstance(dados, dict):
        if "proporcao" in dados:
            prop = str(dados["proporcao"]).strip()
            if prop in ("1:1", "9:16", "4:5", "3:4", "16:9"):
                atual["proporcao"] = prop
        if "max_cortes_por_video" in dados:
            try:
                atual["max_cortes_por_video"] = max(1, min(100, int(dados["max_cortes_por_video"])))
            except (ValueError, TypeError):
                pass
        if "modo_automatico" in dados:
            atual["modo_automatico"] = bool(dados["modo_automatico"])
        if "variacoes" in dados and isinstance(dados["variacoes"], dict):
            for k in ("com_legenda", "sem_legenda", "so_legenda", "cru", "headlines"):
                if k in dados["variacoes"]:
                    atual["variacoes"][k] = bool(dados["variacoes"][k])
        if "visual" in dados and isinstance(dados["visual"], dict):
            vis = dados["visual"]
            if "marca_dagua" in vis:
                atual["visual"]["marca_dagua"] = bool(vis["marca_dagua"])
            if "cortar_topo" in vis:
                try:
                    atual["visual"]["cortar_topo"] = max(0, min(300, int(vis["cortar_topo"])))
                except (ValueError, TypeError):
                    pass
            if "tamanho_headline" in vis:
                try:
                    atual["visual"]["tamanho_headline"] = max(20, min(110, int(vis["tamanho_headline"])))
                except (ValueError, TypeError):
                    pass
            if "estilo_card" in vis:
                atual["visual"]["estilo_card"] = str(vis["estilo_card"])[:50]
        if "video_inicial_id" in dados:
            atual["video_inicial_id"] = str(dados["video_inicial_id"] or "").strip()
        if "drive" in dados and isinstance(dados["drive"], dict):
            drv = dados["drive"]
            if "pasta_id" in drv and drv["pasta_id"]:
                atual["drive"]["pasta_id"] = str(drv["pasta_id"]).strip()
            if "upload_ativo" in drv:
                atual["drive"]["upload_ativo"] = bool(drv["upload_ativo"])

    caminho = caminho_config_cortes()
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(atual, f, indent=2, ensure_ascii=False)
    return atual


def video_eh_elegivel_por_ponto_partida(youtube_id):
    """Verifica se um vídeo deve ser cortado automaticamente baseado na configuração de ponto de partida."""
    cfg = carregar_config_cortes()
    alvo = (cfg.get("video_inicial_id") or "").strip()
    if not alvo:
        return True  # Sem restrição: todos os vídeos são elegíveis

    if alvo == "__apenas_novos__":
        # Apenas vídeos detectados após a ativação desta opção
        return True

    caminho_reg = os.path.join(config.PASTA_DADOS, "playlist_aovivo.json")
    if not os.path.exists(caminho_reg):
        return True

    try:
        with open(caminho_reg, encoding="utf-8") as f:
            reg = json.load(f)
        videos = reg.get("videos", [])
        ts_alvo = None
        for v in videos:
            if v.get("youtube_id") == alvo:
                ts_alvo = float(v.get("adicionado_em") or 0)
                break
        if ts_alvo is None:
            return True

        for v in videos:
            if v.get("youtube_id") == youtube_id:
                ts_vid = float(v.get("adicionado_em") or 0)
                return ts_vid >= ts_alvo
        return True
    except Exception as e:
        log.warning("Erro ao verificar elegibilidade de %s: %s", youtube_id, e)
        return True


def carregar_modo_automatico():
    try:
        cfg = carregar_config_cortes()
        return bool(cfg.get("modo_automatico", True))
    except Exception:
        return True


# Estado global do modo automático (persistido em config_cortes.json)
_MODO_AUTOMATICO = carregar_modo_automatico()
_TRAVA_MODO = threading.Lock()


def obter_modo_automatico():
    """Retorna se o modo de cortes automáticos está ativo."""
    global _MODO_AUTOMATICO
    with _TRAVA_MODO:
        _MODO_AUTOMATICO = carregar_modo_automatico()
        return _MODO_AUTOMATICO


def definir_modo_automatico(ativo):
    """Ativa ou desativa o modo de cortes automáticos."""
    global _MODO_AUTOMATICO
    with _TRAVA_MODO:
        _MODO_AUTOMATICO = bool(ativo)
        salvar_config_cortes({"modo_automatico": _MODO_AUTOMATICO})
        log.info("Modo de cortes automáticos alterado para: %s", "LIGADO" if _MODO_AUTOMATICO else "DESLIGADO")
        return _MODO_AUTOMATICO


def score_bloco(bloco):
    """Calcula pontuação de adequação para corte vertical (0 a 150+)."""
    score = float(bloco.get("potencial") or 50.0)
    duracao = float(bloco.get("duracao") or (float(bloco.get("fim", 0)) - float(bloco.get("inicio", 0))))

    # Bônus para o Renan falando (se confirmado)
    if bloco.get("renan_falando"):
        score += 25.0

    # Bônus para bloco autocontido (não precisa de contexto externo)
    if not bloco.get("precisa_contexto"):
        score += 20.0

    # Faixa de ouro para retenção no Reels/TikTok/Shorts (30s a 80s)
    if 30.0 <= duracao <= 80.0:
        score += 20.0
    elif 20.0 <= duracao < 30.0 or 80.0 < duracao <= 100.0:
        score += 10.0
    elif duracao > 120.0:
        score -= 15.0

    # Bônus se possui momentos fortes marcados
    if bloco.get("destaques"):
        score += min(15.0, len(bloco["destaques"]) * 5.0)

    return score


def selecionar_top_blocos(blocos, limite=4):
    """Filtra e ranqueia os melhores blocos para formato vertical 9:16."""
    candidatos = []
    for b in blocos:
        duracao = float(b.get("duracao") or (float(b.get("fim", 0)) - float(b.get("inicio", 0))))
        if 15.0 <= duracao <= 130.0:
            candidatos.append(b)

    if not candidatos:
        candidatos = list(blocos)

    candidatos.sort(key=score_bloco, reverse=True)
    return candidatos[:limite]


def proxima_pasta_fernando(pasta_base=None):
    """Localiza e cria a próxima pasta sequencial FernandoXX sem colisões de número."""
    pasta_base = pasta_base or config.PASTA_GOOGLE_DRIVE
    os.makedirs(pasta_base, exist_ok=True)
    existentes = []
    padrao = re.compile(r"^Fernando(\d+)$", re.IGNORECASE)
    with os.scandir(pasta_base) as it:
        for entrada in it:
            if entrada.is_dir():
                m = padrao.match(entrada.name)
                if m:
                    existentes.append(int(m.group(1)))
    prox_num = (max(existentes) + 1) if existentes else 1
    nome_pasta = f"Fernando{prox_num:02d}"
    pasta_corte = os.path.join(pasta_base, nome_pasta)
    os.makedirs(pasta_corte, exist_ok=True)
    return prox_num, pasta_corte


def limpar_arquivos_incompletos(pastas=None):
    """Remove resíduos temporários (.tmp, .part) e arquivos vazios (0 bytes) gerados por interrupções."""
    import tempfile
    if pastas is None:
        pastas = [config.PASTA_GOOGLE_DRIVE, config.PASTA_OUTPUT_CORTES, config.PASTA_DOWNLOADS, config.PASTA_VIDEOS, tempfile.gettempdir()]

    extensoes_temp = {".tmp", ".temp", ".partial", ".part", ".ytdl", ".crdownload"}
    removidos = []

    for pasta in pastas:
        if not os.path.exists(pasta):
            continue
        for raiz, dirs, arquivos in os.walk(pasta, topdown=False):
            for arq in arquivos:
                caminho = os.path.join(raiz, arq)
                _, ext = os.path.splitext(arq.lower())
                try:
                    deve_remover = False
                    if ext in extensoes_temp:
                        deve_remover = True
                    elif ext in (".mp4", ".srt") and os.path.getsize(caminho) == 0:
                        deve_remover = True

                    if deve_remover:
                        os.remove(caminho)
                        removidos.append(caminho)
                except Exception:
                    pass

            for d in dirs:
                caminho_dir = os.path.join(raiz, d)
                if re.match(r"^Fernando\d+$", d, re.IGNORECASE) or d.startswith(("indomavel_", "troca_audio_")):
                    try:
                        if not os.listdir(caminho_dir):
                            os.rmdir(caminho_dir)
                            removidos.append(caminho_dir)
                    except Exception:
                        pass

    if removidos:
        log.info("Limpeza de inicialização: %d arquivos/pastas incompletos removidos", len(removidos))
    return removidos


def caminho_registro(youtube_id):
    pasta = acervo_local.pasta_do_video(youtube_id)
    return os.path.join(pasta, "cortes_automaticos.json")


def obter_status_cortes(youtube_id):
    caminho = caminho_registro(youtube_id)
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def salvar_status_cortes(youtube_id, dados):
    caminho = caminho_registro(youtube_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def verificar_pacote_completo(pasta_corte, prefixo=None, variacoes=None):
    """Verifica se o pacote FernandoXX na pasta de destino (Google Drive) foi gerado integralmente com tamanho > 0.

    Respeita as variações ativas solicitadas pelo usuário (com_legenda, sem_legenda, so_legenda, cru, headlines).
    """
    if not pasta_corte or not os.path.isdir(pasta_corte):
        return False

    if not prefixo:
        prefixo = os.path.basename(os.path.normpath(pasta_corte))

    if variacoes is not None:
        ativas = variacoes
    else:
        # Se não fornecido diretamente, tenta ler os metadados do pacote gerado
        meta_path = os.path.join(pasta_corte, "pacote_info.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    ativas = json.load(f).get("variacoes")
            except Exception:
                ativas = None
        else:
            ativas = None

    if ativas is not None:
        arquivos_obrigatorios = []
        if ativas.get("com_legenda"):
            arquivos_obrigatorios.append(f"{prefixo}_com_legenda.mp4")
        if ativas.get("sem_legenda"):
            if os.path.isfile(os.path.join(pasta_corte, f"{prefixo}_headline.mp4")):
                arquivos_obrigatorios.append(f"{prefixo}_headline.mp4")
            else:
                arquivos_obrigatorios.append(f"{prefixo}_sem_legenda.mp4")
        if ativas.get("so_legenda"):
            arquivos_obrigatorios.append(f"{prefixo}_so_legenda.mp4")
        if ativas.get("cru"):
            arquivos_obrigatorios.append(f"{prefixo}_cru.mp4")
            arquivos_obrigatorios.append(f"{prefixo}_cru.srt")
        if ativas.get("headlines"):
            arquivos_obrigatorios.append(f"{prefixo}_headlines.txt")
    else:
        # Modo legado (exige os 5 arquivos clássicos para compatibilidade estrita de testes)
        arquivos_obrigatorios = [
            f"{prefixo}_com_legenda.mp4",
            f"{prefixo}_sem_legenda.mp4",
            f"{prefixo}_cru.mp4",
            f"{prefixo}_cru.srt",
            f"{prefixo}_headlines.txt",
        ]

    if not arquivos_obrigatorios:
        return False

    for nome in arquivos_obrigatorios:
        caminho = os.path.join(pasta_corte, nome)
        if not os.path.isfile(caminho):
            log.warning("Arquivo obrigatório ausente no pacote %s: %s", prefixo, caminho)
            return False
        try:
            if os.path.getsize(caminho) <= 0:
                log.warning("Arquivo com 0 bytes no pacote %s: %s", prefixo, caminho)
                return False
        except OSError as err:
            log.warning("Erro ao ler tamanho de %s no pacote %s: %s", caminho, prefixo, err)
            return False

    return True


def liberar_cache_video(youtube_id):
    """Libera arquivos brutos e intermediários baixados localmente (PASTA_VIDEOS/<id>.mp4 e temporários).

    Só deve ser executado após confirmação de que os cortes foram salvos com sucesso na pasta de destino (Google Drive).
    """
    bytes_liberados = 0

    # 1. Remove vídeo bruto completo de PASTA_VIDEOS (<id>.mp4 e ficha <id>.json)
    try:
        bytes_liberados += youtube.remover_video_cache(youtube_id)
    except Exception as e:
        log.warning("Erro ao remover cache do vídeo %s de PASTA_VIDEOS: %s", youtube_id, e)

    # 2. Remove eventuais trechos intermediários em PASTA_DOWNLOADS correspondentes a este vídeo
    if os.path.isdir(config.PASTA_DOWNLOADS):
        try:
            for arq in os.listdir(config.PASTA_DOWNLOADS):
                caminho = os.path.join(config.PASTA_DOWNLOADS, arq)
                if not os.path.isfile(caminho):
                    continue
                if youtube_id in arq:
                    try:
                        sz = os.path.getsize(caminho)
                        os.remove(caminho)
                        bytes_liberados += sz
                        log.info("Trecho temporário removido em downloads: %s", caminho)
                    except Exception as e:
                        log.warning("Não foi possível remover temporário %s: %s", caminho, e)
        except Exception as e:
            log.warning("Erro ao varrer downloads para limpeza do vídeo %s: %s", youtube_id, e)

    # 3. Limpeza preventiva de arquivos residuais (.tmp, .part, 0-bytes)
    try:
        limpar_arquivos_incompletos([config.PASTA_DOWNLOADS, config.PASTA_VIDEOS])
    except Exception as e:
        log.warning("Aviso durante limpeza residual pós-exclusão: %s", e)

    log.info("Cache liberado para %s: %.2f MB liberados.", youtube_id, bytes_liberados / (1024 * 1024))
    return bytes_liberados


def limpar_cache_cortes_concluidos():
    """Varre todos os registros de cortes; se os pacotes estiverem íntegros no Drive e houver cache bruto, remove."""
    removidos_total = 0
    if not os.path.isdir(config.PASTA_DADOS):
        return 0

    for item in os.listdir(config.PASTA_DADOS):
        pasta_v = os.path.join(config.PASTA_DADOS, item)
        if not os.path.isdir(pasta_v):
            continue
        status = obter_status_cortes(item)
        if not status or status.get("estado") != "concluido":
            continue
        pacotes = status.get("pacotes") or []
        if not pacotes:
            continue
        if all(verificar_pacote_completo(p.get("pasta"), p.get("prefixo")) for p in pacotes):
            bytes_lib = liberar_cache_video(item)
            if bytes_lib > 0 or not status.get("cache_liberado"):
                status["cache_liberado"] = True
                status["bytes_liberados"] = (status.get("bytes_liberados") or 0) + bytes_lib
                status["atualizado_em"] = time.time()
                salvar_status_cortes(item, status)
            removidos_total += bytes_lib
    return removidos_total


def gerar_pacote_corte(youtube_id, info, bloco, pasta_corte, prefixo, frases=None, caminho_fonte=None, ao_progredir=None, config_cortes=None):
    """Gera o pacote de corte dentro da pasta FernandoXX respeitando as preferências do operador:
    1. FernandoXX_cru.mp4 (+ FernandoXX_cru.srt) (opcional)
    2. FernandoXX_headlines.txt (opcional)
    3. FernandoXX_com_legenda.mp4 (com card de headline e legendas) (opcional)
    4. FernandoXX_sem_legenda.mp4 (com card de headline e sem legendas) (opcional, desmarcado por padrão)
    5. FernandoXX_so_legenda.mp4 (sem card de headline, só vídeo + legenda) (opcional)

    Suporta proporções: 1:1 (padrão inicial), 9:16, 4:5, 3:4, 16:9.
    Envia para o Google Drive separando cada variação em sua respectiva pasta.
    """
    os.makedirs(pasta_corte, exist_ok=True)

    if config_cortes is None:
        config_cortes = carregar_config_cortes()

    formato = config_cortes.get("proporcao", "1:1")
    if formato not in render.FORMATOS:
        formato = "1:1"

    variacoes = dict(CONFIG_CORTES_PADRAO["variacoes"])
    if "variacoes" in config_cortes and isinstance(config_cortes["variacoes"], dict):
        variacoes.update(config_cortes["variacoes"])

    visual = dict(CONFIG_CORTES_PADRAO["visual"])
    if "visual" in config_cortes and isinstance(config_cortes["visual"], dict):
        visual.update(config_cortes["visual"])

    marca_dagua = bool(visual.get("marca_dagua", False))
    cortar_topo = int(visual.get("cortar_topo", 140))
    tamanho_headline = int(visual.get("tamanho_headline", 44))
    tamanho_tag = int(visual.get("tamanho_tag", 60))

    titulo_video = info.get("titulo") or youtube_id
    titulo_bloco = bloco.get("titulo") or f"Corte {prefixo}"
    inicio = max(0.0, float(bloco.get("inicio", 0.0)))
    fim = float(bloco.get("fim", inicio + 60.0))
    duracao = max(0.1, fim - inicio)

    renan_falando = bool(bloco.get("renan_falando", False))
    locutor = bloco.get("locutor") or ""

    if not caminho_fonte or not os.path.exists(caminho_fonte):
        caminho_fonte = youtube.baixar_video_maximo(youtube_id)

    arquivos_gerados = {}
    inicio_real = inicio

    # 1. Corte Cru sem perda de qualidade (se ativo)
    caminho_cru = os.path.join(pasta_corte, f"{prefixo}_cru.mp4")
    caminho_srt = os.path.join(pasta_corte, f"{prefixo}_cru.srt")
    if variacoes.get("cru", True):
        inicio_real = recorte.recortar_sem_perda(caminho_fonte, inicio, fim, caminho_cru)
        conteudo_srt = ""
        if frases:
            try:
                conteudo_srt = legenda.srt(frases, inicio_real, fim)
            except Exception as e:
                log.warning("Falha ao gerar .srt para %s: %s", prefixo, e)

        if not conteudo_srt or not conteudo_srt.strip():
            dur_trecho = max(1.0, fim - inicio_real)
            conteudo_srt = f"1\n00:00:00,000 --> {legenda._tempo(dur_trecho)}\n{titulo_bloco}\n"

        try:
            with open(caminho_srt, "w", encoding="utf-8") as f:
                f.write(conteudo_srt)
        except Exception as e:
            log.warning("Falha ao salvar %s: %s", caminho_srt, e)

        arquivos_gerados["cru"] = os.path.basename(caminho_cru)
        if os.path.exists(caminho_srt):
            arquivos_gerados["srt"] = os.path.basename(caminho_srt)

    # 2. Transcrição do corte e verificação de áudio
    texto_trecho = ""
    if frases:
        frases_trecho = [
            f.get("texto", "") for f in frases
            if f.get("inicio", 0.0) >= (inicio - 1.0) and f.get("fim", 0.0) <= (fim + 1.0)
        ]
        texto_trecho = " ".join(frases_trecho).strip()
    if not texto_trecho:
        texto_trecho = (bloco.get("transcricao") or "").strip()

    tem_fala = len(texto_trecho) > 5

    # 3. Sugestões de headlines com arquétipos virais e auto-avaliação
    resp_headlines = headlines.sugerir(
        titulo=titulo_bloco,
        resumo=bloco.get("resumo") or "",
        texto_trecho=texto_trecho,
        categoria=bloco.get("categoria") or "",
        temas=bloco.get("temas") or [],
        destaques=[d.get("texto", "") for d in bloco.get("destaques") or []],
        renan_falando=renan_falando,
        locutor=locutor,
        limite_sugestoes=8,
    )
    opcoes = resp_headlines.get("opcoes") or []
    if not opcoes:
        opcoes = headlines.headlines_heuristicas(
            titulo=titulo_bloco, resumo=bloco.get("resumo") or "",
            categoria=bloco.get("categoria") or "", temas=bloco.get("temas") or [],
            renan_falando=renan_falando, locutor=locutor, limite=8,
        )

    caminho_txt = os.path.join(pasta_corte, f"{prefixo}_headlines.txt")
    if variacoes.get("headlines", True):
        locutor_desc = "Renan Santos (Partido Missão)" if renan_falando else (locutor if locutor else "Geral / Não atribuído a Renan")
        status_audio = "Fala identificada na transcrição" if tem_fala else "Atenção: Vídeo sem falas significativas detectadas"

        linhas_txt = [
            "============================================================",
            f"SUGESTÕES DE HEADLINES — {prefixo}",
            "============================================================",
            f"Vídeo: {titulo_video} ({youtube_id})",
            f"Trecho: {inicio:.1f}s a {fim:.1f}s ({duracao:.1f}s)",
            f"Locutor identificado: {locutor_desc}",
            f"Status de fala: {status_audio}",
            f"Formato Renderizado: {formato}",
            "------------------------------------------------------------",
            f"OPÇÕES DE HEADLINE GERADAS ({len(opcoes)} opções ranqueadas):",
            "------------------------------------------------------------",
        ]
        for idx, opc in enumerate(opcoes, start=1):
            score_txt = f" | CTR Estimado: {opc.get('score_ctr', 8)}/10" if opc.get("score_ctr") else ""
            gatilho_txt = f" | Gatilho: {opc.get('gatilho', '')}" if opc.get("gatilho") else ""
            linhas_txt.append(f"\n[Opção {idx}] - Ângulo: {opc.get('angulo', 'geral')}{score_txt}{gatilho_txt}")
            linhas_txt.append(f"TAG:      {opc.get('tag', 'EM ALTA!')}")
            linhas_txt.append(f"HEADLINE: {opc.get('headline', '')}")

        with open(caminho_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas_txt) + "\n")
        arquivos_gerados["headlines"] = os.path.basename(caminho_txt)

    top_tag = opcoes[0].get("tag", "EM ALTA!")
    top_headline = opcoes[0].get("headline", titulo_bloco)

    estilo_base = {
        "card": True,
        "tag": top_tag,
        "headline": top_headline,
        "tamanho_tag": tamanho_tag,
        "tamanho_headline": tamanho_headline,
        "cortar_topo": cortar_topo,
        "legenda": True,
        "fonte_legenda": "bebas",
        "cor_destaque": "#FFD60A",
        "destacar_palavra": True,
        "rodape": marca_dagua,
        "enquadramento_x": 0.0,
        "enquadramento_y": 0.0,
        "zoom": 1.0,
        "posicao_legenda": 0.72,
        "maiusculas": True,
        "max_palavras": 3,
        "cor_legenda": "#FFFFFF",
    }
    estilo_base = render.normalizar_estilo(estilo_base)

    trechos_legenda = []
    if frases:
        try:
            trechos_legenda = legenda.trechos_de_frases(frases, inicio, fim, max_palavras=3)
        except Exception:
            trechos_legenda = []

    def _exec_render(estilo_render, trechos_render, nome_arq, titulo_corte):
        try:
            return render.exportar(
                youtube_id=youtube_id,
                inicio=inicio,
                fim=fim,
                formato=formato,
                estilo=estilo_render,
                trechos=trechos_render,
                pasta_saida=pasta_corte,
                titulo=titulo_corte,
                ao_progredir=ao_progredir,
                nome_arquivo=nome_arq,
                caminho_fonte=caminho_fonte,
            )
        except TypeError:
            return render.exportar(
                youtube_id=youtube_id,
                inicio=inicio,
                fim=fim,
                formato=formato,
                estilo=estilo_render,
                trechos=trechos_render,
                pasta_saida=pasta_corte,
                titulo=titulo_corte,
                ao_progredir=ao_progredir,
                nome_arquivo=nome_arq,
            )

    # 4. Render COM legenda (card + headline + legenda)
    if variacoes.get("com_legenda", True):
        try:
            estilo_com = dict(estilo_base)
            estilo_com["card"] = True
            estilo_com["legenda"] = True
            caminho_com_legenda = _exec_render(
                estilo_render=estilo_com,
                trechos_render=trechos_legenda,
                nome_arq=f"{prefixo}_com_legenda.mp4",
                titulo_corte=f"{prefixo}_com_legenda",
            )
            arquivos_gerados["com_legenda"] = os.path.basename(caminho_com_legenda)
        except Exception as err_render:
            log.warning("Falha ao renderizar versão com legenda para %s: %s", prefixo, err_render)

    # 5. Render SEM legenda (card + headline, sem legenda) - Opcional
    if variacoes.get("sem_legenda", False):
        try:
            estilo_sem = dict(estilo_base)
            estilo_sem["card"] = True
            estilo_sem["legenda"] = False
            caminho_sem_legenda = _exec_render(
                estilo_render=estilo_sem,
                trechos_render=[],
                nome_arq=f"{prefixo}_sem_legenda.mp4",
                titulo_corte=f"{prefixo}_sem_legenda",
            )
            arquivos_gerados["sem_legenda"] = os.path.basename(caminho_sem_legenda)
        except Exception as err_render:
            log.warning("Falha ao renderizar versão sem legenda para %s: %s", prefixo, err_render)

    # 6. Render SÓ legenda (sem card de headline, apenas o vídeo com legenda)
    if variacoes.get("so_legenda", True):
        try:
            estilo_so = dict(estilo_base)
            estilo_so["card"] = False
            estilo_so["legenda"] = True
            caminho_so_legenda = _exec_render(
                estilo_render=estilo_so,
                trechos_render=trechos_legenda,
                nome_arq=f"{prefixo}_so_legenda.mp4",
                titulo_corte=f"{prefixo}_so_legenda",
            )
            arquivos_gerados["so_legenda"] = os.path.basename(caminho_so_legenda)
        except Exception as err_render:
            log.warning("Falha ao renderizar versão só legenda para %s: %s", prefixo, err_render)

    # Grava ficha técnica do pacote para permitir validação precisa de arquivos gerados
    info_meta = {
        "prefixo": prefixo,
        "formato": formato,
        "variacoes": variacoes,
        "arquivos": arquivos_gerados,
        "gerado_em": time.time(),
    }
    with open(os.path.join(pasta_corte, "pacote_info.json"), "w", encoding="utf-8") as f:
        json.dump(info_meta, f, indent=2, ensure_ascii=False)

    # 7. Sincronização / Upload para o Google Drive por categoria
    drive_pasta_id = config_cortes.get("drive", {}).get("pasta_id", google_drive.ID_PASTA_PADRAO)
    upload_ativo = config_cortes.get("drive", {}).get("upload_ativo", True)
    drive_res = None
    if upload_ativo:
        drive_res = google_drive.enviar_pacote_drive(
            pasta_corte=pasta_corte,
            prefixo=prefixo,
            variacoes_ativas=variacoes,
            pasta_id_raiz=drive_pasta_id,
            titulo_video=titulo_video,
        )

    return {
        "prefixo": prefixo,
        "pasta": pasta_corte,
        "bloco_id": bloco.get("id"),
        "inicio": inicio,
        "fim": fim,
        "duracao": duracao,
        "formato": formato,
        "renan_falando": renan_falando,
        "locutor": locutor,
        "tag": top_tag,
        "headline": top_headline,
        "variacoes": variacoes,
        "arquivos": arquivos_gerados,
        "drive_upload": drive_res,
    }


class ExecutorCortesSequencial:
    """Fila de execução com ordem sequencial estrita: conclui todos os cortes de um vídeo antes de iniciar o próximo."""

    def __init__(self):
        self._fila = queue.Queue()
        self._trava = threading.Lock()
        self._video_em_andamento = None
        self._thread = threading.Thread(target=self._loop_trabalho, name="executor-cortes-sequencial", daemon=True)
        self._thread.start()

    def enfileirar(self, youtube_id, info, blocos, frases=None, max_cortes=4, refazer=False):
        """Enfileira um vídeo para geração sequencial de cortes."""
        item = {
            "youtube_id": youtube_id,
            "info": info or {},
            "blocos": blocos or [],
            "frases": frases,
            "max_cortes": max_cortes,
            "refazer": refazer,
            "enfileirado_em": time.time(),
        }
        with self._trava:
            # Registra estado inicial
            status = obter_status_cortes(youtube_id) or {}
            status.update({
                "youtube_id": youtube_id,
                "estado": "em_fila",
                "mensagem": "Na fila para geração sequencial de cortes",
                "atualizado_em": time.time(),
            })
            salvar_status_cortes(youtube_id, status)
            self._fila.put(item)
        log.info("Vídeo %s enfileirado para cortes sequenciais (refazer=%s)", youtube_id, refazer)
        return status

    def status_atual(self):
        with self._trava:
            return {
                "video_em_andamento": self._video_em_andamento,
                "videos_na_fila": self._fila.qsize(),
            }

    def _loop_trabalho(self):
        while True:
            item = self._fila.get()
            youtube_id = item["youtube_id"]
            with self._trava:
                self._video_em_andamento = youtube_id

            try:
                self._executar_video(item)
            except Exception as erro:
                log.exception("Erro crítico no processamento sequencial de cortes de %s: %s", youtube_id, erro)
                try:
                    limpar_arquivos_incompletos()
                except Exception:
                    pass
                status = obter_status_cortes(youtube_id) or {}
                status.update({
                    "estado": "falhou",
                    "erro": str(erro),
                    "atualizado_em": time.time(),
                })
                salvar_status_cortes(youtube_id, status)
            finally:
                with self._trava:
                    self._video_em_andamento = None
                self._fila.task_done()

    def _executar_video(self, item):
        youtube_id = item["youtube_id"]
        info = item["info"]
        blocos = item["blocos"]
        frases = item["frases"]
        max_cortes = item["max_cortes"]
        refazer = item["refazer"]

        # Se frases não foram fornecidas diretamente, tenta recuperar do acervo local
        if not frases:
            dados_frases = acervo_local.ler(youtube_id, "frases.json")
            if dados_frases and "frases" in dados_frases:
                frases = dados_frases["frases"]

        # 1. Limpeza preventiva de arquivos temporários antes de iniciar
        limpar_arquivos_incompletos()

        status = obter_status_cortes(youtube_id) or {
            "youtube_id": youtube_id,
            "criado_em": time.time(),
        }

        # Se for refazer do zero, limpa pastas anteriores deste vídeo se existirem
        if refazer:
            pastas_anteriores = [pkg.get("pasta") for pkg in status.get("pacotes", []) if pkg.get("pasta")]
            for p in pastas_anteriores:
                if os.path.exists(p):
                    try:
                        shutil.rmtree(p)
                        log.info("Pasta de corte anterior removida para refazer: %s", p)
                    except Exception as e:
                        log.warning("Não foi possível apagar pasta anterior %s: %s", p, e)
            status["pacotes"] = []

        cfg_cortes = carregar_config_cortes()
        if not max_cortes:
            max_cortes = cfg_cortes.get("max_cortes_por_video", 4)

        top_blocos = selecionar_top_blocos(blocos, limite=max_cortes)
        total = len(top_blocos)
        if total == 0:
            status.update({
                "estado": "concluido",
                "mensagem": "Nenhum bloco disponível para cortes",
                "atualizado_em": time.time(),
            })
            salvar_status_cortes(youtube_id, status)
            return

        status.update({
            "estado": "processando",
            "total_blocos": total,
            "feitos": 0,
            "mensagem": "Baixando vídeo fonte em 1080p para cortes sequenciais...",
            "atualizado_em": time.time(),
        })
        salvar_status_cortes(youtube_id, status)

        # Baixa vídeo completo na qualidade máxima uma única vez
        caminho_fonte = youtube.baixar_video_maximo(youtube_id)

        pacotes = []
        for idx, bloco in enumerate(top_blocos, start=1):
            prop_str = cfg_cortes.get("proporcao", "1:1")
            status.update({
                "feitos": idx - 1,
                "mensagem": f"Gerando pacote {idx} de {total} (cortes secos, headlines e render {prop_str})...",
                "atualizado_em": time.time(),
            })
            salvar_status_cortes(youtube_id, status)

            numero, pasta_corte = proxima_pasta_fernando(config.PASTA_GOOGLE_DRIVE)
            prefixo = f"Fernando{numero:02d}"

            pct_base = round((idx - 1) / total * 100.0, 1)

            def _progresso_corte(msg, progresso=None, etapa=None, etapa_nome=None, eta_s=None):
                if progresso is not None:
                    pct_total = round(pct_base + (progresso / 100.0) * (100.0 / total), 1)
                    status["progresso"] = pct_total
                status["mensagem"] = f"{prefixo} ({idx}/{total}): {msg}"
                salvar_status_cortes(youtube_id, status)

            pacote = gerar_pacote_corte(
                youtube_id=youtube_id,
                info=info,
                bloco=bloco,
                pasta_corte=pasta_corte,
                prefixo=prefixo,
                frases=frases,
                caminho_fonte=caminho_fonte,
                ao_progredir=_progresso_corte,
                config_cortes=cfg_cortes,
            )
            pacotes.append(pacote)
            status["pacotes"] = pacotes
            status["feitos"] = len(pacotes)
            salvar_status_cortes(youtube_id, status)

        # Verificação de integridade dos cortes gravados no Google Drive
        todos_validos = len(pacotes) == total and all(
            verificar_pacote_completo(pkg.get("pasta"), pkg.get("prefixo"), pkg.get("variacoes")) for pkg in pacotes
        )

        bytes_liberados = 0
        if todos_validos and config.RETENCAO_LIMPA:
            log.info("Cortes de %s confirmados no Drive com sucesso (>0 bytes). Executando exclusão automática de cache...", youtube_id)
            bytes_liberados = liberar_cache_video(youtube_id)
            mb = bytes_liberados / (1024 * 1024)
            msg_conclusao = f"Concluído: {total} pacotes FernandoXX salvos no Drive. Cache local liberado ({mb:.1f} MB)."
        elif not config.RETENCAO_LIMPA:
            msg_conclusao = f"Concluído: {total} pacotes FernandoXX salvos no Drive. Cache local mantido (retenção limpa desativada)."
        else:
            msg_conclusao = f"Concluído: {total} pacotes gerados, mas validação incompleta. Cache local mantido por segurança."

        status.update({
            "estado": "concluido",
            "feitos": total,
            "total_blocos": total,
            "pacotes": pacotes,
            "mensagem": msg_conclusao,
            "cache_liberado": bool(todos_validos and config.RETENCAO_LIMPA),
            "bytes_liberados": bytes_liberados,
            "atualizado_em": time.time(),
            "concluido_em": time.time(),
        })
        salvar_status_cortes(youtube_id, status)
        log.info("Vídeo %s finalizado: %s", youtube_id, msg_conclusao)


# Instância global do executor sequencial
executor_sequencial = ExecutorCortesSequencial()

# Limpa resíduos temporários na inicialização do módulo
try:
    limpar_arquivos_incompletos()
except Exception as _e:
    log.warning("Aviso ao limpar resíduos na inicialização: %s", _e)


def processar_blocos_automaticamente(fila, youtube_id, info, blocos, frases=None,
                                     exportar_crus=True, exportar_9x16=True, max_9x16=None,
                                     refazer=False):
    """Gatilho unificado: enfileira para a fila sequencial FernandoXX mantendo retrocompatibilidade com fila antiga."""
    if not blocos:
        return None

    if max_9x16 is None:
        cfg = carregar_config_cortes()
        max_9x16 = cfg.get("max_cortes_por_video", 50)

    # Enfileira no executor sequencial rigoroso
    status = executor_sequencial.enfileirar(
        youtube_id=youtube_id,
        info=info,
        blocos=blocos,
        frases=frases,
        max_cortes=max_9x16,
        refazer=refazer,
    )

    # Compatibilidade com testes unitários antigos se a fila mock for passada
    if fila and hasattr(fila, "adicionar_blocos_crus") and hasattr(fila, "adicionar_exportacao_lote"):
        try:
            titulo = info.get("titulo") or f"Vídeo {youtube_id}"
            if exportar_crus:
                tarefa_crus = fila.adicionar_blocos_crus(youtube_id, titulo, blocos)
                status["crus"] = {"tarefa_id": tarefa_crus["id"]}
            if exportar_9x16:
                top = selecionar_top_blocos(blocos, limite=max_9x16)
                tarefa_lote = fila.adicionar_exportacao_lote(youtube_id, titulo, top, "9:16", {})
                status["reels_9x16"] = {"tarefa_id": tarefa_lote["id"]}
            salvar_status_cortes(youtube_id, status)
        except Exception:
            pass

    return status


class VigilanteCortesContinuo:
    """Supervisiona continuamente vídeos da playlist para garantir que nenhum vídeo das 21h em diante fique sem blocos ou cortes."""

    def __init__(self, intervalo_s=20):
        self.intervalo_s = intervalo_s
        self._thread = None
        self._ativo = True

    def iniciar(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="vigilante-cortes-continuo", daemon=True)
        self._thread.start()
        log.info("Vigilante contínuo de cortes automáticos iniciado (intervalo: %ds).", self.intervalo_s)

    def parar(self):
        self._ativo = False

    def _loop(self):
        time.sleep(5)
        while self._ativo:
            try:
                if obter_modo_automatico():
                    self.verificar_e_processar()
            except Exception as e:
                log.warning("Erro no loop do vigilante contínuo: %s", e)
            for _ in range(max(1, int(self.intervalo_s))):
                if not self._ativo:
                    break
                time.sleep(1)

    def verificar_e_processar(self):
        caminho_reg = os.path.join(config.PASTA_DADOS, "playlist_aovivo.json")
        if not os.path.exists(caminho_reg):
            return

        try:
            with open(caminho_reg, encoding="utf-8") as f:
                reg = json.load(f)
            videos = reg.get("videos", [])
        except Exception:
            return

        for vid in videos:
            yid = vid.get("youtube_id")
            if not yid:
                continue

            if not video_eh_elegivel_por_ponto_partida(yid):
                continue

            info = acervo_local.ler(yid, "info.json") or {
                "youtube_id": yid,
                "titulo": vid.get("titulo") or f"Vídeo {yid}",
                "duracao_s": vid.get("duracao_s", 0),
            }
            dados_blocos = acervo_local.ler(yid, "blocos.json")
            blocos = dados_blocos.get("blocos") if dados_blocos else None

            # 1. Se não tem blocos mas tem frases, gera blocos imediatamente
            if not blocos:
                dados_frases = acervo_local.ler(yid, "frases.json")
                if dados_frases and dados_frases.get("frases"):
                    try:
                        from . import blocador
                        log.info("Vigilante: Gerando blocos para %s (%s)...", info.get("titulo"), yid)
                        novos_b, ign, mod = blocador.dividir(
                            dados_frases["frases"],
                            f"VÍDEO: {info.get('titulo')}\nID: {yid}",
                        )
                        if novos_b:
                            acervo_local.salvar(yid, "blocos.json", {
                                "blocos": novos_b,
                                "ignorados": ign,
                                "modelos": mod,
                                "gerado_em": time.time(),
                                "origem_frases": dados_frases.get("origem", "local"),
                            })
                            acervo_local.salvar_estado(yid, "pronto", f"{len(novos_b)} blocos", 1.0)
                            blocos = novos_b
                    except Exception as err_bl:
                        log.warning("Vigilante: erro ao blocar %s: %s", yid, err_bl)
                else:
                    # Se não tem frases nem blocos, dispara transcrição
                    try:
                        from .servidor import links
                        if links and yid not in links.ativos():
                            est_local = acervo_local.ler(yid, "estado.json", {})
                            st_nome = est_local.get("estado")
                            if st_nome not in ("blocos", "legenda", "transcrevendo", "aguardando_retentativa"):
                                log.info("Vigilante: Enfileirando transcrição para %s (%s)", info.get("titulo"), yid)
                                links.adicionar(yid)
                    except Exception:
                        pass

            # 2. Se tem blocos, verifica se os cortes foram feitos ou estão em andamento
            if blocos:
                st = obter_status_cortes(yid) or {}
                estado_cortes = st.get("estado")
                status_exec = executor_sequencial.status_atual()
                vid_atual = status_exec.get("video_em_andamento")

                if estado_cortes not in ("concluido", "processando", "em_fila") and yid != vid_atual:
                    dados_frases = acervo_local.ler(yid, "frases.json")
                    frases = dados_frases.get("frases") if dados_frases else None
                    cfg_cortes = carregar_config_cortes()
                    max_c = cfg_cortes.get("max_cortes_por_video", 50)
                    log.info("Vigilante: Enfileirando cortes para %s (%s)", info.get("titulo"), yid)
                    executor_sequencial.enfileirar(
                        youtube_id=yid,
                        info=info,
                        blocos=blocos,
                        frases=frases,
                        max_cortes=max_c,
                    )


# Instância global do vigilante contínuo
vigilante_continuo = VigilanteCortesContinuo()
vigilante_continuo.iniciar()

