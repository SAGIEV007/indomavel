"""Conexão do Indomável com o Campaign Hub (Chub).

O próprio programa fala com o servidor MCP do Chub por HTTP, sem depender de
nenhum assistente. Qualquer falha vira ChubErro: uma lista vazia que sai daqui
sempre quer dizer "o Chub respondeu que não há nada", nunca "a conexão caiu".
"""

import json
import threading
import time
import urllib.error
import urllib.request

from . import config

TEMPO_LIMITE_S = 60
VALIDADE_CACHE_S = 300
# Mesmos pesos da pauta do Chub: força (densityRank), autonomia (selfContainedRank), cortes possíveis.
PESOS_POTENCIAL = (0.55, 0.30, 0.15)
DURACAO_SHORT_S = (20.0, 120.0)


class ChubErro(RuntimeError):
    """O Chub não respondeu, recusou a chave ou devolveu um erro."""


def texto_sql(valor):
    """Literal de texto seguro para o chub_sql, que não aceita parâmetros separados."""
    valor = str(valor).replace("\x00", "")
    return "'" + valor.replace("'", "''") + "'"


def _numero(valor, padrao=0.0):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def _inteiro(valor, padrao=0):
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return padrao


def _booleano(valor):
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "t", "1")
    return bool(valor)


def _decodificar(bruto, tipo_conteudo):
    if "text/event-stream" in tipo_conteudo:
        mensagem = None
        for linha in bruto.splitlines():
            if not linha.startswith("data:"):
                continue
            try:
                candidata = json.loads(linha[5:].strip())
            except json.JSONDecodeError:
                continue
            if "result" in candidata or "error" in candidata:
                mensagem = candidata
        if mensagem is None:
            raise ChubErro("o Chub respondeu em stream sem resultado")
        return mensagem
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        raise ChubErro("a resposta do Chub não é JSON: " + bruto[:120]) from None


def _conteudo(resultado):
    if "structuredContent" in resultado:
        return resultado["structuredContent"]
    texto = "".join(parte.get("text", "") for parte in resultado.get("content") or [])
    if not texto:
        raise ChubErro("o Chub respondeu sem conteúdo")
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        raise ChubErro("o conteúdo do Chub não é JSON: " + texto[:120]) from None


def potencial(bloco):
    """Nota de 0 a 100 para ordenar blocos como a pauta do Chub ordena."""
    forca = _numero(bloco.get("densityRank"))
    autonomia = _numero(bloco.get("selfContainedRank"))
    cortes = min(_inteiro(bloco.get("possibleCuts")), 4) / 4 * 100
    peso_forca, peso_autonomia, peso_cortes = PESOS_POTENCIAL
    return round(peso_forca * forca + peso_autonomia * autonomia + peso_cortes * cortes, 1)


def _bloco(bruto):
    duracao = _numero(bruto.get("durationS"))
    renan = _booleano(bruto.get("renanSpeaking"))
    precisa_contexto = _booleano(bruto.get("needsContext"))
    return {
        "id": bruto.get("id"),
        "titulo": bruto.get("title") or "",
        "resumo": bruto.get("summary") or "",
        "categoria": bruto.get("category") or "",
        "temas": bruto.get("topics") or [],
        "inicio": _numero(bruto.get("startS")),
        "fim": _numero(bruto.get("endS")),
        "duracao": duracao,
        "renan_falando": renan,
        "precisa_contexto": precisa_contexto,
        "cortes_possiveis": _inteiro(bruto.get("possibleCuts")),
        "forca": _inteiro(bruto.get("densityRank")),
        "autonomia": _inteiro(bruto.get("selfContainedRank")),
        "pergunta": bruto.get("triggerQuestion") or "",
        "riscos": bruto.get("riskFlags") or [],
        "avisos": bruto.get("gateWarnings") or [],
        "frase_inicial": bruto.get("startSentenceIdx"),
        "frase_final": bruto.get("endSentenceIdx"),
        "destaques": [
            {
                "inicio": _numero(h.get("startS")),
                "fim": _numero(h.get("endS")),
                "texto": h.get("text") or "",
                "motivo": h.get("reason") or "",
                "frase": h.get("sentenceIdx"),
            }
            for h in bruto.get("highlights") or []
        ],
        "potencial": potencial(bruto),
        "pronto": renan and not precisa_contexto and DURACAO_SHORT_S[0] <= duracao <= DURACAO_SHORT_S[1],
    }


