"""Servidor local do Indomável (http://127.0.0.1:5055)."""

import json
import logging
import os
import re
import subprocess

from flask import Flask, jsonify, request, send_file, send_from_directory

from . import acervo_local, config, headlines, legenda, legendas, palavras, render, versao
from .automacao import Automacao
from .chub import Chub, ChubErro
from .gemini import GeminiErro
from .massa import Massa
from .processador import FilaDeLinks
from .tarefas import Fila

ID_YOUTUBE = re.compile(r"^[A-Za-z0-9_-]{11}$")
TAMANHO_PAGINA = 30
TRECHO_MAXIMO_S = 20 * 60

app = Flask(__name__, static_folder=os.path.join(config.PASTA_WEB, "static"), static_url_path="/static")
chub = Chub()
links = FilaDeLinks()


def frases_do_video(youtube_id):
    """Frases do acervo local, se o vídeo foi processado aqui; senão, as do Chub."""
    local = acervo_local.ler(youtube_id, "frases.json")
    if local:
        return local["frases"]
    return chub.transcricao(youtube_id)["frases"]


def trechos_do_corte(youtube_id, inicio, fim, estilo):
    """Legenda palavra por palavra do corte, com a mesma regra do vídeo exportado."""
    return legenda.trechos_de_palavras(palavras.palavras_do_video(youtube_id), inicio, fim, max_palavras=estilo["max_palavras"])


def blocos_do_video(youtube_id):
    local = acervo_local.ler(youtube_id, "blocos.json")
    return local["blocos"] if local else chub.blocos(youtube_id)


def video_resumido(youtube_id):
    info = acervo_local.ler(youtube_id, "info.json")
    if info and acervo_local.ler(youtube_id, "blocos.json"):
        return {"youtube_id": youtube_id, "titulo": info.get("titulo") or youtube_id}
    video = chub.video(youtube_id) or {}
    return {"youtube_id": youtube_id, "titulo": video.get("titulo") or youtube_id}


fila = Fila(frases_do_video, trechos_do_corte)
automacao = Automacao(chub, fila)
massa = Massa(video_resumido, blocos_do_video, frases_do_video, fila)


def _erro(mensagem, status):
    return jsonify({"erro": mensagem}), status


class PedidoInvalido(ValueError):
    """Dados do corte inválidos; a mensagem vai para a tela."""


@app.errorhandler(PedidoInvalido)
def _pedido_invalido(erro):
    return _erro(str(erro), 400)


def _ler_corte(dados):
    youtube_id = str(dados.get("youtube_id", ""))
    if not ID_YOUTUBE.match(youtube_id):
        raise PedidoInvalido("id de vídeo inválido")
    try:
        inicio_s, fim_s = float(dados.get("inicio")), float(dados.get("fim"))
    except (TypeError, ValueError):
        raise PedidoInvalido("início e fim precisam ser números") from None
    if inicio_s < 0 or fim_s <= inicio_s:
        raise PedidoInvalido("o fim precisa vir depois do início")
    if fim_s - inicio_s > TRECHO_MAXIMO_S:
        raise PedidoInvalido("trecho maior que 20 minutos")
    return youtube_id, round(inicio_s, 2), round(fim_s, 2)


def _ler_formato(dados):
    formato = str(dados.get("formato", "9:16"))
    if formato not in render.FORMATOS:
        raise PedidoInvalido("formato desconhecido")
    return formato


@app.errorhandler(ChubErro)
def _erro_do_chub(erro):
    return jsonify({"erro": str(erro), "origem": "chub"}), 502


@app.get("/")
def inicio():
    return send_from_directory(os.path.join(config.PASTA_WEB, "templates"), "index.html")


@app.get("/api/vivo")
def vivo():
    return jsonify({"ok": True, "app": "indomavel", "versao": versao.VERSAO})


@app.get("/api/saude")
def saude():
    return jsonify({"ok": True, "chub": chub.agora()})


# ---------- vídeos do Chub ----------

