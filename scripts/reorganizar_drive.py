#!/usr/bin/env python3
"""Script de reorganização completa do Google Drive e espelho local.
Consolida todas as pastas de lives e cortes na hierarquia estrita:

Pasta Raiz / [Nome do Evento Principal] / [Corte com headline | Corte com headline e legenda | Corte cru] / Arquivos FernandoXX

- Elimina pastas intermediárias FernandoXX (subpastas de modelo ficam diretamente sob a pasta do evento).
- Preserva IDs originais dos arquivos no Drive (move via Google Drive API sem re-upload).
- Move arquivos das pastas legadas de partes e consolida nos eventos oficiais.
- Remove pastas legadas vazias da raiz do Drive e de dentro dos eventos.
- Espelha exatamente a mesma hierarquia em PASTA_GOOGLE_DRIVE localmente.
- Suporta flag --dry-run para simulação prévia sem alterações.
"""

import argparse
import concurrent.futures
import json
import logging
import os
import re
import shutil
import sys

# Adiciona o diretório raiz ao path do Python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indomavel import config, google_drive

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reorganizar_drive")

RAIZ_CORTES_ID = google_drive.ID_PASTA_PADRAO  # 1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G
PADRAO_FERNANDO = re.compile(r"Fernando(\d+)", re.IGNORECASE)

SUBPASTAS_MODELO_OFICIAIS = [
    "Corte com headline",
    "Corte com headline e legenda",
    "Corte cru",
    "Corte com legenda",
]


def carregar_mapa_fernando_para_evento():
    """Carrega o mapeamento FernandoXX -> Nome do Evento a partir do histórico drive_processados.json."""
    mapa = {}
    caminho_hist = os.path.join(config.PASTA_DADOS, "automacao", "drive_processados.json")
    if os.path.isfile(caminho_hist):
        try:
            with open(caminho_hist, "r", encoding="utf-8") as f:
                hist = json.load(f)
            for tit, dados in hist.items():
                ev = google_drive.extrair_nome_evento_principal(tit)
                for c in dados.get("cortes", []):
                    mapa[c.lower()] = ev
        except Exception as e:
            log.warning("Aviso ao carregar drive_processados.json: %s", e)
    return mapa


