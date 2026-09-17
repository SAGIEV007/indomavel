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

from . import acervo_local, config, headlines, legenda, recorte, render, youtube

log = logging.getLogger("indomavel.cortador_automatico")

# Estado global do modo automático (desligado por padrão no boot conforme especificado pelo usuário)
_MODO_AUTOMATICO = False
_TRAVA_MODO = threading.Lock()


def obter_modo_automatico():
    """Retorna se o modo de cortes automáticos está ativo."""
    global _MODO_AUTOMATICO
    with _TRAVA_MODO:
        return _MODO_AUTOMATICO


def definir_modo_automatico(ativo):
    """Ativa ou desativa o modo de cortes automáticos."""
    global _MODO_AUTOMATICO
    with _TRAVA_MODO:
        _MODO_AUTOMATICO = bool(ativo)
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
    """Remove resíduos temporários (.tmp, .part) e arquivos de vídeo vazios (0 bytes) gerados por interrupções."""
    if pastas is None:
        pastas = [config.PASTA_GOOGLE_DRIVE, config.PASTA_OUTPUT_CORTES, config.PASTA_DOWNLOADS, config.PASTA_VIDEOS]

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
                    elif ext == ".mp4" and os.path.getsize(caminho) == 0:
                        deve_remover = True

                    if deve_remover:
                        os.remove(caminho)
                        removidos.append(caminho)
                except Exception:
                    pass

            for d in dirs:
                caminho_dir = os.path.join(raiz, d)
                if re.match(r"^Fernando\d+$", d, re.IGNORECASE):
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


def verificar_pacote_completo(pasta_corte, prefixo=None):
    """Verifica se o pacote FernandoXX na pasta de destino (Google Drive) foi gerado integralmente com tamanho > 0.

    Exige os 4 arquivos essenciais do pacote:
    - FernandoXX_com_legenda.mp4 (> 0 bytes)
    - FernandoXX_sem_legenda.mp4 (> 0 bytes)
    - FernandoXX_cru.mp4 (> 0 bytes)
    - FernandoXX_headlines.txt (> 0 bytes)
    """
    if not pasta_corte or not os.path.isdir(pasta_corte):
        return False

    if not prefixo:
        prefixo = os.path.basename(os.path.normpath(pasta_corte))

    arquivos_obrigatorios = [
        f"{prefixo}_com_legenda.mp4",
        f"{prefixo}_sem_legenda.mp4",
        f"{prefixo}_cru.mp4",
        f"{prefixo}_headlines.txt",
    ]

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
            removidos_total += liberar_cache_video(item)
    return removidos_total


