"""Automação: vigia os vídeos novos do Chub, escolhe os cortes e exporta cada um pronto para revisão.

Blocos "prontos para short" viram corte do tamanho do bloco. Blocos longos do Renan com
vários cortes possíveis passam pelo Gemini, que escolhe os trechos dentro deles pelos
números das frases do Chub e escreve o card. Cada corte é exportado pela mesma fila da
tela (um trabalho pesado por vez) para downloads/automatico. Nada é publicado: tudo fica
"para revisar" até alguém aprovar ou descartar, e cada decisão fica registrada.
"""

import json
import os
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from . import config, gemini, render
from .blocador import linhas_da_transcricao
from .chub import DURACAO_SHORT_S
from .gemini import GeminiErro
from .headlines import EXEMPLOS_DRIVE

PASTA = os.path.join(config.PASTA_DADOS, "automacao")
CONFIG_PADRAO = {"ligada": False, "intervalo_min": 60, "dias": 3, "max_cortes_por_rodada": 6, "formato": "4:5"}
INTERVALOS_MIN = (30, 60, 180, 360)
DURACAO_CORTE_S = (15.0, 100.0)
MAX_BLOCOS_POR_PEDIDO = 5
MOTIVOS_DESCARTE = ("começa mal", "termina mal", "falta contexto", "headline ruim", "não é o Renan", "outro")

INSTRUCAO = """Você é editor de cortes do Renan Santos (Partido Missão) para Reels, TikTok e Shorts, no padrão dos editores da Tropa do Renan.

Você recebe blocos do acervo do Campaign Hub com categoria, temas e as frases numeradas no formato "[índice] (mm:ss) texto".
- Bloco marcado PRONTO: devolva um corte com o bloco inteiro, ou um pouco menor se o começo ou o fim tiverem sobra (conversa, hesitação, passagem de fala).
- Bloco marcado ESCOLHER: devolva até o número de cortes indicado, independentes e sem sobreposição, dentro do bloco.

Regras de cada corte:
- Entre 20 e 90 segundos, pelos tempos das frases.
- Começa na frase que abre a ideia (inclua a pergunta quando ela for necessária) e termina na frase que conclui a ideia. Nunca comece no meio de um raciocínio nem termine antes do fechamento.
- Quem não viu o vídeo precisa entender o corte sozinho.
- Prefira trechos em que o Renan fala.

Card de cada corte:
- tag: 1 a 3 palavras em CAIXA ALTA terminando com exclamação.
- headline: uma frase de até 110 caracteres, em terceira pessoa, fiel ao trecho, construída em torno do tema principal do bloco (use a categoria e os temas informados); não invente fatos, números ou citações.
- motivo: uma frase dizendo por que o corte funciona.

Exemplos reais de card:
""" + "\n".join(f"- {tag} | {headline}" for tag, headline in EXEMPLOS_DRIVE)

ESQUEMA = {
    "type": "object",
    "properties": {
        "cortes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bloco": {"type": "string"},
                    "frase_inicial": {"type": "integer"},
                    "frase_final": {"type": "integer"},
                    "tag": {"type": "string"},
                    "headline": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["bloco", "frase_inicial", "frase_final", "tag", "headline", "motivo"],
            },
        }
    },
    "required": ["cortes"],
}


def selecionar_blocos(blocos, ja_processados, limite=MAX_BLOCOS_POR_PEDIDO):
    """Blocos prontos primeiro, depois os longos do Renan com vários cortes possíveis; os de maior potencial antes."""
    prontos = [b for b in blocos if b["pronto"] and b["id"] not in ja_processados]
    longos = [
        b for b in blocos
        if b["id"] not in ja_processados and not b["pronto"] and b["renan_falando"]
        and b["duracao"] > DURACAO_SHORT_S[1] and b["cortes_possiveis"] >= 2
    ]
    ordem = lambda b: -b["potencial"]  # noqa: E731
    return (sorted(prontos, key=ordem) + sorted(longos, key=ordem))[:limite]


def _novo_corte(video, bloco, inicio, fim, tag, headline, motivo, origem):
    return {
        "id": uuid.uuid4().hex[:10],
        "youtube_id": video["youtube_id"],
        "video_titulo": video["titulo"],
        "bloco_id": bloco["id"],
        "bloco_titulo": bloco["titulo"],
        "inicio": round(inicio, 2),
        "fim": round(fim, 2),
        "tag": tag,
        "headline": headline,
        "motivo": motivo,
        "origem": origem,
        "estado": "na_fila",
        "criado_em": time.time(),
    }