def reorganizar_drive(dry_run=False):
    token, erro = google_drive.obter_token_acesso()
    if not token:
        log.error("Não foi possível autenticar no Google Drive: %s", erro)
        return False

    mapa_fernando = carregar_mapa_fernando_para_evento()

    log.info("1. Carregando catálogo completo de pastas e arquivos no Google Drive...")
    folders = google_drive.listar_todos_arquivos_drive("mimeType = 'application/vnd.google-apps.folder'", token=token)
    fmap = {f["id"]: f for f in folders}
    log.info("Total de pastas carregadas no Drive: %d", len(fmap))

    files = google_drive.listar_todos_arquivos_drive(
        "name contains 'Fernando' and mimeType != 'application/vnd.google-apps.folder'",
        token=token,
    )
    log.info("Total de arquivos Fernando carregados no Drive: %d", len(files))

    # 2. Identifica pastas na raiz do Drive (1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G)
    root_subfolders = [f for f in folders if RAIZ_CORTES_ID in f.get("parents", [])]
    log.info("Subpastas diretas na raiz de cortes: %d", len(root_subfolders))

    # Agrupa pastas por evento oficial consolidado
    eventos_detectados = {}
    for rf in root_subfolders:
        nome_rf = rf["name"]
        ev = google_drive.extrair_nome_evento_principal(nome_rf)
        eventos_detectados.setdefault(ev, []).append(rf)

    log.info("Eventos Oficiais consolidados detectados na raiz (%d):", len(eventos_detectados))
    for ev_nome, pasts in eventos_detectados.items():
        log.info("  * '%s' (%d pasta(s) associada(s))", ev_nome, len(pasts))

    # 3. Garante que cada evento oficial existe e tem suas subpastas de modelo diretamente sob ele
    eventos_oficiais_ids = {}
    modelos_ids_map = {}  # (nome_evento, subpasta_mod) -> folder_id

    for ev_nome, pasts in eventos_detectados.items():
        id_ev = None
        for p in pasts:
            if p["name"] == ev_nome:
                id_ev = p["id"]
                break
        if not id_ev and not dry_run:
            id_ev = google_drive.obter_ou_criar_pasta_drive(ev_nome, id_pai=RAIZ_CORTES_ID, token=token)
        elif not id_ev and dry_run:
            id_ev = f"simulacao_evento_{ev_nome}"

        eventos_oficiais_ids[ev_nome] = id_ev

        for mod_nome in SUBPASTAS_MODELO_OFICIAIS:
            if not dry_run:
                id_mod = google_drive.obter_ou_criar_pasta_drive(mod_nome, id_pai=id_ev, token=token)
            else:
                id_mod = f"simulacao_{ev_nome}_{mod_nome}"
            modelos_ids_map[(ev_nome, mod_nome)] = id_mod

    # Atualiza fmap caso tenhamos criado pastas de modelo
    if not dry_run:
        folders_updated = google_drive.listar_todos_arquivos_drive("mimeType = 'application/vnd.google-apps.folder'", token=token)
        fmap = {f["id"]: f for f in folders_updated}

    # 4. Determina destino de cada arquivo e planeja movimentos
    log.info("\n2. Mapeando arquivos para suas subpastas de modelo oficiais...")
    movimentos = []  # (file_id, dest_id, curr_parent, nome_arq, ev_destino, mod_destino)
    ja_certos = 0

    for arq in files:
        arq_id = arq["id"]
        nome_arq = arq["name"]
        parents = arq.get("parents", [])
        curr_parent = parents[0] if parents else None

        # Identifica evento escalando a árvore de pais até encontrar uma pasta filha da raiz
        ev_destino = None
        curr = curr_parent
        while curr and curr in fmap:
            p_obj = fmap[curr]
            if RAIZ_CORTES_ID in p_obj.get("parents", []):
                ev_destino = google_drive.extrair_nome_evento_principal(p_obj["name"])
                break
            curr_parents = p_obj.get("parents", [])
            curr = curr_parents[0] if curr_parents else None

        # Fallback via prefixo FernandoXX se não rastreou a raiz
        if not ev_destino:
            m_f = PADRAO_FERNANDO.search(nome_arq)
            if m_f:
                prefixo = f"Fernando{int(m_f.group(1)):02d}"
                ev_destino = mapa_fernando.get(prefixo.lower())
        if not ev_destino:
            ev_destino = "Cortes Gerais"

        # Garante evento e modelos para eventos avulsos
        if ev_destino not in eventos_oficiais_ids:
            if not dry_run:
                id_ev_avulso = google_drive.obter_ou_criar_pasta_drive(ev_destino, id_pai=RAIZ_CORTES_ID, token=token)
                for mod_nome in SUBPASTAS_MODELO_OFICIAIS:
                    id_m = google_drive.obter_ou_criar_pasta_drive(mod_nome, id_pai=id_ev_avulso, token=token)
                    modelos_ids_map[(ev_destino, mod_nome)] = id_m
            else:
                id_ev_avulso = f"simulacao_{ev_destino}"
            eventos_oficiais_ids[ev_destino] = id_ev_avulso

        cat = google_drive.identificar_categoria_arquivo(nome_arq)
        mod_destino = google_drive.obter_nome_subpasta_modalidade(cat)
        id_dest = modelos_ids_map.get((ev_destino, mod_destino))

        if curr_parent == id_dest:
            ja_certos += 1
        else:
            movimentos.append((arq_id, id_dest, curr_parent, nome_arq, ev_destino, mod_destino))

    log.info("Arquivos já na pasta correta: %d", ja_certos)
    log.info("Arquivos a mover para [Evento] / [Subpasta de Modelo]: %d", len(movimentos))

    # 5. Executa os movimentos em paralelo com ThreadPoolExecutor
    if dry_run:
        log.info("[SIMULAÇÃO] %d arquivos seriam movidos no Drive.", len(movimentos))
    else:
        log.info("\n3. Movendo %d arquivos no Google Drive...", len(movimentos))
        sucessos_move = 0
        erros_move = 0

        def _mover_uma(tarefa):
            fid, dest_id, orig_id, nome, ev, mod = tarefa
            ok = google_drive.mover_arquivo_drive(fid, dest_id, antigo_pai_id=orig_id, token=token)
            return ok, nome, ev, mod

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(_mover_uma, t) for t in movimentos]
            for i, fut in enumerate(concurrent.futures.as_completed(futures), start=1):
                ok, nome, ev, mod = fut.result()
                if ok:
                    sucessos_move += 1
                else:
                    erros_move += 1
                if i % 100 == 0 or i == len(movimentos):
                    log.info("Progresso: %d / %d arquivos movidos (sucessos: %d, erros: %d)...", i, len(movimentos), sucessos_move, erros_move)

        log.info("Movimentação concluída: %d movidos com sucesso, %d falhas.", sucessos_move, erros_move)

    # 6. Limpeza de subpastas intermediárias FernandoXX dentro dos eventos consolidados
    log.info("\n4. Limpando subpastas intermediárias FernandoXX dos eventos...")
    for ev_nome, id_ev in eventos_oficiais_ids.items():
        if not id_ev or id_ev.startswith("simulacao"):
            continue
        itens_ev = google_drive.listar_arquivos_subpasta(id_ev, token=token)
        for it in itens_ev:
            if it.get("mimeType") == "application/vnd.google-apps.folder":
                if it["name"] not in SUBPASTAS_MODELO_OFICIAIS:
                    if dry_run:
                        log.info("  [SIMULAÇÃO] Excluir pasta intermediária de '%s': '%s'", ev_nome, it["name"])
                    else:
                        sub_filhos = google_drive.listar_arquivos_subpasta(it["id"], token=token)
                        for sf in sub_filhos:
                            google_drive.excluir_arquivo_drive(sf["id"], token=token)
                        google_drive.excluir_arquivo_drive(it["id"], token=token)
                        log.info("  [OK] Pasta intermediária removida de '%s': '%s'", ev_nome, it["name"])

    # 7. Limpeza de pastas legadas de partes da raiz do Drive
    log.info("\n5. Limpando pastas legadas da raiz do Drive...")
    root_subfolders_atualizado = google_drive.listar_arquivos_subpasta(RAIZ_CORTES_ID, token=token)
    ids_oficiais_set = set(eventos_oficiais_ids.values())
    for rf in root_subfolders_atualizado:
        rf_id = rf["id"]
        rf_name = rf["name"]
        if rf_id not in ids_oficiais_set:
            if dry_run:
                log.info("  [SIMULAÇÃO] Excluir pasta legada da raiz: '%s' (ID: %s)", rf_name, rf_id)
            else:
                ev_nome_legado = google_drive.extrair_nome_evento_principal(rf_name)
                id_ev_oficial = eventos_oficiais_ids.get(ev_nome_legado) or eventos_oficiais_ids.get("Cortes Gerais")
                subs = google_drive.listar_arquivos_subpasta(rf_id, token=token)
                for s in subs:
                    sub_filhos = google_drive.listar_arquivos_subpasta(s["id"], token=token)
                    for sf in sub_filhos:
                        if sf.get("mimeType") == "application/vnd.google-apps.folder":
                            google_drive.excluir_arquivo_drive(sf["id"], token=token)
                        else:
                            # Se for arquivo que sobrou, move para o modelo do evento antes de apagar
                            cat = google_drive.identificar_categoria_arquivo(sf["name"])
                            mod = google_drive.obter_nome_subpasta_modalidade(cat)
                            id_dest_mod = modelos_ids_map.get((ev_nome_legado, mod))
                            if id_dest_mod:
                                google_drive.mover_arquivo_drive(sf["id"], id_dest_mod, antigo_pai_id=s["id"], token=token)
                            else:
                                google_drive.excluir_arquivo_drive(sf["id"], token=token)
                    google_drive.excluir_arquivo_drive(s["id"], token=token)
                ok_del = google_drive.excluir_arquivo_drive(rf_id, token=token)
                if ok_del:
                    log.info("  [OK] Pasta legada removida da raiz: '%s'", rf_name)
                else:
                    log.warning("  [AVISO] Falha ao remover pasta da raiz: '%s'", rf_name)

    log.info("\nReorganização do Google Drive finalizada!")

    # 8. Reorganização do espelho local
    reorganizar_espelho_local(dry_run=dry_run, mapa_fernando=mapa_fernando)
    return True