def gerar_pacote_corte(youtube_id, info, bloco, pasta_corte, prefixo, frases=None, caminho_fonte=None, ao_progredir=None):
    """Gera o pacote quádruplo completo dentro da pasta FernandoXX:
    1. FernandoXX_cru.mp4 (+ FernandoXX_cru.srt)
    2. FernandoXX_headlines.txt (3-8 sugestões de headlines com locutor inteligente)
    3. FernandoXX_com_legenda.mp4 (9:16 vertical, card com headline, legendas animadas ASS, sem marca)
    4. FernandoXX_sem_legenda.mp4 (9:16 vertical, card com headline, sem legendas, sem marca)
    """
    os.makedirs(pasta_corte, exist_ok=True)
    titulo_video = info.get("titulo") or youtube_id
    titulo_bloco = bloco.get("titulo") or f"Corte {prefixo}"
    inicio = max(0.0, float(bloco.get("inicio", 0.0)))
    fim = float(bloco.get("fim", inicio + 60.0))
    duracao = max(0.1, fim - inicio)

    renan_falando = bool(bloco.get("renan_falando", False))
    locutor = bloco.get("locutor") or ""

    if not caminho_fonte or not os.path.exists(caminho_fonte):
        caminho_fonte = youtube.baixar_video_maximo(youtube_id)

    # 1. Corte Cru sem perda de qualidade (lossless stream copy)
    caminho_cru = os.path.join(pasta_corte, f"{prefixo}_cru.mp4")
    caminho_srt = os.path.join(pasta_corte, f"{prefixo}_cru.srt")
    inicio_real = recorte.recortar_sem_perda(caminho_fonte, inicio, fim, caminho_cru)

    if frases:
        try:
            with open(caminho_srt, "w", encoding="utf-8") as f:
                f.write(legenda.srt(frases, inicio_real, fim))
        except Exception as e:
            log.warning("Falha ao gerar .srt para %s: %s", prefixo, e)

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

    # 3. Sugestões de headlines (3 a 8 opções com locutor inteligente)
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
        "------------------------------------------------------------",
        f"OPÇÕES DE HEADLINE GERADAS ({len(opcoes)} opções):",
        "------------------------------------------------------------",
    ]
    for idx, opc in enumerate(opcoes, start=1):
        linhas_txt.append(f"\n[Opção {idx}] - Ângulo: {opc.get('angulo', 'geral')}")
        linhas_txt.append(f"TAG:      {opc.get('tag', 'EM ALTA!')}")
        linhas_txt.append(f"HEADLINE: {opc.get('headline', '')}")

    with open(caminho_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas_txt) + "\n")

    top_tag = opcoes[0].get("tag", "EM ALTA!")
    top_headline = opcoes[0].get("headline", titulo_bloco)

    # 4. Render 9:16 Vertical COM legenda
    estilo_com = {
        "card": True,
        "tag": top_tag,
        "headline": top_headline,
        "cortar_topo": 140,  # Remove GC de live superior
        "legenda": True,
        "fonte_legenda": "bebas",
        "cor_destaque": "#FFD60A",
        "destacar_palavra": True,
        "rodape": False,  # Sem marca d'água inicial
        "enquadramento_x": 0.0,
        "enquadramento_y": 0.0,
        "zoom": 1.0,
        "maiusculas": True,
        "max_palavras": 3,
        "cor_legenda": "#FFFFFF",
    }
    trechos_legenda = []
    if frases:
        try:
            trechos_legenda = legenda.trechos_de_frases(frases, inicio, fim, max_palavras=3)
        except Exception:
            trechos_legenda = []

    caminho_com_legenda = render.exportar(
        youtube_id=youtube_id,
        inicio=inicio,
        fim=fim,
        formato="9:16",
        estilo=estilo_com,
        trechos=trechos_legenda,
        pasta_saida=pasta_corte,
        titulo=f"{prefixo}_com_legenda",
        ao_progredir=ao_progredir,
        nome_arquivo=f"{prefixo}_com_legenda.mp4",
    )

    # 5. Render 9:16 Vertical SEM legenda
    estilo_sem = dict(estilo_com)
    estilo_sem["legenda"] = False

    caminho_sem_legenda = render.exportar(
        youtube_id=youtube_id,
        inicio=inicio,
        fim=fim,
        formato="9:16",
        estilo=estilo_sem,
        trechos=[],
        pasta_saida=pasta_corte,
        titulo=f"{prefixo}_sem_legenda",
        ao_progredir=ao_progredir,
        nome_arquivo=f"{prefixo}_sem_legenda.mp4",
    )

    return {
        "prefixo": prefixo,
        "pasta": pasta_corte,
        "bloco_id": bloco.get("id"),
        "inicio": inicio,
        "fim": fim,
        "duracao": duracao,
        "renan_falando": renan_falando,
        "locutor": locutor,
        "tag": top_tag,
        "headline": top_headline,
        "arquivos": {
            "com_legenda": os.path.basename(caminho_com_legenda),
            "sem_legenda": os.path.basename(caminho_sem_legenda),
            "cru": os.path.basename(caminho_cru),
            "srt": os.path.basename(caminho_srt) if os.path.exists(caminho_srt) else None,
            "headlines": os.path.basename(caminho_txt),
        },
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
            status.update({
                "feitos": idx - 1,
                "mensagem": f"Gerando pacote {idx} de {total} (cortes secos, headlines e render 9:16)...",
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
            )
            pacotes.append(pacote)

        # Verificação de integridade dos cortes gravados no Google Drive
        todos_validos = len(pacotes) == total and all(
            verificar_pacote_completo(pkg.get("pasta"), pkg.get("prefixo")) for pkg in pacotes
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
                                     exportar_crus=True, exportar_9x16=True, max_9x16=4,
                                     refazer=False):
    """Gatilho unificado: enfileira para a fila sequencial FernandoXX mantendo retrocompatibilidade com fila antiga."""
    if not blocos:
        return None

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