@app.get("/api/fontes")
def fontes():
    return jsonify({"fontes": chub.fontes()})


@app.get("/api/videos")
def videos():
    pagina = max(0, request.args.get("pagina", 0, type=int))
    fonte = request.args.get("fonte", "").strip() or None
    busca = request.args.get("q", "").strip()[:80] or None
    if fonte and fonte not in chub.fontes():
        return _erro("fonte desconhecida", 400)
    lista = chub.videos_recentes(fonte=fonte, busca=busca, limite=TAMANHO_PAGINA, deslocamento=pagina * TAMANHO_PAGINA)
    return jsonify({"videos": lista, "pagina": pagina, "tamanho": TAMANHO_PAGINA})


@app.get("/api/videos/<youtube_id>/blocos")
def blocos(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    return jsonify({"blocos": chub.blocos(youtube_id)})


@app.post("/api/videos/<youtube_id>/blocos-crus")
def baixar_blocos_crus(youtube_id):
    """Todos os blocos do vídeo, sem edição e na maior qualidade (vídeo inteiro baixado uma vez, recorte sem perda)."""
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    blocos_do_chub = blocos_do_video(youtube_id)
    if not blocos_do_chub:
        return _erro("Este vídeo ainda não tem blocos. O Chub só divide o vídeo depois que o YouTube gera a legenda "
                     "automática em português, o que pode levar horas numa live longa. Tente de novo mais tarde.", 409)
    video = video_resumido(youtube_id)
    tarefa = fila.adicionar_blocos_crus(youtube_id, video["titulo"], blocos_do_chub)
    return jsonify({"tarefa": tarefa}), 202


@app.get("/api/videos/<youtube_id>/transcricao")
def transcricao(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    return jsonify(chub.transcricao(youtube_id))


# ---------- links de fora do Chub ----------

@app.post("/api/links")
def novo_link():
    dados = request.get_json(silent=True) or {}
    youtube_id = legendas.id_do_link(str(dados.get("link", "")))
    if not youtube_id:
        return _erro("não reconheci um link do YouTube", 400)
    refazer = bool(dados.get("refazer"))
    if not refazer and acervo_local.ler(youtube_id, "estado.json", {}).get("estado") == "pronto":
        return jsonify({"situacao": "local_pronto", "youtube_id": youtube_id})
    aviso_chub = None
    try:
        video_chub = chub.video(youtube_id)
    except ChubErro as erro:
        video_chub, aviso_chub = None, str(erro)
    if video_chub and video_chub["blocos"] and not dados.get("processar_aqui"):
        return jsonify({"situacao": "no_chub", "video": video_chub})
    adicionado = links.adicionar(youtube_id, refazer=refazer)
    return jsonify({
        "situacao": "processando" if adicionado else "ja_na_fila",
        "youtube_id": youtube_id,
        "video_chub": video_chub,
        "aviso_chub": aviso_chub,
    }), 202


@app.get("/api/locais")
def locais():
    ativos = links.ativos()
    lista = acervo_local.listar()
    for video in lista:
        if video["estado"] not in acervo_local.ESTADOS_FINAIS and video["youtube_id"] not in ativos:
            video["estado"], video["mensagem"] = "interrompido", "Parou quando o programa fechou."
    return jsonify({"videos": lista})


@app.get("/api/locais/<youtube_id>/blocos")
def blocos_locais(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    dados = acervo_local.ler(youtube_id, "blocos.json")
    if dados is None:
        return _erro("este vídeo ainda não foi dividido em blocos", 404)
    return jsonify({"blocos": dados["blocos"], "ignorados": dados.get("ignorados", []), "modelos": dados.get("modelos", [])})


@app.get("/api/locais/<youtube_id>/transcricao")
def transcricao_de_video_local(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    frases = acervo_local.ler(youtube_id, "frases.json")
    if frases is None:
        return _erro("este vídeo ainda não tem transcrição", 404)
    info = acervo_local.ler(youtube_id, "info.json", {})
    return jsonify({
        "video": {"youtube_id": youtube_id, "titulo": info.get("titulo", ""), "publicado_em": info.get("publicado_em"),
                  "duracao_s": info.get("duracao_s") or 0},
        "frases": frases["frases"],
        "origem": frases.get("origem"),
    })


# ---------- editor: estilo, layout, legenda e headlines ----------

@app.get("/api/estilo")
def estilo_padrao():
    return jsonify({"estilo": render.ESTILO_PADRAO, "formatos": list(render.FORMATOS),
                    "fontes_legenda": list(render.FONTES_LEGENDA)})


@app.post("/api/layout")
def layout_do_corte():
    dados = request.get_json(silent=True) or {}
    formato = _ler_formato(dados)
    estilo = render.normalizar_estilo(dados.get("estilo"))
    return jsonify({"layout": render.layout(formato, estilo), "estilo": estilo})


@app.get("/api/moldura.png")
def moldura():
    try:
        dados = json.loads(request.args.get("d", "{}"))
    except json.JSONDecodeError:
        return _erro("parâmetros inválidos", 400)
    formato = _ler_formato(dados)
    resposta = app.response_class(render.moldura_png(formato, render.normalizar_estilo(dados.get("estilo"))), mimetype="image/png")
    resposta.headers["Cache-Control"] = "public, max-age=3600"
    return resposta


@app.post("/api/legenda")
def legenda_do_corte():
    dados = request.get_json(silent=True) or {}
    youtube_id, inicio_s, fim_s = _ler_corte(dados)
    estilo = render.normalizar_estilo(dados.get("estilo"))
    try:
        trechos = trechos_do_corte(youtube_id, inicio_s, fim_s, estilo)
    except Exception as erro:
        return _erro(f"sem o tempo de cada palavra: {erro}", 502)
    return jsonify({"trechos": trechos})


@app.post("/api/headlines")
def sugerir_headlines():
    dados = request.get_json(silent=True) or {}
    youtube_id, inicio_s, fim_s = _ler_corte(dados)
    frases = frases_do_video(youtube_id)
    texto = " ".join(f["texto"] for f in frases if f["fim"] > inicio_s and f["inicio"] < fim_s)
    temas = [str(t)[:60] for t in dados.get("temas") or [] if isinstance(t, str)][:8]
    destaques = [str(d)[:200] for d in dados.get("destaques") or [] if isinstance(d, str)][:5]
    try:
        resultado = headlines.sugerir(str(dados.get("titulo", ""))[:200], str(dados.get("resumo", ""))[:1000], texto,
                                      categoria=str(dados.get("categoria", ""))[:60], temas=temas, destaques=destaques)
    except GeminiErro as erro:
        return _erro(str(erro), 502)
    return jsonify(resultado)


# ---------- downloads e exportações ----------

@app.get("/api/trechos")
def listar_trechos():
    return jsonify({"tarefas": fila.listar()})


@app.post("/api/trechos")
def novo_trecho():
    dados = request.get_json(silent=True) or {}
    youtube_id, inicio_s, fim_s = _ler_corte(dados)
    tarefa = fila.adicionar(youtube_id, inicio_s, fim_s, str(dados.get("titulo", ""))[:120], bool(dados.get("legenda", True)))
    return jsonify({"tarefa": tarefa}), 202


@app.post("/api/exportar")
def exportar_video():
    dados = request.get_json(silent=True) or {}
    youtube_id, inicio_s, fim_s = _ler_corte(dados)
    formato = _ler_formato(dados)
    estilo = render.normalizar_estilo(dados.get("estilo"))
    tarefa = fila.adicionar_exportacao(youtube_id, inicio_s, fim_s, str(dados.get("titulo", ""))[:120], formato, estilo)
    return jsonify({"tarefa": tarefa}), 202


@app.get("/api/trechos/<ident>")
def ver_trecho(ident):
    tarefa = fila.ver(ident)
    return jsonify({"tarefa": tarefa}) if tarefa else _erro("trecho não encontrado", 404)


def _arquivo_de_download(caminho):
    """Caminho absoluto se o arquivo existe e está dentro da pasta de downloads; senão None."""
    if not caminho:
        return None
    absoluto = os.path.abspath(caminho)
    if not absoluto.startswith(os.path.abspath(config.PASTA_DOWNLOADS)) or not os.path.exists(absoluto):
        return None
    return absoluto


def _mostrar_no_explorer(caminho):
    subprocess.Popen(f'explorer /select,"{caminho}"')


@app.post("/api/trechos/<ident>/mostrar")
def mostrar_na_pasta(ident):
    tarefa = fila.ver(ident)
    caminho = _arquivo_de_download(tarefa and tarefa.get("arquivo"))
    if not caminho:
        return _erro("arquivo não encontrado", 404)
    _mostrar_no_explorer(caminho)
    return jsonify({"ok": True})


# ---------- em massa ----------

@app.post("/api/massa")
def criar_trabalho_em_massa():
    dados = request.get_json(silent=True) or {}
    try:
        trabalho = massa.criar(str(dados.get("modo", "")), dados.get("videos") or [], int(dados.get("por_video", 3)),
                               bool(dados.get("so_prontos")), str(dados.get("formato", "4:5")))
    except (TypeError, ValueError) as erro:
        return _erro(str(erro), 400)
    return jsonify({"trabalho": trabalho}), 202


@app.get("/api/massa")
def listar_trabalhos_em_massa():
    return jsonify({"trabalhos": massa.listar()})


@app.post("/api/massa/<ident>/abrir")
def abrir_pasta_em_massa(ident):
    trabalho = massa.ver(ident)
    pasta = trabalho and trabalho.get("pasta")
    if not pasta or not os.path.isdir(pasta) or not os.path.abspath(pasta).startswith(os.path.abspath(config.PASTA_DOWNLOADS)):
        return _erro("a pasta ainda não existe", 404)
    os.startfile(pasta)
    return jsonify({"ok": True})


# ---------- automação ----------

@app.get("/api/automacao")
def automacao_estado():
    return jsonify(automacao.estado())


@app.post("/api/automacao/config")
def automacao_config():
    try:
        return jsonify({"config": automacao.salvar_config(request.get_json(silent=True) or {})})
    except (TypeError, ValueError):
        return _erro("configuração inválida", 400)


@app.post("/api/automacao/rodar")
def automacao_rodar():
    automacao.rodar_agora()
    return jsonify({"ok": True}), 202


@app.post("/api/automacao/cortes/<ident>")
def automacao_decidir(ident):
    dados = request.get_json(silent=True) or {}
    try:
        corte = automacao.decidir(ident, str(dados.get("acao", "")), str(dados.get("motivo", "")))
    except KeyError:
        return _erro("corte não encontrado", 404)
    except ValueError as erro:
        return _erro(str(erro), 400)
    return jsonify({"corte": corte})


@app.get("/api/automacao/cortes/<ident>/video")
def automacao_video(ident):
    corte = automacao.corte(ident)
    caminho = _arquivo_de_download(corte and corte.get("arquivo"))
    if not caminho:
        return _erro("vídeo não encontrado", 404)
    return send_file(caminho, mimetype="video/mp4", conditional=True)


@app.post("/api/automacao/cortes/<ident>/mostrar")
def automacao_mostrar(ident):
    corte = automacao.corte(ident)
    caminho = _arquivo_de_download(corte and corte.get("arquivo"))
    if not caminho:
        return _erro("arquivo não encontrado", 404)
    _mostrar_no_explorer(caminho)
    return jsonify({"ok": True})


def main():
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"Indomavel em http://127.0.0.1:{config.PORTA}")
    app.run(host="127.0.0.1", port=config.PORTA, debug=False, threaded=True)


if __name__ == "__main__":
    main()