def _video(linha):
    return {
        "youtube_id": linha.get("youtube_id"),
        "titulo": linha.get("titulo") or "",
        "publicado_em": linha.get("publicado_em"),
        "duracao_s": _inteiro(linha.get("duracao_s")),
        "ao_vivo": linha.get("ao_vivo"),
        "fontes": linha.get("fontes") or "",
        "tem_transcricao": _booleano(linha.get("tem_transcricao")),
        "blocos": _inteiro(linha.get("blocos")),
        "blocos_qa": _inteiro(linha.get("blocos_qa")),
        "origem": "chub",
    }


class Chub:
    def __init__(self, url=None, tempo_limite=TEMPO_LIMITE_S, validade_cache=VALIDADE_CACHE_S):
        self.url = url or config.CHUB_MCP_URL
        if not self.url:
            raise ChubErro("CHUB_MCP_URL não está no arquivo .env")
        self.tempo_limite = tempo_limite
        self.validade_cache = validade_cache
        self._cache = {}
        self._trava = threading.Lock()
        self._contador = 0

    def chamar(self, ferramenta, argumentos=None, usar_cache=True):
        """Chama uma ferramenta do Chub e devolve o conteúdo já decodificado."""
        argumentos = argumentos or {}
        chave = (ferramenta, json.dumps(argumentos, sort_keys=True))
        if usar_cache:
            with self._trava:
                guardado = self._cache.get(chave)
            if guardado and time.time() - guardado[0] < self.validade_cache:
                return guardado[1]
        with self._trava:
            self._contador += 1
            ident = self._contador
        corpo = json.dumps({
            "jsonrpc": "2.0",
            "id": ident,
            "method": "tools/call",
            "params": {"name": ferramenta, "arguments": argumentos},
        }).encode("utf-8")
        pedido = urllib.request.Request(self.url, data=corpo, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Indomavel/1.0",
        })
        try:
            with urllib.request.urlopen(pedido, timeout=self.tempo_limite) as resposta:
                tipo = resposta.headers.get("Content-Type", "")
                bruto = resposta.read().decode("utf-8")
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", "replace")[:200].strip()
            raise ChubErro(f"{ferramenta}: HTTP {erro.code} {detalhe}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            motivo = getattr(erro, "reason", erro)
            raise ChubErro(f"{ferramenta}: sem conexão com o Chub ({motivo})") from None

        mensagem = _decodificar(bruto, tipo)
        if "error" in mensagem:
            erro = mensagem["error"]
            texto = erro.get("message") if isinstance(erro, dict) else erro
            raise ChubErro(f"{ferramenta}: {texto}")
        resultado = mensagem.get("result") or {}
        if resultado.get("isError"):
            texto = " ".join(parte.get("text", "") for parte in resultado.get("content") or [])
            raise ChubErro(f"{ferramenta}: {texto[:300] or 'erro sem descrição'}")
        dados = _conteudo(resultado)
        if usar_cache:
            with self._trava:
                self._cache[chave] = (time.time(), dados)
        return dados

    def sql(self, consulta, usar_cache=True, limite=500):
        dados = self.chamar("chub_sql", {"query": consulta, "rowLimit": limite}, usar_cache)
        if not isinstance(dados, dict) or "rows" not in dados:
            raise ChubErro("chub_sql: resposta sem linhas")
        return dados["rows"]

    def agora(self):
        """Hora do servidor e total de blocos, sempre sem cache: prova de conexão viva."""
        linhas = self.sql(
            "select now() as agora, (select count(*) from blocks where status = 'active') as blocos_ativos",
            usar_cache=False,
        )
        return linhas[0]

    def fontes(self):
        return [linha["label"] for linha in self.sql("select label from yt_sources where active order by label")]

    def video(self, youtube_id):
        """O vídeo do Chub com esse id do YouTube, ou None se o Chub não acompanha esse vídeo."""
        encontrados = self.videos_recentes(youtube_id=youtube_id, limite=1)
        return encontrados[0] if encontrados else None

    def videos_recentes(self, fonte=None, busca=None, limite=30, deslocamento=0, youtube_id=None,
                        filtro_tatico=None, so_com_blocos=False):
        filtros = [
            "v.platform = 'youtube'",
            "exists (select 1 from yt_video_sources yvs where yvs.video_id = v.id)",
        ]
        if youtube_id:
            filtros.append(f"v.external_id = {texto_sql(youtube_id)}")
        if fonte:
            filtros.append(
                "exists (select 1 from yt_video_sources yvs join yt_sources ys on ys.id = yvs.source_id "
                f"where yvs.video_id = v.id and ys.label = {texto_sql(fonte)})"
            )
        if busca:
            filtros.append(f"v.title ilike {texto_sql('%' + busca + '%')}")
        if so_com_blocos:
            filtros.append("exists (select 1 from blocks b join sentence_tables st on st.id = b.sentence_table_id where st.video_id = v.id and b.status = 'active')")
        if filtro_tatico == "virais":
            filtros.append(
                "exists (select 1 from blocks b join sentence_tables st on st.id = b.sentence_table_id "
                "where st.video_id = v.id and b.status = 'active' and b.density_rank > 80)"
            )
        elif filtro_tatico == "stf":
            filtros.append(
                "exists (select 1 from blocks b join sentence_tables st on st.id = b.sentence_table_id "
                "where st.video_id = v.id and b.status = 'active' and "
                "(b.title ilike '%stf%' or b.title ilike '%confronto%' or b.summary ilike '%stf%' or b.summary ilike '%moraes%' "
                "or v.title ilike '%stf%' or exists (select 1 from unnest(b.topics) t where t ilike '%stf%' or t ilike '%confronto%' or t ilike '%moraes%')))"
            )
        elif filtro_tatico == "economia":
            filtros.append(
                "exists (select 1 from blocks b join sentence_tables st on st.id = b.sentence_table_id "
                "where st.video_id = v.id and b.status = 'active' and "
                "(b.category ilike '%economia%' or b.title ilike '%rombo%' or b.title ilike '%economia%' or b.title ilike '%gasto%' "
                "or b.title ilike '%impost%' or b.summary ilike '%rombo%' or b.summary ilike '%economia%' "
                "or exists (select 1 from unnest(b.topics) t where t ilike '%economia%' or t ilike '%rombo%' or t ilike '%imposto%' or t ilike '%inflação%')))"
            )
        elif filtro_tatico == "curtos":
            filtros.append(
                "exists (select 1 from blocks b join sentence_tables st on st.id = b.sentence_table_id "
                "where st.video_id = v.id and b.status = 'active' and b.duration_s <= 90 and b.duration_s >= 20)"
            )
        consulta = f"""
            select v.external_id as youtube_id, v.title as titulo, v.published_at as publicado_em,
                   v.duration_s as duracao_s, v.youtube_live_status as ao_vivo,
                   (select string_agg(distinct ys.label, ' · ') from yt_video_sources yvs
                      join yt_sources ys on ys.id = yvs.source_id where yvs.video_id = v.id) as fontes,
                   exists (select 1 from sentence_tables st where st.video_id = v.id) as tem_transcricao,
                   (select count(*) from blocks b join sentence_tables st on st.id = b.sentence_table_id
                     where st.video_id = v.id and b.status = 'active') as blocos,
                   (select count(*) from blocks b join sentence_tables st on st.id = b.sentence_table_id
                     where st.video_id = v.id and b.status = 'active' and b.renan_speaking = true and not b.needs_context and b.duration_s between 20 and 120) as blocos_qa
            from videos v
            where {' and '.join(filtros)}
            order by v.published_at desc nulls last
            limit {int(limite)} offset {int(deslocamento)}
        """
        return [_video(linha) for linha in self.sql(consulta)]

    def blocos(self, youtube_id):
        """Todos os blocos ativos do vídeo, em ordem de aparição."""
        itens, cursor = [], None
        while True:
            argumentos = {"videoId": youtube_id, "limit": 500, "mode": "lexical"}
            if cursor:
                argumentos["cursor"] = cursor
            dados = self.chamar("chub_acervo_blocks", argumentos)
            itens.extend(dados.get("items") or [])
            cursor = dados.get("nextCursor")
            if not cursor:
                break
        return sorted((_bloco(item) for item in itens), key=lambda bloco: bloco["inicio"])

    def transcricao(self, youtube_id):
        """Frases do vídeo inteiro, com tempo por frase (legenda automática do YouTube)."""
        frases, inicio, video = [], 0, None
        while True:
            dados = self.chamar("chub_acervo_transcript", {
                "videoId": youtube_id,
                "limit": 5000,
                "startSentenceIdx": inicio,
                "includeIgnoredRegions": False,
            })
            video = video or dados.get("video")
            for frase in dados.get("sentences") or []:
                frases.append({
                    "i": frase.get("idx"),
                    "inicio": _numero(frase.get("startS")),
                    "fim": _numero(frase.get("endS")),
                    "texto": frase.get("text") or "",
                    "troca": _booleano(frase.get("speakerChange")),
                    "conferir": bool(frase.get("audioCheckRanges")),
                })
            proximo = (dados.get("page") or {}).get("nextSentenceIdx")
            if proximo is None or proximo <= inicio:
                break
            inicio = proximo
        video = video or {}
        return {
            "video": {
                "youtube_id": video.get("youtubeId") or youtube_id,
                "titulo": video.get("title") or "",
                "publicado_em": video.get("publishedAt"),
                "duracao_s": _inteiro(video.get("durationS")),
            },
            "frases": frases,
        }
