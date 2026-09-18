"""Conector oficial para Google Drive (Nuvem e Fallback Local).

Envia cortes gerados diretamente para a pasta na nuvem indicada pelo operador:
ID: 1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G
(https://drive.google.com/drive/folders/1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G)

Organiza os arquivos respeitando estritamente cada categoria:
- "Com headline e legenda" (FernandoXX_com_legenda.mp4)
- "Com headline e sem legenda" (FernandoXX_sem_legenda.mp4 - opcional)
- "Só legenda" (FernandoXX_so_legenda.mp4)
- "Cortes crus" (FernandoXX_cru.mp4 + FernandoXX_cru.srt)
- "Headlines" (FernandoXX_headlines.txt)

Detecta credenciais de Service Account ou OAuth2; na ausência, utiliza fallback local
organizado em output/google_drive/ sem travar o pipeline.
"""

import json
import logging
import mimetypes
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("indomavel.google_drive")

ID_PASTA_PADRAO = "1wBxCAat68t-jLBl3RJxCBJmZNvPAjz-G"

# Mapeamento oficial de categorias para nomes de pastas no Drive
PASTAS_CATEGORIA = {
    "com_legenda": "Com headline e legenda",
    "sem_legenda": "Com headline e sem legenda",
    "so_legenda": "Só legenda",
    "cru": "Cortes crus",
    "headlines": "Headlines",
}

_TRAVA_DRIVE = threading.Lock()
_CACHE_PASTAS = {}


def identificar_categoria_arquivo(nome_arquivo):
    """Determina a categoria de pasta para um determinado arquivo de corte."""
    nome = os.path.basename(nome_arquivo).lower()
    if "_com_legenda." in nome:
        return "com_legenda"
    if "_sem_legenda." in nome:
        return "sem_legenda"
    if "_so_legenda." in nome:
        return "so_legenda"
    if "_cru." in nome:
        return "cru"
    if "_headlines." in nome or nome.endswith(".txt"):
        return "headlines"
    return "outros"


def localizar_arquivo_credenciais():
    """Busca arquivo de credenciais ativas do Google Drive (Token OAuth ou Service Account)."""
    candidatos = [
        os.path.join(config.PASTA_DADOS, "token.json"),
        config.valor("GOOGLE_DRIVE_CREDENTIALS"),
        config.valor("GOOGLE_APPLICATION_CREDENTIALS"),
        os.path.join(config.PASTA_DADOS, "google_drive_credentials.json"),
        os.path.join(config.PASTA_DADOS, "credentials.json"),
        os.path.join(config.RAIZ, "google_drive_credentials.json"),
        os.path.join(config.RAIZ, "credentials.json"),
    ]
    for c in candidatos:
        if c and os.path.isfile(c):
            try:
                with open(c, encoding="utf-8") as f:
                    conteudo = json.load(f)
                if isinstance(conteudo, dict):
                    if conteudo.get("type") == "service_account":
                        return os.path.abspath(c)
                    if "token" in conteudo or "refresh_token" in conteudo:
                        return os.path.abspath(c)
            except Exception:
                pass
    return None


def obter_dados_oauth_client():
    """Recupera client_id e client_secret do arquivo de configuração OAuth."""
    candidatos = [
        os.path.join(config.PASTA_DADOS, "google_client_secrets.json"),
        os.path.join(config.PASTA_DADOS, "credentials.json"),
        os.path.join(config.RAIZ, "google_client_secrets.json"),
        os.path.join(config.RAIZ, "credentials.json"),
    ]
    for c in candidatos:
        if c and os.path.isfile(c):
            try:
                with open(c, encoding="utf-8") as f:
                    dados = json.load(f)
                secao = dados.get("installed") or dados.get("web") or dados
                cid = secao.get("client_id")
                sec = secao.get("client_secret")
                if cid and sec:
                    return {"client_id": cid, "client_secret": sec, "caminho": os.path.abspath(c)}
            except Exception as e:
                log.debug("Erro ao ler dados OAuth de %s: %s", c, e)
    return None