def validar_cortes(brutos, video, blocos, frases):
    """Cortes do Gemini dentro do bloco certo, com duração aceitável e sem sobreposição."""
    por_id = {b["id"]: b for b in blocos}
    por_indice = {f["i"]: f for f in frases}
    aceitos, ocupados = [], {}
    for bruto in sorted(brutos, key=lambda c: (str(c.get("bloco")), c.get("frase_inicial", 0))):
        bloco = por_id.get(str(bruto.get("bloco")))
        inicial, final = bruto.get("frase_inicial"), bruto.get("frase_final")
        if not bloco or not isinstance(inicial, int) or not isinstance(final, int) or final < inicial:
            continue
        if inicial < bloco["frase_inicial"] or final > bloco["frase_final"]:
            continue
        if inicial not in por_indice or final not in por_indice:
            continue
        if any(inicial <= b and final >= a for a, b in ocupados.get(bloco["id"], [])):
            continue
        inicio, fim = por_indice[inicial]["inicio"], por_indice[final]["fim"]
        if not DURACAO_CORTE_S[0] <= fim - inicio <= DURACAO_CORTE_S[1]:
            continue
        ocupados.setdefault(bloco["id"], []).append((inicial, final))
        origem = "bloco_pronto" if bloco["pronto"] else "dentro_de_bloco"
        tag = (bruto.get("tag") or "EM ALTA!").strip().upper()[:30]
        headline = (bruto.get("headline") or bloco["titulo"]).strip()[:160]
        aceitos.append(_novo_corte(video, bloco, inicio, fim, tag, headline, (bruto.get("motivo") or "").strip(), origem))
    return aceitos


def corte_pelo_destaque(video, bloco, frases, minimo_s=25.0, maximo_s=60.0):
    """Sem Gemini: um corte em volta do primeiro momento forte do bloco, com bordas em frases inteiras."""
    do_bloco = [f for f in frases if bloco["frase_inicial"] <= f["i"] <= bloco["frase_final"]]
    if not do_bloco:
        return None
    indices = [f["i"] for f in do_bloco]
    alvo = next((d.get("frase") for d in bloco.get("destaques") or [] if d.get("frase") in indices), indices[0])
    comeco = indices.index(alvo)
    # Uma frase antes do momento forte costuma abrir a ideia, se for a mesma pessoa falando logo antes.
    if comeco > 0 and not do_bloco[comeco].get("troca") and do_bloco[comeco]["inicio"] - do_bloco[comeco - 1]["inicio"] <= 8:
        comeco -= 1
    fim = comeco
    while fim + 1 < len(do_bloco):
        duracao = do_bloco[fim]["fim"] - do_bloco[comeco]["inicio"]
        if duracao >= minimo_s and do_bloco[fim]["texto"].rstrip().endswith((".", "?", "!")):
            break
        if do_bloco[fim + 1]["fim"] - do_bloco[comeco]["inicio"] > maximo_s:
            break
        fim += 1
    while comeco > 0 and do_bloco[fim]["fim"] - do_bloco[comeco]["inicio"] < minimo_s:
        comeco -= 1
    inicio, final = do_bloco[comeco]["inicio"], do_bloco[fim]["fim"]
    if final - inicio < DURACAO_CORTE_S[0]:
        return None
    return _novo_corte(video, bloco, inicio, final, "EM ALTA!", bloco["titulo"],
                       "Sem Gemini: corte em volta do momento forte marcado pelo Chub.", "dentro_de_bloco")


def cortes_sem_gemini(video, blocos, frases=None):
    """Quando o Gemini não responde: blocos prontos inteiros e, se houver frases, um corte pelo momento
    forte de cada bloco longo. O card usa o título do bloco."""
    cortes = []
    for bloco in blocos:
        if bloco["pronto"]:
            cortes.append(_novo_corte(video, bloco, bloco["inicio"], bloco["fim"], "EM ALTA!", bloco["titulo"],
                                      "Bloco pronto do Chub, sem ajuste do Gemini.", "bloco_pronto"))
        elif frases:
            corte = corte_pelo_destaque(video, bloco, frases)
            if corte:
                cortes.append(corte)
    return cortes


def conteudo_do_pedido(video, blocos, frases, cortes_por_bloco=None):
    partes = [f"VÍDEO: {video['titulo']}"]
    for bloco in blocos:
        do_bloco = [f for f in frases if bloco["frase_inicial"] <= f["i"] <= bloco["frase_final"]]
        quantos = cortes_por_bloco or min(3, max(1, bloco["cortes_possiveis"]))
        marca = "PRONTO" if bloco["pronto"] else f"ESCOLHER, até {quantos} corte(s)"
        partes.append(
            f"\nBLOCO {bloco['id']} ({marca})\nTítulo: {bloco['titulo']}\nResumo: {bloco['resumo']}\n"
            f"Categoria: {bloco.get('categoria', '')}\nTemas: {', '.join(bloco.get('temas') or [])}\n"
            f"{linhas_da_transcricao(do_bloco)}"
        )
    return "\n".join(partes)


