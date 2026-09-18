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
import re
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
    if "_com_headline_e_legenda." in nome or "_com_legenda." in nome:
        return "com_legenda"
    if "_com_headline." in nome or "_sem_legenda." in nome or "_headline." in nome:
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

        import datetime
        dt_exp = datetime.datetime.fromtimestamp(time.time() + expires_in, datetime.timezone.utc)
        token_data = {
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "scopes": ["https://www.googleapis.com/auth/drive"],
            "expiry": dt_exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
            import datetime

            with open(caminho, encoding="utf-8") as ft:
                dados_token = json.load(ft)
            if isinstance(dados_token, dict) and ("token" in dados_token or "refresh_token" in dados_token):
                exp = dados_token.get("expiry")
                if isinstance(exp, (int, float)):
                    dt = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
                    dados_token["expiry"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    with open(caminho, "w", encoding="utf-8") as ft_out:
                        json.dump(dados_token, ft_out, indent=2)

                scopes = ["https://www.googleapis.com/auth/drive"]
                creds = Credentials.from_authorized_user_info(dados_token, scopes=scopes)
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    try:
                        with open(caminho, "w", encoding="utf-8") as ft:
                            ft.write(creds.to_json())
                    except Exception:
                        pass
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


def buscar_pasta_drive(nome_pasta, id_pai=ID_PASTA_PADRAO, token=None):
    """Busca uma subpasta no Google Drive por nome SEM criá-la."""
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
    return None


def listar_todos_arquivos_drive(query_extra="", token=None):
    """Executa busca genérica no Google Drive com paginação."""
    if not token:
        token, _ = obter_token_acesso()
        if not token:
            return []
    headers = {"Authorization": f"Bearer {token}"}
    itens = []
    page_token = None
    query = "trashed = false"
    if query_extra:
        query += f" and ({query_extra})"

    while True:
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=nextPageToken,files(id,name,mimeType,size,parents)&pageSize=200"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
                itens.extend(dados.get("files", []))
                page_token = dados.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            log.warning("Erro ao buscar arquivos no Drive (%s): %s", query, e)
            break
    return itens


def obter_maior_numero_fernando(pasta_base_local=None, pasta_raiz_id=ID_PASTA_PADRAO, token=None):
    """Encontra o maior número FernandoXX já usado tanto no disco local quanto no Google Drive."""
    numeros = set()
    padrao = re.compile(r"Fernando(\d+)", re.IGNORECASE)

    # 1. Local
    pasta_local = pasta_base_local or config.PASTA_GOOGLE_DRIVE
    if os.path.isdir(pasta_local):
        for raiz, dirs, arqs in os.walk(pasta_local):
            for d in dirs:
                m = padrao.search(d)
                if m:
                    numeros.add(int(m.group(1)))
            for a in arqs:
                m = padrao.search(a)
                if m:
                    numeros.add(int(m.group(1)))

    # 2. Google Drive
    if not token:
        token, _ = obter_token_acesso()
    if token:
        try:
            arqs_drive = listar_todos_arquivos_drive("name contains 'Fernando'", token=token)
            for item in arqs_drive:
                m = padrao.search(item.get("name", ""))
                if m:
                    numeros.add(int(m.group(1)))
        except Exception as e:
            log.warning("Erro ao buscar números Fernando no Drive: %s", e)

    return max(numeros) if numeros else 0


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

    # Deduplicação: verifica se arquivo com mesmo nome e tamanho já existe na pasta de destino
    query = f"'{id_pasta_destino}' in parents and name = '{nome_arquivo}' and trashed = false"
    url_busca = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name,size)"
    try:
        req_b = urllib.request.Request(url_busca, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req_b, timeout=15) as resp_b:
            dados_b = json.loads(resp_b.read().decode("utf-8"))
            for f_existente in dados_b.get("files", []):
                if int(f_existente.get("size", 0)) == tamanho:
                    log.info("Arquivo já existe no Google Drive: %s (ID: %s). Re-upload evitado.", nome_arquivo, f_existente["id"])
                    return {
                        "id": f_existente["id"],
                        "nome": nome_arquivo,
                        "tamanho": tamanho,
                        "mime_type": mime_type,
                    }
    except Exception as e_check:
        log.debug("Aviso ao checar arquivo existente no Drive: %s", e_check)

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
        msg_erro = str(e)
        if hasattr(e, "read"):
            try:
                msg_erro += " " + e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
        if "storageQuotaExceeded" in msg_erro or "quota" in msg_erro.lower():
            log.error(
                "Cota do Google Drive excedida ao enviar %s: %s. "
                "Contas de Serviço têm 0 bytes de cota em drives pessoais. "
                "Execute Conectar_Google_Drive.bat para autenticar via OAuth pessoal.",
                nome_arquivo, msg_erro,
            )
        else:
            log.warning("Falha ao enviar arquivo %s para Google Drive: %s", nome_arquivo, e)
        return None


def extrair_nome_evento_principal(titulo: str) -> str:
    """Extrai o nome do evento/live principal removendo sufixos de partes/blocos.
    Exemplos:
    - 'A CAMINHO DE GRAMADO-RS FUTURO GLORIOSO TOUR - Parte 6' -> 'A CAMINHO DE GRAMADO-RS FUTURO GLORIOSO TOUR'
    - 'FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 12' -> 'FUTURO GLORIOSO TOUR - SANTA CATARINA'
    - 'BOM DIA, RIO GRANDE DO SUL | FUTURO GLORIOSO TOUR 2026-09-18 14:04 - Parte 1' -> 'BOM DIA, RIO GRANDE DO SUL | FUTURO GLORIOSO TOUR'
    - 'parte_000' -> Título da transmissão ao vivo mais recente
    """
    if not titulo:
        return "Cortes Gerais"

    t = str(titulo).strip()

    # Se for apenas 'parte_XXX' ou 'local_parte_XXX', busca título real da live na base SQLite
    if re.match(r"^(?:local_)?parte_\d+$", t, re.IGNORECASE):
        try:
            db_path = os.path.join(config.PASTA_DADOS, "gravador_lives.sqlite3")
            if os.path.exists(db_path):
                import sqlite3
                with sqlite3.connect(db_path, timeout=5.0) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute("SELECT titulo FROM sessoes_live ORDER BY id DESC LIMIT 1").fetchone()
                    if row and row["titulo"] and not row["titulo"].lower().startswith("live "):
                        t = row["titulo"]
        except Exception:
            pass

    # Remove carimbo de data/hora no formato 2026-09-18 14:04 ou 2026-09-18 1404 ou similar
    t = re.sub(r"\s+\d{4}-\d{2}-\d{2}(?:\s+\d{2}:?\d{2}(?::?\d{2})?)?", "", t)

    # Remove sufixos de parte, bloco, chunk ou fatia
    t = re.sub(r"(?i)\s*[-_–]\s*(?:parte|bloco|chunk|fatia)\s*\d+.*$", "", t)
    t = re.sub(r"(?i)\s+(?:parte|bloco|chunk|fatia)\s*\d+.*$", "", t)
    t = re.sub(r"(?i)\s*[-_–]\s*p\d+.*$", "", t)

    # Sanitiza caracteres proibidos em caminhos de arquivo no Windows e Drive
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t or "Cortes Gerais"


PASTAS_CATEGORIA_HIERARQUICA = {
    "cru": "Corte cru",
    "headlines": "Corte cru",
    "sem_legenda": "Corte com headline",
    "com_headline": "Corte com headline",
    "com_legenda": "Corte com headline e legenda",
    "so_legenda": "Corte com legenda",
}


def obter_nome_subpasta_modalidade(categoria, variacoes_ativas=None):
    """Determina o nome da subpasta de modelo de corte conforme a especificação:
    - 'Corte cru' (vídeo original cru + srt + sugestões de headlines)
    - 'Corte com headline' (vídeo com headline sem legenda)
    - 'Corte com headline e legenda' (vídeo com headline e legenda dinâmicas)
    - 'Corte com legenda' (se configurado como variação avulsa)
    """
    if categoria in ("cru", "headlines"):
        return "Corte cru"
    if categoria in ("sem_legenda", "com_headline"):
        return "Corte com headline"
    if categoria == "com_legenda":
        return "Corte com headline e legenda"
    if categoria == "so_legenda":
        return "Corte com legenda"
    return PASTAS_CATEGORIA_HIERARQUICA.get(categoria, "Corte cru")


def mover_arquivo_drive(file_id, novo_pai_id, antigo_pai_id=None, token=None):
    """Move um arquivo ou pasta no Google Drive alterando seus parents de forma atômica."""
    if not token:
        token, _ = obter_token_acesso()
    if not token or not file_id or not novo_pai_id:
        return False

    try:
        if not antigo_pai_id:
            url_get = f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=parents"
            req_get = urllib.request.Request(url_get, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req_get, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                antigo_pai_id = ",".join(data.get("parents", []))

        url_patch = (
            f"https://www.googleapis.com/drive/v3/files/{file_id}?"
            f"addParents={urllib.parse.quote(novo_pai_id)}"
            f"&removeParents={urllib.parse.quote(antigo_pai_id)}"
            f"&fields=id,parents"
        )
        req_patch = urllib.request.Request(url_patch, headers={"Authorization": f"Bearer {token}"}, method="PATCH")
        with urllib.request.urlopen(req_patch, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning("Erro ao mover arquivo %s no Drive: %s", file_id, e)
        return False


def excluir_arquivo_drive(file_id, token=None):
    """Exclui ou move para a lixeira um arquivo/pasta no Google Drive."""
    if not token:
        token, _ = obter_token_acesso()
    if not token or not file_id:
        return False
    try:
        url_del = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        req_del = urllib.request.Request(url_del, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
        with urllib.request.urlopen(req_del, timeout=15) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        log.debug("Aviso ao excluir arquivo %s no Drive: %s", file_id, e)
        return False


def enviar_pacote_drive(pasta_corte, prefixo, variacoes_ativas=None, pasta_id_raiz=ID_PASTA_PADRAO, titulo_video=None):
    """Envia todos os arquivos gerados de um pacote FernandoXX para a hierarquia estrita:
    Pasta Raiz / [Nome do Evento] / [Subpastas de Modelo] / Arquivos FernandoXX

    As 3 (ou 4) subpastas de modelo ficam DIRETAMENTE sob a pasta do evento/vídeo:
    - 'Corte com headline': FernandoXX_com_headline.mp4 / FernandoXX_sem_legenda.mp4
    - 'Corte com headline e legenda': FernandoXX_com_headline_e_legenda.mp4 / FernandoXX_com_legenda.mp4
    - 'Corte cru': FernandoXX_cru.mp4, FernandoXX_cru.srt, FernandoXX_headlines.txt
    - 'Corte com legenda': FernandoXX_so_legenda.mp4 (se ativado)

    (Não existe subpasta individual FernandoXX entre o Evento e os modelos).
    Também organiza localmente com essa mesma hierarquia em PASTA_GOOGLE_DRIVE como espelhamento.
    """
    if not pasta_corte or not os.path.isdir(pasta_corte):
        return {"sucesso": False, "erro": "Pasta de corte não encontrada"}

    token, motivo_sem_token = obter_token_acesso()
    conectado_nuvem = bool(token)

    pasta_base_local = config.PASTA_GOOGLE_DRIVE
    os.makedirs(pasta_base_local, exist_ok=True)

    nome_pasta_evento = extrair_nome_evento_principal(titulo_video) if titulo_video else "Cortes Gerais"

    # 1. Pasta da Live/Evento principal sob a raiz
    pasta_evento_local = os.path.join(pasta_base_local, nome_pasta_evento)
    os.makedirs(pasta_evento_local, exist_ok=True)
    id_pasta_evento = obter_ou_criar_pasta_drive(nome_pasta_evento, id_pai=pasta_id_raiz, token=token) if conectado_nuvem else None

    arquivos_locais = [f for f in os.listdir(pasta_corte) if os.path.isfile(os.path.join(pasta_corte, f))]
    resultado_arquivos = {}
    todos_ok = True

    cache_pastas_modelo_drive = {}

    for nome_arq in arquivos_locais:
        # Metadados internos (.json) e arquivos ocultos não compõem pacotes de cortes para o usuário
        if nome_arq.endswith(".json") or nome_arq.startswith("."):
            continue

        caminho_origem = os.path.join(pasta_corte, nome_arq)
        cat = identificar_categoria_arquivo(nome_arq)

        # Se a variação está explicitamente desativada, ignora
        if variacoes_ativas is not None:
            if cat in ("com_legenda", "sem_legenda", "so_legenda", "cru", "headlines"):
                if not variacoes_ativas.get(cat, True):
                    continue

        nome_subpasta_mod = obter_nome_subpasta_modalidade(cat, variacoes_ativas)

        # 2. Espelhamento local hierárquico: Evento / Subpasta Modelo / Arquivo
        pasta_modelo_local = os.path.join(pasta_evento_local, nome_subpasta_mod)
        os.makedirs(pasta_modelo_local, exist_ok=True)
        destino_local = os.path.join(pasta_modelo_local, nome_arq)
        try:
            if not os.path.exists(destino_local) or os.path.getmtime(caminho_origem) > os.path.getmtime(destino_local):
                shutil.copy2(caminho_origem, destino_local)
        except Exception as e_copy:
            log.debug("Aviso ao copiar espelho local de %s: %s", nome_arq, e_copy)

        # 3. Upload para a nuvem no Google Drive na pasta de modelo diretamente no Evento
        if conectado_nuvem and id_pasta_evento:
            if nome_subpasta_mod not in cache_pastas_modelo_drive:
                cache_pastas_modelo_drive[nome_subpasta_mod] = obter_ou_criar_pasta_drive(
                    nome_subpasta_mod, id_pai=id_pasta_evento, token=token
                )
            id_pasta_mod_drive = cache_pastas_modelo_drive[nome_subpasta_mod]

            if id_pasta_mod_drive:
                upload_info = enviar_arquivo_drive(caminho_origem, nome_arq, id_pasta_mod_drive, token=token)
                if upload_info:
                    resultado_arquivos[nome_arq] = {
                        "categoria": cat,
                        "pasta_drive": nome_subpasta_mod,
                        "pasta_evento": nome_pasta_evento,
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
                "pasta_drive": nome_subpasta_mod,
                "pasta_evento": nome_pasta_evento,
                "caminho_local": destino_local,
                "status": "salvo_local_fallback",
            }

    modo = "nuvem" if conectado_nuvem else "fallback_local"
    return {
        "sucesso": todos_ok if conectado_nuvem else True,
        "modo": modo,
        "pasta_raiz_id": pasta_id_raiz,
        "pasta_video": nome_pasta_evento,
        "pasta_evento": nome_pasta_evento,
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

    tipo_cred = None
    if cred_arquivo and os.path.isfile(cred_arquivo):
        try:
            with open(cred_arquivo, encoding="utf-8") as f:
                c_data = json.load(f)
            if c_data.get("type") == "service_account":
                tipo_cred = "service_account"
            elif "token" in c_data or "refresh_token" in c_data:
                tipo_cred = "oauth_token"
        except Exception:
            pass

    oauth_conectado = (tipo_cred == "oauth_token")
    url_auth = None
    if (not conectado or not oauth_conectado) and oauth_dados:
        url_auth, _ = gerar_url_autorizacao()

    if conectado and oauth_conectado:
        msg = "Conectado ao Google Drive pessoal (OAuth). Uploads ilimitados ativos na nuvem."
    elif conectado and tipo_cred == "service_account":
        msg = (
            "Atenção: Conectado via Service Account (cota 0 bytes em drives pessoais). "
            "Para uploads funcionarem sem erro de cota, conecte sua Conta Google via botão ou Conectar_Google_Drive.bat."
        )
    elif oauth_dados:
        msg = "Operando em fallback local (output/google_drive). Cliente OAuth configurado. Clique em 'Conectar Google Drive' para autorizar."
    else:
        msg = (
            f"Operando em fallback local (output/google_drive). Motivo: {erro}. "
            "Para envio direto à nuvem, insira o arquivo de credenciais do Google Drive ou conecte o OAuth."
        )

    pasta_ativa = obter_pasta_id_ativa()
    modo_status = "nuvem" if (conectado and oauth_conectado) else ("service_account_limitado" if (conectado and tipo_cred == "service_account") else "fallback_local")
    return {
        "conectado": conectado,
        "oauth_conectado": oauth_conectado,
        "tipo_credencial": tipo_cred,
        "modo": modo_status,
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
    """Varre todas as pastas em output/google_drive e envia os arquivos gerados para o Drive na nuvem."""
    if not pasta_id_raiz:
        pasta_id_raiz = obter_pasta_id_ativa()
    pasta_base = config.PASTA_GOOGLE_DRIVE
    if not os.path.isdir(pasta_base):
        return {"ok": False, "mensagem": "Pasta de saída não encontrada"}

    token, erro = obter_token_acesso()
    if not token:
        return {"ok": False, "mensagem": f"Google Drive não conectado: {erro}"}

    st_conn = status_conexao()
    if st_conn.get("tipo_credencial") == "service_account":
        log.warning("Upload na nuvem pausado: Contas de serviço possuem 0 bytes de cota em drives pessoais (@gmail.com). Cortes preservados localmente em %s.", pasta_base)
        return {
            "ok": False,
            "aviso_cota": True,
            "mensagem": "Contas de serviço possuem 0 bytes de cota em drives pessoais do Google Drive (@gmail.com). Conecte sua Conta Google via botão ou Conectar_Google_Drive.bat para realizar o envio à nuvem. Cortes estão salvos e espelhados localmente.",
        }

    enviados = 0
    erros = 0
    detalhes = {}

    for item in sorted(os.listdir(pasta_base)):
        pasta_item = os.path.join(pasta_base, item)
        if not os.path.isdir(pasta_item):
            continue

        # 1. Caso legado: pasta direta FernandoXX
        if item.startswith("Fernando"):
            tit_desc = None
            txt_hl = os.path.join(pasta_item, f"{item}_headlines.txt")
            if os.path.isfile(txt_hl):
                try:
                    with open(txt_hl, "r", encoding="utf-8") as f_hl:
                        for lin in f_hl:
                            if lin.startswith("Vídeo:") or lin.startswith("Video:"):
                                m_tit = re.search(r"Vídeo:\s*([^(\n\r]+)", lin, re.IGNORECASE)
                                if m_tit:
                                    tit_desc = m_tit.group(1).strip()
                                break
                except Exception:
                    pass
            res = enviar_pacote_drive(pasta_item, item, pasta_id_raiz=pasta_id_raiz, titulo_video=tit_desc)
            detalhes[item] = res
            if res.get("sucesso"):
                enviados += 1
            else:
                erros += 1
            continue

        # 2. Caso hierárquico: pasta com [Nome do Vídeo ou Evento]
        # Dentro dela existem subpastas de modelo diretamente ('Corte com headline', 'Corte cru', etc.)
        nome_evento = extrair_nome_evento_principal(item)
        id_pasta_evento = obter_ou_criar_pasta_drive(nome_evento, id_pai=pasta_id_raiz, token=token)

        subpastas = [s for s in os.listdir(pasta_item) if os.path.isdir(os.path.join(pasta_item, s))]
        if subpastas:
            for sub in subpastas:
                pasta_sub = os.path.join(pasta_item, sub)
                if sub.startswith("Fernando"):
                    # Pasta legada intermediária FernandoXX: envia arquivos para pastas de modelo diretamente no evento
                    for arq in os.listdir(pasta_sub):
                        caminho_arq = os.path.join(pasta_sub, arq)
                        if os.path.isfile(caminho_arq) and os.path.getsize(caminho_arq) > 0 and not arq.endswith(".json") and not arq.startswith("."):
                            cat = identificar_categoria_arquivo(arq)
                            sub_mod = obter_nome_subpasta_modalidade(cat)
                            id_pasta_mod = obter_ou_criar_pasta_drive(sub_mod, id_pai=id_pasta_evento, token=token) if id_pasta_evento else None
                            if id_pasta_mod:
                                up = enviar_arquivo_drive(caminho_arq, arq, id_pasta_mod, token=token)
                                if up:
                                    enviados += 1
                                else:
                                    erros += 1
                else:
                    # Subpasta de modelo direta (ex: 'Corte com headline', 'Corte cru')
                    id_pasta_cat = obter_ou_criar_pasta_drive(sub, id_pai=id_pasta_evento, token=token) if id_pasta_evento else None
                    for arq in os.listdir(pasta_sub):
                        caminho_arq = os.path.join(pasta_sub, arq)
                        if os.path.isfile(caminho_arq) and os.path.getsize(caminho_arq) > 0 and not arq.endswith(".json") and not arq.startswith("."):
                            up = enviar_arquivo_drive(caminho_arq, arq, id_pasta_cat, token=token) if id_pasta_cat else None
                            if up:
                                enviados += 1
                            else:
                                erros += 1

    return {
        "ok": True,
        "enviados": enviados,
        "erros": erros,
        "detalhes": detalhes,
        "mensagem": f"Sincronização concluída: {enviados} arquivos enviados para o Google Drive na nuvem.",
    }


def baixar_arquivo_drive(id_arquivo, caminho_destino, token=None):
    """Baixa um arquivo do Google Drive para o disco local usando alt=media."""
    if not token:
        token, _ = obter_token_acesso()
        if not token:
            return False, "Sem token de acesso ao Google Drive"

    url = f"https://www.googleapis.com/drive/v3/files/{id_arquivo}?alt=media"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers)

    os.makedirs(os.path.dirname(os.path.abspath(caminho_destino)), exist_ok=True)
    caminho_tmp = caminho_destino + ".tmp"

    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(caminho_tmp, "wb") as f_out:
            while True:
                chunk = resp.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                f_out.write(chunk)
        if os.path.exists(caminho_destino):
            os.remove(caminho_destino)
        os.rename(caminho_tmp, caminho_destino)
        sz = os.path.getsize(caminho_destino)
        log.info("Arquivo baixado do Google Drive: %s (%d bytes)", caminho_destino, sz)
        return True, None
    except Exception as e:
        if os.path.exists(caminho_tmp):
            try:
                os.remove(caminho_tmp)
            except Exception:
                pass
        log.error("Erro ao baixar arquivo %s do Drive: %s", id_arquivo, e)
        return False, str(e)


def arquivo_existe_no_drive(nome_arquivo, id_pasta_pai, token=None):
    """Verifica se um arquivo com o mesmo nome já existe dentro da pasta pai no Drive com tamanho > 0."""
    if not token:
        token, _ = obter_token_acesso()
        if not token:
            return False
    query = f"'{id_pasta_pai}' in parents and name = '{nome_arquivo}' and trashed = false"
    url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name,size)"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
            files = dados.get("files", [])
            for f in files:
                sz = f.get("size")
                if sz is None or int(sz) > 0:
                    return True
            return False
    except Exception as e:
        log.debug("Aviso ao checar existência de %s no Drive: %s", nome_arquivo, e)
        return False


def listar_videos_pasta_drive(id_pasta=ID_PASTA_PADRAO, token=None):
    """Lista todos os arquivos de vídeo (.mp4, .mkv, .mov, etc.) presentes diretamente na pasta indicada do Drive."""
    if not token:
        token, _ = obter_token_acesso()
        if not token:
            return []
    query = f"'{id_pasta}' in parents and (mimeType contains 'video/' or name contains '.mp4' or name contains '.mkv' or name contains '.mov') and trashed = false"
    headers = {"Authorization": f"Bearer {token}"}
    itens = []
    page_token = None
    while True:
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=nextPageToken,files(id,name,mimeType,size,createdTime)&pageSize=500"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
                itens.extend(dados.get("files", []))
                page_token = dados.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            log.warning("Erro ao listar vídeos da pasta %s no Drive: %s", id_pasta, e)
            break
    return itens


def listar_arquivos_subpasta(id_pasta, token=None):
    """Lista todos os arquivos dentro de uma pasta do Drive com paginação completa."""
    if not token:
        token, _ = obter_token_acesso()
        if not token:
            return []
    query = f"'{id_pasta}' in parents and trashed = false"
    headers = {"Authorization": f"Bearer {token}"}
    itens = []
    page_token = None
    while True:
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=nextPageToken,files(id,name,mimeType,size)&pageSize=500"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
                itens.extend(dados.get("files", []))
                page_token = dados.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            log.warning("Erro ao listar arquivos da pasta %s no Drive: %s", id_pasta, e)
            break
    return itens