def gerar_url_autorizacao(redirect_uri="http://127.0.0.1:5055/oauth2callback"):
    """Gera a URL de consentimento OAuth do Google para autorizar acesso ao Google Drive."""
    oauth = obter_dados_oauth_client()
    if not oauth:
        return None, "Arquivo com Client ID e Client Secret não encontrado (google_client_secrets.json)"

    params = {
        "client_id": oauth["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return url, None


def trocar_codigo_por_token(code, redirect_uri="http://127.0.0.1:5055/oauth2callback"):
    """Troca o authorization code retornado pelo Google por tokens de acesso e refresh_token."""
    oauth = obter_dados_oauth_client()
    if not oauth:
        return False, "Configuração do cliente OAuth não encontrada"

    url_token = "https://oauth2.googleapis.com/token"
    dados_post = urllib.parse.urlencode({
        "code": code,
        "client_id": oauth["client_id"],
        "client_secret": oauth["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")

    req = urllib.request.Request(
        url_token,
        data=dados_post,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resposta = json.loads(resp.read().decode("utf-8"))

        access_token = resposta.get("access_token")
        refresh_token = resposta.get("refresh_token")
        expires_in = resposta.get("expires_in", 3600)

        if not access_token:
            return False, "Google não retornou access_token"

        token_data = {
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "scopes": ["https://www.googleapis.com/auth/drive"],
            "expiry": time.time() + expires_in,
        }

        caminho_token = os.path.join(config.PASTA_DADOS, "token.json")
        os.makedirs(config.PASTA_DADOS, exist_ok=True)
        with open(caminho_token, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2, ensure_ascii=False)

        log.info("Token OAuth do Google Drive salvo com sucesso em %s", caminho_token)
        return True, "Google Drive conectado com sucesso!"

    except urllib.error.HTTPError as he:
        detalhe = he.read().decode("utf-8", errors="ignore")
        log.error("Erro HTTP ao trocar token OAuth (%d): %s", he.code, detalhe)
        return False, f"Erro Google ({he.code}): {detalhe}"
    except Exception as e:
        log.exception("Erro ao trocar código por token OAuth: %s", e)
        return False, f"Erro ao obter token: {e}"


def obter_token_acesso():
    """Obtém Bearer Token OAuth2 para Google Drive se houver credenciais disponíveis."""
    caminho = localizar_arquivo_credenciais()
    if not caminho:
        return None, "Nenhum arquivo de credenciais ativas encontrado (token.json ou service account)"

    try:
        # 1. Tenta carregar como Service Account
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request

            scopes = ["https://www.googleapis.com/auth/drive"]
            creds = service_account.Credentials.from_service_account_file(caminho, scopes=scopes)
            creds.refresh(Request())
            if creds.token:
                return creds.token, None
        except Exception as e_sa:
            log.debug("Arquivo %s não é Service Account válida: %s", caminho, e_sa)

        # 2. Tenta carregar como Token de Usuário Autorizado
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            scopes = ["https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_authorized_user_file(caminho, scopes=scopes)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            if creds.token:
                return creds.token, None
        except Exception as e_user:
            log.debug("Arquivo %s não é Token OAuth de usuário: %s", caminho, e_user)

        return None, f"Arquivo de credenciais presente ({os.path.basename(caminho)}), mas não pôde gerar token de acesso"

    except Exception as err:
        return None, f"Erro ao autenticar com Google Drive: {err}"


def obter_ou_criar_pasta_drive(nome_pasta, id_pai=ID_PASTA_PADRAO, token=None):
    """Busca ou cria uma subpasta no Google Drive sob a pasta pai."""
    if not token:
        token, _ = obter_token_acesso()
        if not token:
            return None

    chave_cache = f"{id_pai}_{nome_pasta}"
    with _TRAVA_DRIVE:
        if chave_cache in _CACHE_PASTAS:
            return _CACHE_PASTAS[chave_cache]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # 1. Busca se já existe pasta com esse nome dentro do id_pai
    query = f"'{id_pai}' in parents and name = '{nome_pasta}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    url_busca = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name)"

    try:
        req = urllib.request.Request(url_busca, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
            arquivos = dados.get("files", [])
            if arquivos:
                pasta_id = arquivos[0]["id"]
                with _TRAVA_DRIVE:
                    _CACHE_PASTAS[chave_cache] = pasta_id
                return pasta_id
    except Exception as e:
        log.warning("Erro ao buscar pasta '%s' no Drive: %s", nome_pasta, e)

    # 2. Se não encontrou, cria a pasta
    url_criar = "https://www.googleapis.com/drive/v3/files"
    corpo = {
        "name": nome_pasta,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [id_pai],
    }
    dados_bytes = json.dumps(corpo).encode("utf-8")

    try:
        req = urllib.request.Request(url_criar, data=dados_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
            pasta_id = dados.get("id")
            if pasta_id:
                with _TRAVA_DRIVE:
                    _CACHE_PASTAS[chave_cache] = pasta_id
                log.info("Pasta criada no Google Drive: '%s' (ID: %s)", nome_pasta, pasta_id)
                return pasta_id
    except Exception as e:
        log.warning("Erro ao criar pasta '%s' no Drive: %s", nome_pasta, e)

    return None


def enviar_arquivo_drive(caminho_local, nome_arquivo, id_pasta_destino, token=None):
    """Envia um arquivo local para o Google Drive via Resumable Upload ou Multipart."""
    if not os.path.isfile(caminho_local):
        return None

    tamanho = os.path.getsize(caminho_local)
    if tamanho == 0:
        log.warning("Tentativa de upload de arquivo vazio (0 bytes): %s", caminho_local)
        return None

    if not token:
        token, erro = obter_token_acesso()
        if not token:
            log.warning("Upload ignorado (sem token): %s", erro)
            return None

    mime_type, _ = mimetypes.guess_type(caminho_local)
    mime_type = mime_type or "application/octet-stream"

    # Resumable upload para maior confiabilidade com vídeos MP4
    url_sessao = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
    metadados = {
        "name": nome_arquivo,
        "parents": [id_pasta_destino],
    }
    corpo_meta = json.dumps(metadados).encode("utf-8")

    headers_sessao = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime_type,
        "X-Upload-Content-Length": str(tamanho),
    }

    try:
        req_sessao = urllib.request.Request(url_sessao, data=corpo_meta, headers=headers_sessao, method="POST")
        with urllib.request.urlopen(req_sessao, timeout=20) as resp_sessao:
            upload_url = resp_sessao.headers.get("Location")

        if not upload_url:
            log.warning("Google Drive não retornou Location para upload de %s", nome_arquivo)
            return None

        with open(caminho_local, "rb") as f_conteudo:
            conteudo = f_conteudo.read()

        headers_upload = {
            "Content-Length": str(tamanho),
            "Content-Type": mime_type,
        }

        req_upload = urllib.request.Request(upload_url, data=conteudo, headers=headers_upload, method="PUT")
        with urllib.request.urlopen(req_upload, timeout=120) as resp_final:
            dados_final = json.loads(resp_final.read().decode("utf-8"))
            arquivo_id = dados_final.get("id")
            log.info("Arquivo enviado com sucesso ao Google Drive: %s -> ID %s (%d bytes)", nome_arquivo, arquivo_id, tamanho)
            return {
                "id": arquivo_id,
                "nome": nome_arquivo,
                "tamanho": tamanho,
                "mime_type": mime_type,
            }

    except Exception as e:
        log.warning("Falha ao enviar arquivo %s para Google Drive: %s", nome_arquivo, e)
        return None


def enviar_pacote_drive(pasta_corte, prefixo, variacoes_ativas=None, pasta_id_raiz=ID_PASTA_PADRAO):
    """Envia todos os arquivos gerados de um pacote FernandoXX para as respectivas pastas no Drive.

    Também organiza localmente em subpastas de categoria em PASTA_GOOGLE_DRIVE como fallback
    garantido ou espelhamento.
    """
    if not pasta_corte or not os.path.isdir(pasta_corte):
        return {"sucesso": False, "erro": "Pasta de corte não encontrada"}

    token, motivo_sem_token = obter_token_acesso()
    conectado_nuvem = bool(token)

    pasta_base_local = config.PASTA_GOOGLE_DRIVE
    os.makedirs(pasta_base_local, exist_ok=True)

    arquivos_locais = [f for f in os.listdir(pasta_corte) if os.path.isfile(os.path.join(pasta_corte, f))]
    resultado_arquivos = {}
    todos_ok = True

    for nome_arq in arquivos_locais:
        caminho_origem = os.path.join(pasta_corte, nome_arq)
        cat = identificar_categoria_arquivo(nome_arq)

        # Se a variação está explicitamente desativada, ignora
        if variacoes_ativas is not None:
            if cat in ("com_legenda", "sem_legenda", "so_legenda", "cru", "headlines"):
                if not variacoes_ativas.get(cat, True):
                    continue

        nome_pasta_cat = PASTAS_CATEGORIA.get(cat, "Outros")

        # 1. Organização local por pasta de categoria (espelhamento garantido)
        pasta_cat_local = os.path.join(pasta_base_local, nome_pasta_cat)
        os.makedirs(pasta_cat_local, exist_ok=True)
        destino_cat_local = os.path.join(pasta_cat_local, nome_arq)
        try:
            if not os.path.exists(destino_cat_local) or os.path.getmtime(caminho_origem) > os.path.getmtime(destino_cat_local):
                shutil.copy2(caminho_origem, destino_cat_local)
        except Exception as e_copy:
            log.debug("Aviso ao copiar espelho local de %s: %s", nome_arq, e_copy)

        # 2. Upload para a nuvem no Google Drive se conectado
        if conectado_nuvem:
            id_pasta_cat = obter_ou_criar_pasta_drive(nome_pasta_cat, id_pai=pasta_id_raiz, token=token)
            if id_pasta_cat:
                upload_info = enviar_arquivo_drive(caminho_origem, nome_arq, id_pasta_cat, token=token)
                if upload_info:
                    resultado_arquivos[nome_arq] = {
                        "categoria": cat,
                        "pasta_drive": nome_pasta_cat,
                        "drive_id": upload_info["id"],
                        "tamanho": upload_info["tamanho"],
                        "status": "enviado_nuvem",
                    }
                else:
                    todos_ok = False
                    resultado_arquivos[nome_arq] = {
                        "categoria": cat,
                        "status": "falha_upload_nuvem",
                    }
            else:
                todos_ok = False
                resultado_arquivos[nome_arq] = {
                    "categoria": cat,
                    "status": "falha_criar_pasta_drive",
                }
        else:
            # Fallback local
            resultado_arquivos[nome_arq] = {
                "categoria": cat,
                "pasta_drive": nome_pasta_cat,
                "caminho_local": destino_cat_local,
                "status": "salvo_local_fallback",
            }

    modo = "nuvem" if conectado_nuvem else "fallback_local"
    return {
        "sucesso": todos_ok if conectado_nuvem else True,
        "modo": modo,
        "pasta_raiz_id": pasta_id_raiz,
        "arquivos": resultado_arquivos,
        "mensagem": "Arquivos sincronizados na nuvem do Google Drive" if conectado_nuvem else f"Modo local (Google Drive aguardando credenciais: {motivo_sem_token})",
    }


def obter_pasta_id_ativa():
    """Retorna o ID da pasta do Google Drive configurada pelo usuário ou o padrão oficial."""
    caminho_cfg = os.path.join(config.PASTA_DADOS, "automacao", "config_cortes.json")
    if os.path.exists(caminho_cfg):
        try:
            with open(caminho_cfg, encoding="utf-8") as f:
                cfg = json.load(f)
            pid = (cfg.get("drive") or {}).get("pasta_id")
            if pid:
                return pid.strip()
        except Exception:
            pass
    return ID_PASTA_PADRAO


def status_conexao():
    """Retorna informações detalhadas sobre a conexão com a nuvem do Google Drive."""
    cred_arquivo = localizar_arquivo_credenciais()
    token, erro = obter_token_acesso()
    conectado = bool(token)
    oauth_dados = obter_dados_oauth_client()
    url_auth = None
    if not conectado and oauth_dados:
        url_auth, _ = gerar_url_autorizacao()

    msg = "Conectado ao Google Drive na nuvem. Uploads ativos." if conectado else (
        "Cliente OAuth configurado. Clique em 'Conectar Google Drive' para autorizar." if oauth_dados else
        f"Operando em fallback local (output/google_drive). Motivo: {erro}. "
        "Para envio direto à nuvem, insira o arquivo de credenciais do Google Drive ou conecte o OAuth."
    )

    pasta_ativa = obter_pasta_id_ativa()
    return {
        "conectado": conectado,
        "modo": "nuvem" if conectado else "fallback_local",
        "pasta_id": pasta_ativa,
        "pasta_url": f"https://drive.google.com/drive/folders/{pasta_ativa}",
        "pasta_local": os.path.abspath(config.PASTA_GOOGLE_DRIVE),
        "credenciais_arquivo": os.path.basename(cred_arquivo) if cred_arquivo else None,
        "caminho_credenciais": cred_arquivo,
        "oauth_configurado": bool(oauth_dados),
        "url_autorizacao": url_auth,
        "mensagem": msg,
    }


def salvar_credenciais(conteudo_ou_dict):
    """Salva credenciais inteligentemente de acordo com o tipo (OAuth Client, Service Account ou Token)."""
    if isinstance(conteudo_ou_dict, str):
        try:
            dados = json.loads(conteudo_ou_dict)
        except Exception:
            dados = {}
        conteudo_str = conteudo_ou_dict
    else:
        dados = conteudo_ou_dict or {}
        conteudo_str = json.dumps(dados, indent=2, ensure_ascii=False)

    os.makedirs(config.PASTA_DADOS, exist_ok=True)

    # Detecta se é OAuth Client Secrets
    if "installed" in dados or "web" in dados or ("client_id" in dados and "token" not in dados and dados.get("type") != "service_account"):
        caminho = os.path.join(config.PASTA_DADOS, "google_client_secrets.json")
    # Detecta se é Token autorizado
    elif "token" in dados or "refresh_token" in dados:
        caminho = os.path.join(config.PASTA_DADOS, "token.json")
    # Padrão: Service Account
    else:
        caminho = os.path.join(config.PASTA_DADOS, "google_drive_credentials.json")

    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo_str)

    log.info("Credencial salva com sucesso em %s", caminho)
    return status_conexao()


def subir_cortes_locais_pendentes(pasta_id_raiz=None):
    """Varre todas as pastas FernandoXX em output/google_drive e envia os arquivos gerados para o Drive na nuvem."""
    if not pasta_id_raiz:
        pasta_id_raiz = obter_pasta_id_ativa()
    pasta_base = config.PASTA_GOOGLE_DRIVE
    if not os.path.isdir(pasta_base):
        return {"ok": False, "mensagem": "Pasta de saída não encontrada"}

    token, erro = obter_token_acesso()
    if not token:
        return {"ok": False, "mensagem": f"Google Drive não conectado: {erro}"}

    enviados = 0
    erros = 0
    detalhes = {}

    for item in sorted(os.listdir(pasta_base)):
        pasta_item = os.path.join(pasta_base, item)
        if not os.path.isdir(pasta_item) or not item.startswith("Fernando"):
            continue

        res = enviar_pacote_drive(pasta_item, item, pasta_id_raiz=pasta_id_raiz)
        detalhes[item] = res
        if res.get("sucesso"):
            enviados += 1
        else:
            erros += 1

    return {
        "ok": True,
        "enviados": enviados,
        "erros": erros,
        "detalhes": detalhes,
        "mensagem": f"Sincronização concluída: {enviados} pacotes enviados para o Google Drive na nuvem.",
    }
