# -*- coding: utf-8 -*-
"""
Processador e Cortador Integrado de Videos (Local + Google Drive).
Unifica a Automacao de Gravacao de Lives (Zema) com o Indomavel.

Modos de Operacao:
1. Arquivo Especifico:
   python scripts/processar_corte_integrado.py --arquivo "C:/caminho/video.mp4" --titulo "Nome do Video - Parte 1"

2. Daemon Continuo 24/7:
   python scripts/processar_corte_integrado.py --daemon
   - Monitora pastas locais de gravacao do Zema (prioridade maxima, zero download da nuvem)
   - Em background, processa videos pendentes do Google Drive um a um
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# Ajusta path para importar o modulo indomavel
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from indomavel import config, google_drive, cortador_drive, cortador_automatico

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("processador_integrado")

PASTA_GRAVACOES_LOCAL_PADRAO = r"c:\Users\70156213125\Desktop\Zema\recordings"


def is_file_ready(filepath: Path, min_size: int = 10 * 1024 * 1024) -> bool:
    """Verifica se o arquivo .mp4 esta concluido e fechado pelo FFmpeg."""
    if not filepath.exists() or not filepath.is_file():
        return False
    try:
        if filepath.stat().st_size < min_size:
            return False
        with open(filepath, "r+b") as f:
            f.seek(0, os.SEEK_END)
        return True
    except (PermissionError, OSError):
        return False


def obter_videos_locais_pendentes(pasta_recordings: str):
    """Varre a pasta local do Zema em busca de pedacos de gravacao que ainda nao foram cortados."""
    root = Path(pasta_recordings)
    if not root.exists():
        return []

    caminho_hist = os.path.join(config.PASTA_DADOS, "automacao", "drive_processados.json")
    hist = {}
    if os.path.isfile(caminho_hist):
        try:
            with open(caminho_hist, "r", encoding="utf-8") as fh:
                hist = json.load(fh)
        except Exception:
            pass

    token, _ = google_drive.obter_token_acesso()
    id_raiz_cortes = google_drive.obter_pasta_id_ativa() or cortador_drive.PASTA_DESTINO_CORTES_PADRAO

    pendentes = []
    for mp4 in root.rglob("*.mp4"):
        if not is_file_ready(mp4):
            continue

        nome_arq = mp4.name
        titulo = cortador_drive.sanitizar_nome_video(mp4.stem)
        
        m_part = re.search(r"part(\d+)", nome_arq, re.IGNORECASE)
        num_parte = int(m_part.group(1)) + 1 if m_part else 1
        
        parent_name = mp4.parent.name
        if "session_" in parent_name:
            titulo = f"Live {parent_name.replace('session_', '')} - Parte {num_parte}"
        else:
            titulo = f"{titulo} - Parte {num_parte}"

        if titulo in hist and hist[titulo].get("total_cortes", 0) > 0:
            continue

        if token and cortador_drive._ja_possui_cortes_no_drive(titulo, id_raiz_cortes, token):
            continue

        pendentes.append({
            "id": f"local_{mp4.stem}",
            "nome_arquivo": nome_arq,
            "titulo": titulo,
            "caminho_local": str(mp4.resolve()),
            "tamanho": mp4.stat().st_size,
            "numero_parte": num_parte,
            "origem": "local"
        })

    return pendentes


def processar_arquivo_unico(caminho_arquivo: str, titulo: str = None, max_cortes: int = 50):
    """Processa um video local especifico e gera os cortes com as 3 modalidades no Google Drive."""
    fp = Path(caminho_arquivo)
    if not fp.exists():
        log.error("Arquivo nao encontrado: %s", caminho_arquivo)
        return False

    if not titulo:
        titulo = cortador_drive.sanitizar_nome_video(fp.stem)

    video_info = {
        "id": f"local_{fp.stem}",
        "nome_arquivo": fp.name,
        "titulo": titulo,
        "caminho_local": str(fp.resolve()),
        "tamanho": fp.stat().st_size,
        "numero_parte": 1,
        "origem": "local"
    }

    log.info("=== PROCESSANDO VIDEO LOCAL: '%s' ===", titulo)
    res = cortador_drive.processar_video_drive(video_info, max_cortes=max_cortes)
    log.info("Resultado do corte para '%s': Sucesso=%s, Cortes=%s", titulo, res.get("sucesso"), res.get("cortes_gerados"))
    return res.get("sucesso", False)


def loop_daemon_continuo(intervalo_s: int = 20, max_cortes: int = 50):
    """Loop 24/7: consome gravacoes locais primeiro; quando livre, processa pendencias do Google Drive."""
    log.info("=================================================================")
    log.info(" MOTOR DE CORTES INTEGRADO 24/7 (ZEMA + INDOMAVEL) INICIADO")
    log.info(" Pasta Local de Gravacoes: %s", PASTA_GRAVACOES_LOCAL_PADRAO)
    log.info(" Destino no Drive: Pasta 'Cortes' (Subpastas por Modalidade)")
    log.info("=================================================================")

    while True:
        try:
            # 1. Prioridade Maxima: Gravacoes locais recem-concluidas
            locais = obter_videos_locais_pendentes(PASTA_GRAVACOES_LOCAL_PADRAO)
            if locais:
                log.info("[Prioridade 1] Encontrado(s) %d video(s) local(is) pendente(s) de cortes.", len(locais))
                for vid in locais:
                    log.info("-> Iniciando cortes locais para: '%s'", vid['titulo'])
                    cortador_drive.processar_video_drive(vid, max_cortes=max_cortes)
                    time.sleep(2)
                continue

            # 2. Prioridade 2: Videos pendentes no Google Drive (historico de lives)
            drive_pendentes = cortador_drive.escanear_videos_pendentes_drive()
            if drive_pendentes:
                log.info("[Prioridade 2] Encontrado(s) %d video(s) pendente(s) no Google Drive.", len(drive_pendentes))
                proximo = drive_pendentes[0]
                log.info("-> Baixando e processando video do Drive: '%s' (%s)", proximo['titulo'], proximo['nome_arquivo'])
                cortador_drive.processar_video_drive(proximo, max_cortes=max_cortes)
                time.sleep(2)
                continue

            log.info("Todos os videos locais e do Drive estao cortados e sincronizados. Aguardando novos blocos (%ds)...", intervalo_s)
            time.sleep(intervalo_s)

        except KeyboardInterrupt:
            log.info("Interrupcao manual do usuario recebida. Encerrando motor integrado com seguranca...")
            break
        except Exception as e:
            log.error("Erro no ciclo do motor integrado: %s", e, exc_info=True)
            time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Processador e Cortador Integrado de Videos (Zema + Indomavel)")
    parser.add_argument("--arquivo", type=str, help="Caminho do arquivo .mp4 local para cortar")
    parser.add_argument("--titulo", type=str, help="Titulo do video ou live")
    parser.add_argument("--daemon", action="store_true", help="Executar em loop continuo 24/7")
    parser.add_argument("--max-cortes", type=int, default=50, help="Numero maximo de cortes por video (padrao: 50)")
    parser.add_argument("--intervalo", type=int, default=20, help="Intervalo de checagem do daemon em segundos (padrao: 20)")
    args = parser.parse_args()

    if args.arquivo:
        processar_arquivo_unico(args.arquivo, titulo=args.titulo, max_cortes=args.max_cortes)
    elif args.daemon:
        loop_daemon_continuo(intervalo_s=args.intervalo, max_cortes=args.max_cortes)
    else:
        parser.print_help()