def _data(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


class Automacao:
    def __init__(self, chub, fila, intervalo_vigia_s=30):
        self.chub = chub
        self.fila = fila
        self.intervalo_vigia_s = intervalo_vigia_s
        self._trava = threading.RLock()
        self._acordar = threading.Event()
        self._pedido_manual = False
        self.rodando = False
        self.mensagem = "Parada"
        self.ultima_rodada = None
        self.proxima_rodada = None
        os.makedirs(PASTA, exist_ok=True)
        threading.Thread(target=self._vigiar, name="automacao", daemon=True).start()

    # ---------- arquivos ----------

    def _ler(self, nome, padrao):
        caminho = os.path.join(PASTA, nome)
        with self._trava:
            if not os.path.exists(caminho):
                return padrao
            with open(caminho, encoding="utf-8") as arquivo:
                return json.load(arquivo)

    def _salvar(self, nome, dados):
        caminho = os.path.join(PASTA, nome)
        with self._trava:
            with open(caminho + ".tmp", "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False, indent=1)
            for tentativa in range(10):
                try:
                    os.replace(caminho + ".tmp", caminho)
                    break
                except PermissionError:
                    if tentativa == 9:
                        raise
                    time.sleep(0.05 * (tentativa + 1))

    def _guardar_corte(self, corte):
        with self._trava:
            cortes = [c for c in self._ler("cortes.json", []) if c["id"] != corte["id"]]
            cortes.append(corte)
            self._salvar("cortes.json", cortes)

    # ---------- o que a tela usa ----------

    def config(self):
        return {**CONFIG_PADRAO, **self._ler("config.json", {})}

    def salvar_config(self, dados):
        atual = self.config()
        if "ligada" in dados:
            atual["ligada"] = bool(dados["ligada"])
        if int(dados.get("intervalo_min", atual["intervalo_min"])) in INTERVALOS_MIN:
            atual["intervalo_min"] = int(dados.get("intervalo_min", atual["intervalo_min"]))
        atual["dias"] = max(1, min(14, int(dados.get("dias", atual["dias"]))))
        atual["max_cortes_por_rodada"] = max(1, min(20, int(dados.get("max_cortes_por_rodada", atual["max_cortes_por_rodada"]))))
        if dados.get("formato", atual["formato"]) in render.FORMATOS:
            atual["formato"] = dados.get("formato", atual["formato"])
        self._salvar("config.json", atual)
        if atual["ligada"] and self.proxima_rodada is None:
            self._acordar.set()
        if not atual["ligada"]:
            self.proxima_rodada = None
        return atual

    def estado(self):
        cortes = sorted(self._ler("cortes.json", []), key=lambda c: c["criado_em"], reverse=True)
        return {
            "config": self.config(),
            "rodando": self.rodando,
            "mensagem": self.mensagem,
            "ultima_rodada": self.ultima_rodada,
            "proxima_rodada": self.proxima_rodada,
            "cortes": cortes,
            "motivos_descarte": list(MOTIVOS_DESCARTE),
        }

    def corte(self, ident):
        return next((c for c in self._ler("cortes.json", []) if c["id"] == ident), None)

    def rodar_agora(self):
        self._pedido_manual = True
        self._acordar.set()

    def decidir(self, ident, acao, motivo=""):
        corte = self.corte(ident)
        if not corte:
            raise KeyError("corte não encontrado")
        if acao == "aprovar":
            if corte.get("arquivo") and os.path.exists(corte["arquivo"]):
                pasta = os.path.join(config.PASTA_DOWNLOADS, "aprovados", time.strftime("%Y-%m-%d"))
                os.makedirs(pasta, exist_ok=True)
                destino = os.path.join(pasta, os.path.basename(corte["arquivo"]))
                shutil.move(corte["arquivo"], destino)
                corte["arquivo"] = destino
            corte["estado"] = "aprovado"
        elif acao == "descartar":
            corte["estado"] = "descartado"
            corte["motivo_descarte"] = motivo if motivo in MOTIVOS_DESCARTE else "outro"
        else:
            raise ValueError("ação desconhecida")
        corte["decidido_em"] = time.time()
        self._guardar_corte(corte)
        # O caderno de vereditos: cada decisão fica guardada para calibrar a escolha dos cortes depois.
        with self._trava:
            with open(os.path.join(PASTA, "vereditos.jsonl"), "a", encoding="utf-8") as arquivo:
                arquivo.write(json.dumps({"acao": acao, "motivo": corte.get("motivo_descarte"), "corte": corte}, ensure_ascii=False) + "\n")
        return corte

    # ---------- a rodada ----------

    def _vigiar(self):
        while True:
            configuracao = self.config()
            agora = time.time()
            vencida = configuracao["ligada"] and (self.proxima_rodada is None or agora >= self.proxima_rodada)
            if self._pedido_manual or vencida:
                self._pedido_manual = False
                self.rodando = True
                try:
                    self._rodada(configuracao)
                except Exception as erro:
                    self.mensagem = f"A rodada falhou: {erro}"
                    traceback.print_exc()
                finally:
                    self.rodando = False
                    self.ultima_rodada = time.time()
                    configuracao = self.config()
                    self.proxima_rodada = time.time() + configuracao["intervalo_min"] * 60 if configuracao["ligada"] else None
            self._acordar.wait(timeout=self.intervalo_vigia_s)
            self._acordar.clear()

    def _rodada(self, configuracao):
        self.mensagem = "Procurando vídeos novos no Chub"
        processados = self._ler("processados.json", {})
        limite = datetime.now(timezone.utc) - timedelta(days=configuracao["dias"])
        videos = [v for v in self.chub.videos_recentes(limite=25) if v["blocos"] and _data(v["publicado_em"]) >= limite]
        restantes = configuracao["max_cortes_por_rodada"]
        feitos, avisos = 0, []
        for video in videos:
            if restantes <= 0:
                break
            ja = set(processados.get(video["youtube_id"], []))
            blocos = selecionar_blocos(self.chub.blocos(video["youtube_id"]), ja)
            if not blocos:
                continue
            self.mensagem = f"Escolhendo cortes em “{video['titulo']}”"
            frases = self.chub.transcricao(video["youtube_id"])["frases"]
            try:
                dados, modelo = gemini.gerar_json(INSTRUCAO, conteudo_do_pedido(video, blocos, frases), ESQUEMA)
                cortes = validar_cortes(dados.get("cortes") or [], video, blocos, frases)
                respondeu = True
            except GeminiErro as erro:
                # A vigia não usa o corte pelo momento forte: o bloco longo fica para a próxima rodada com Gemini.
                cortes = cortes_sem_gemini(video, blocos)
                respondeu = False
                avisos.append(f"{gemini.motivo_curto(erro)}; só blocos prontos, com o título do Chub como headline")
            exportados_por_bloco = {}
            for corte in cortes:
                if restantes <= 0:
                    break
                self.mensagem = f"Exportando {feitos + 1}: {corte['headline'][:60]}"
                self._exportar(corte, configuracao)
                exportados_por_bloco[corte["bloco_id"]] = exportados_por_bloco.get(corte["bloco_id"], 0) + 1
                restantes -= 1
                feitos += 1
            planejados_por_bloco = {}
            for corte in cortes:
                planejados_por_bloco[corte["bloco_id"]] = planejados_por_bloco.get(corte["bloco_id"], 0) + 1
            for bloco in blocos:
                todos_feitos = exportados_por_bloco.get(bloco["id"], 0) == planejados_por_bloco.get(bloco["id"], 0)
                if todos_feitos and (respondeu or bloco["pronto"]):
                    ja.add(bloco["id"])
            processados[video["youtube_id"]] = sorted(ja)
            self._salvar("processados.json", processados)
        resumo = f"Rodada terminada: {feitos} corte(s) novo(s) para revisar"
        self.mensagem = resumo + (f". Atenção: {avisos[0]}" if avisos else "")

    def _exportar(self, corte, configuracao):
        # Legenda automática pode ter erro: o vídeo sai só com o card e a legenda vai num .srt ao lado.
        estilo = render.normalizar_estilo({"tag": corte["tag"], "headline": corte["headline"], "legenda": False})
        corte.update(estado="exportando", formato=configuracao["formato"])
        self._guardar_corte(corte)
        tarefa = self.fila.adicionar_exportacao(
            corte["youtube_id"], corte["inicio"], corte["fim"], corte["headline"] or corte["bloco_titulo"],
            configuracao["formato"], estilo, subpasta=os.path.join("automatico", time.strftime("%Y-%m-%d")),
            srt_separado=True,
        )
        while True:
            atual = self.fila.ver(tarefa["id"])
            if atual["estado"] in ("pronta", "falhou"):
                break
            time.sleep(2)
        if atual["estado"] == "pronta":
            corte.update(estado="para_revisar", arquivo=atual["arquivo"], legenda=atual.get("legenda"), aviso=atual["mensagem"])
        else:
            corte.update(estado="falhou", aviso=atual["mensagem"])
        self._guardar_corte(corte)