def reorganizar_espelho_local(dry_run=False, mapa_fernando=None):
    """Reorganiza todas as pastas e arquivos locais em config.PASTA_GOOGLE_DRIVE
    para a hierarquia estrita: Pasta Raiz / [Nome do Evento] / [Subpastas de Modelo] / Arquivos.
    """
    pasta_local = config.PASTA_GOOGLE_DRIVE
    if not os.path.isdir(pasta_local):
        log.warning("Pasta local %s não existe. Pulando espelho local.", pasta_local)
        return

    if mapa_fernando is None:
        mapa_fernando = carregar_mapa_fernando_para_evento()

    log.info("\n--- Reorganizando Espelho Local em '%s' ---", pasta_local)
    PASTAS_LEGADAS_NOMES = {
        "headlines",
        "outros",
        "cortes crus",
        "cortes",
        "com headline e sem legenda",
        "cortes com headline",
        "cortes com headline e legenda",
        "cortes originais",
    }

    itens_locais = sorted(os.listdir(pasta_local))

    # 1. Processa todas as pastas FernandoXX soltas na raiz do espelho local
    for item in itens_locais:
        caminho_item = os.path.join(pasta_local, item)
        if not os.path.isdir(caminho_item):
            continue

        m_f = PADRAO_FERNANDO.search(item)
        if m_f:
            prefixo = f"Fernando{int(m_f.group(1)):02d}"
            ev_real = mapa_fernando.get(prefixo.lower(), "Cortes Gerais")
            for r, dirs, arqs in os.walk(caminho_item):
                for f in arqs:
                    if f.endswith(".json") or f.startswith("."):
                        continue
                    cat = google_drive.identificar_categoria_arquivo(f)
                    mod = google_drive.obter_nome_subpasta_modalidade(cat)
                    dest_mod = os.path.join(pasta_local, ev_real, mod)
                    os.makedirs(dest_mod, exist_ok=True)
                    dest_f = os.path.join(dest_mod, f)
                    orig_f = os.path.join(r, f)
                    if not os.path.exists(dest_f):
                        if not dry_run:
                            shutil.move(orig_f, dest_f)
                        log.info("  [LOCAL] Movido Fernando solto: %s -> %s / %s", f, ev_real, mod)
                    else:
                        if not dry_run and orig_f != dest_f:
                            try:
                                os.remove(orig_f)
                            except Exception:
                                pass
            if not dry_run:
                shutil.rmtree(caminho_item, ignore_errors=True)
                log.info("  [LOCAL] Pasta Fernando removida da raiz: %s", item)

    # 2. Processa pastas legadas gerais (Headlines, Outros, partes de lives)
    itens_restantes = sorted(os.listdir(pasta_local))
    for item in itens_restantes:
        caminho_item = os.path.join(pasta_local, item)
        if not os.path.isdir(caminho_item):
            continue

        nome_evento = google_drive.extrair_nome_evento_principal(item)
        eh_evento_oficial = (
            item == nome_evento
            and not PADRAO_FERNANDO.search(item)
            and item.lower() not in PASTAS_LEGADAS_NOMES
        )

        if not eh_evento_oficial:
            for r, dirs, arqs in os.walk(caminho_item):
                for arq in arqs:
                    if arq.endswith(".json") or arq.startswith("."):
                        continue
                    m_f = PADRAO_FERNANDO.search(arq)
                    prefixo = f"Fernando{int(m_f.group(1)):02d}" if m_f else None
                    if prefixo and prefixo.lower() in mapa_fernando:
                        ev_real = mapa_fernando[prefixo.lower()]
                    elif nome_evento and nome_evento.lower() not in PASTAS_LEGADAS_NOMES:
                        ev_real = nome_evento
                    else:
                        ev_real = "Cortes Gerais"

                    cat = google_drive.identificar_categoria_arquivo(arq)
                    sub_mod = google_drive.obter_nome_subpasta_modalidade(cat)
                    dest_mod = os.path.join(pasta_local, ev_real, sub_mod)
                    os.makedirs(dest_mod, exist_ok=True)
                    dest_f = os.path.join(dest_mod, arq)
                    orig_f = os.path.join(r, arq)
                    if not os.path.exists(dest_f):
                        if not dry_run:
                            shutil.move(orig_f, dest_f)
                        log.info("  [LOCAL] Consolidado: %s -> %s / %s", arq, ev_real, sub_mod)
                    else:
                        if not dry_run and orig_f != dest_f:
                            try:
                                os.remove(orig_f)
                            except Exception:
                                pass
            if not dry_run:
                shutil.rmtree(caminho_item, ignore_errors=True)
                log.info("  [LOCAL] Pasta legada removida da raiz: %s", item)

    # 3. Limpa pastas dos eventos oficiais consolidados (remove subpastas FernandoXX ou intermediárias)
    eventos_finais = sorted(os.listdir(pasta_local))
    for item in eventos_finais:
        caminho_item = os.path.join(pasta_local, item)
        if not os.path.isdir(caminho_item):
            continue

        # Garante as 4 subpastas de modelo
        for mod_nome in SUBPASTAS_MODELO_OFICIAIS:
            os.makedirs(os.path.join(caminho_item, mod_nome), exist_ok=True)

        for sub in list(os.listdir(caminho_item)):
            c_sub = os.path.join(caminho_item, sub)
            if os.path.isdir(c_sub) and sub not in SUBPASTAS_MODELO_OFICIAIS:
                for r, dirs, arqs in os.walk(c_sub):
                    for f in arqs:
                        if f.endswith(".json") or f.startswith("."):
                            continue
                        cat = google_drive.identificar_categoria_arquivo(f)
                        mod = google_drive.obter_nome_subpasta_modalidade(cat)
                        dest_f = os.path.join(caminho_item, mod, f)
                        orig_f = os.path.join(r, f)
                        if not os.path.exists(dest_f):
                            if not dry_run:
                                shutil.move(orig_f, dest_f)
                            log.info("  [LOCAL] Movido subpasta para modelo: %s -> %s / %s", f, item, mod)
                        else:
                            if not dry_run and orig_f != dest_f:
                                try:
                                    os.remove(orig_f)
                                except Exception:
                                    pass
                if not dry_run:
                    shutil.rmtree(c_sub, ignore_errors=True)
                    log.info("  [LOCAL] Removida subpasta interna legada: %s / %s", item, sub)
            elif os.path.isfile(c_sub) and not c_sub.endswith(".json") and not sub.startswith("."):
                # Arquivo solto na pasta do evento
                cat = google_drive.identificar_categoria_arquivo(sub)
                mod = google_drive.obter_nome_subpasta_modalidade(cat)
                dest_f = os.path.join(caminho_item, mod, sub)
                if not os.path.exists(dest_f):
                    if not dry_run:
                        shutil.move(c_sub, dest_f)
                    log.info("  [LOCAL] Movido arquivo solto para modelo: %s -> %s / %s", sub, item, mod)

    log.info("[LOCAL] Espelho local reorganizado com sucesso!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reorganizar Drive e espelho local na hierarquia estrita.")
    parser.add_argument("--dry-run", action="store_true", help="Simula as ações sem mover ou excluir nada")
    args = parser.parse_args()

    reorganizar_drive(dry_run=args.dry_run)
