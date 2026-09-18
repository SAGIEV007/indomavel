"""Servidor local do Indomável (http://127.0.0.1:5055)."""

import json
import logging
import os
import re
import subprocess

from flask import Flask, jsonify, request, send_file, send_from_directory

from . import acervo_local, config, cortador_automatico, google_drive, gravador_live, headlines, legenda, legendas, palavras, render, versao
from .automacao import Automacao
from .chub import Chub, ChubErro
from .gemini import GeminiErro
from .massa import Massa
from .processador import FilaDeLinks
from .sincronizador_playlist import SincronizadorPlaylist
from .tarefas import Fila

ID_YOUTUBE = re.compile(r"^[A-Za-z0-9_-]{11}$")
TAMANHO_PAGINA = 30
TRECHO_MAXIMO_S = 20 * 60

app = Flask(__name__, static_folder=os.path.join(config.PASTA_WEB, "static"), static_url_path="/static")
chub = Chub()
links = FilaDeLinks()
sincronizador = SincronizadorPlaylist(fila_links=links)
sincronizador.iniciar()


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


def ao_concluir_blocagem(youtube_id, info, blocos, frases):
    """Gatilho em tempo real: se o modo automático estiver ativo, dispara geração sequencial dos pacotes FernandoXX."""
    if not cortador_automatico.obter_modo_automatico():
        logging.getLogger(__name__).info(
            "Modo de cortes automáticos está DESLIGADO. Vídeo %s aguardando disparo manual ou ativação do modo automático.",
            youtube_id,
        )
        return

    if not cortador_automatico.video_eh_elegivel_por_ponto_partida(youtube_id):
        logging.getLogger(__name__).info(
            "Vídeo %s ignorado pelo ponto de partida configurado pelo operador.",
            youtube_id,
        )
        return

    try:
        cortador_automatico.processar_blocos_automaticamente(
            fila=fila,
            youtube_id=youtube_id,
            info=info,
            blocos=blocos,
            frases=frases,
            exportar_crus=True,
            exportar_9x16=True,
        )
    except Exception as erro:
        logging.getLogger(__name__).warning("Erro ao disparar cortes automáticos de %s: %s", youtube_id, erro)


links.ao_concluir_blocagem = ao_concluir_blocagem


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
    filtro = request.args.get("filtro", "").strip() or None
    so_com_blocos = request.args.get("so_com_blocos", "1").lower() not in ("0", "false", "f")
    if fonte and fonte not in chub.fontes():
        return _erro("fonte desconhecida", 400)
    lista = chub.videos_recentes(
        fonte=fonte, busca=busca, limite=TAMANHO_PAGINA, deslocamento=pagina * TAMANHO_PAGINA,
        filtro_tatico=filtro, so_com_blocos=so_com_blocos,
    )
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


# ---------- playlist de transmissões ao vivo gravadas ----------

@app.get("/api/playlist")
def listar_playlist():
    return jsonify({
        "videos": sincronizador.listar_videos(),
        "status": sincronizador.status(),
    })


@app.post("/api/playlist/sincronizar")
def sincronizar_playlist():
    resultado = sincronizador.sincronizar_agora()
    return jsonify(resultado)


@app.get("/api/playlist/status")
def status_playlist():
    return jsonify(sincronizador.status())


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
    if "com_legenda" in dados:
        estilo["legenda"] = bool(dados["com_legenda"])
    if "com_headline" in dados:
        estilo["card"] = bool(dados["com_headline"])
    if "com_marca" in dados:
        estilo["rodape"] = bool(dados["com_marca"])
    trechos = dados.get("trechos")

    # Registro no loop de aprendizado contínuo (Active Learning)
    if estilo.get("headline"):
        try:
            headlines.registrar_veredito(
                youtube_id=youtube_id,
                bloco_id=str(dados.get("bloco_id") or ""),
                headline=estilo["headline"],
                tag=estilo.get("tag", ""),
                angulo=dados.get("angulo"),
                acao="aprovado_para_exportar",
            )
        except Exception:
            pass

    tarefa = fila.adicionar_exportacao(youtube_id, inicio_s, fim_s, str(dados.get("titulo", ""))[:120], formato, estilo, trechos=trechos)
    return jsonify({"tarefa": tarefa}), 202


@app.post("/api/headlines/feedback")
def feedback_headline():
    dados = request.get_json(silent=True) or {}
    youtube_id = str(dados.get("youtube_id", ""))
    bloco_id = str(dados.get("bloco_id", ""))
    tag = str(dados.get("tag", ""))
    headline_txt = str(dados.get("headline", ""))
    acao = str(dados.get("acao", "aprovado"))
    motivo = str(dados.get("motivo", ""))
    angulo = str(dados.get("angulo", ""))
    registro = headlines.registrar_veredito(
        youtube_id=youtube_id,
        bloco_id=bloco_id,
        headline=headline_txt,
        tag=tag,
        angulo=angulo,
        acao=acao,
        motivo=motivo,
    )
    return jsonify({"ok": True, "registro": registro})


@app.get("/api/cortes/<youtube_id>")
@app.get("/api/cortes_automaticos/<youtube_id>")
def status_cortes_automaticos(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    status = cortador_automatico.obter_status_cortes(youtube_id)
    return jsonify({"status": status or {}})


@app.get("/api/automacao/cortes/status")
def status_modo_cortes_automaticos():
    return jsonify({
        "modo_automatico": cortador_automatico.obter_modo_automatico(),
        "executor": cortador_automatico.executor_sequencial.status_atual(),
    })


@app.post("/api/automacao/cortes/toggle")
def toggle_modo_cortes_automaticos():
    dados = request.get_json(silent=True) or {}
    ativo = dados.get("ativo")
    if ativo is None:
        ativo = not cortador_automatico.obter_modo_automatico()
    novo_estado = cortador_automatico.definir_modo_automatico(ativo)
    return jsonify({
        "ok": True,
        "modo_automatico": novo_estado,
        "mensagem": f"Modo de cortes automáticos {'ATIVADO' if novo_estado else 'DESLIGADO'}.",
    })


@app.get("/api/automacao/config-cortes")
def obter_config_cortes():
    return jsonify({"ok": True, "config": cortador_automatico.carregar_config_cortes()})


@app.post("/api/automacao/config-cortes")
def atualizar_config_cortes():
    dados = request.get_json(silent=True) or {}
    salvo = cortador_automatico.salvar_config_cortes(dados)
    return jsonify({"ok": True, "config": salvo, "mensagem": "Preferências de cortes salvas com sucesso!"})


@app.get("/api/drive/status")
def status_google_drive():
    return jsonify(google_drive.status_conexao())


@app.post("/api/drive/configurar")
def configurar_google_drive():
    dados = request.get_json(silent=True) or {}
    pasta_id = dados.get("pasta_id")
    if pasta_id:
        cortador_automatico.salvar_config_cortes({"drive": {"pasta_id": pasta_id}})
    credenciais = dados.get("credenciais")
    if credenciais:
        google_drive.salvar_credenciais(credenciais)
    return jsonify({"ok": True, "status": google_drive.status_conexao()})


@app.post("/api/drive/testar")
def testar_conexao_drive():
    return jsonify(google_drive.status_conexao())


@app.post("/api/drive/abrir-pasta-local")
def abrir_pasta_drive_local():
    os.makedirs(config.PASTA_GOOGLE_DRIVE, exist_ok=True)
    try:
        os.startfile(config.PASTA_GOOGLE_DRIVE)
        return jsonify({"ok": True, "caminho": config.PASTA_GOOGLE_DRIVE})
    except Exception as e:
        return _erro(f"Erro ao abrir pasta: {e}", 500)


@app.get("/api/drive/auth-url")
def obter_auth_url_drive():
    url, erro = google_drive.gerar_url_autorizacao()
    if not url:
        return _erro(erro or "Não foi possível gerar URL de autorização", 400)
    return jsonify({"ok": True, "url": url})


@app.get("/oauth2callback")
def oauth2callback():
    code = request.args.get("code")
    erro = request.args.get("error")
    if erro:
        return f"""
        <html><body style="font-family:system-ui,sans-serif;background:#0d1117;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
          <div style="background:#161b22;padding:32px;border-radius:12px;max-width:500px;text-align:center;border:1px solid #30363d;">
            <h2 style="color:#ff4757;margin-top:0;">❌ Autorização Cancelada</h2>
            <p style="color:#8b949e;">O Google retornou: <code>{erro}</code></p>
            <p style="margin-top:24px;"><a href="/" style="background:#238636;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;">Voltar ao Indomável</a></p>
          </div>
        </body></html>
        """, 400

    if not code:
        return "Código de autorização não recebido", 400

    ok, msg = google_drive.trocar_codigo_por_token(code)
    if ok:
        return """
        <html><body style="font-family:system-ui,sans-serif;background:#0d1117;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
          <div style="background:#161b22;padding:32px;border-radius:12px;max-width:500px;text-align:center;border:1px solid #30363d;">
            <h1 style="color:#2ed573;margin-top:0;">✅ Conectado com Sucesso!</h1>
            <p style="color:#c9d1d9;font-size:15px;line-height:1.5;">Sua conta do Google Drive foi autorizada. Os cortes automáticos agora serão enviados diretamente para a pasta na nuvem.</p>
            <p style="margin-top:24px;"><a href="/" style="background:#2ea043;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600;">Voltar ao Indomável Studio</a></p>
            <script>
              try { if (window.opener) { window.opener.location.reload(); } } catch(e){}
              setTimeout(() => { window.close(); }, 4000);
            </script>
          </div>
        </body></html>
        """
    else:
        return f"""
        <html><body style="font-family:system-ui,sans-serif;background:#0d1117;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
          <div style="background:#161b22;padding:32px;border-radius:12px;max-width:500px;text-align:center;border:1px solid #30363d;">
            <h2 style="color:#ff4757;margin-top:0;">⚠️ Falha ao Conectar Token</h2>
            <p style="color:#8b949e;">{msg}</p>
            <p style="margin-top:24px;"><a href="/" style="background:#30363d;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;">Voltar ao Indomável</a></p>
          </div>
        </body></html>
        """, 500


@app.post("/api/drive/conectar-codigo")
def conectar_codigo_manual():
    dados = request.get_json(silent=True) or {}
    code = dados.get("codigo") or dados.get("code")
    if not code:
        return _erro("Código não fornecido", 400)
    if "code=" in code:
        match = re.search(r"[?&]code=([^&]+)", code)
        if match:
            code = urllib.parse.unquote(match.group(1))
    ok, msg = google_drive.trocar_codigo_por_token(code)
    if not ok:
        return _erro(msg, 400)
    return jsonify({"ok": True, "mensagem": msg, "status": google_drive.status_conexao()})


@app.get("/api/drive/subir-locais")
@app.post("/api/drive/subir-locais")
def subir_cortes_locais():
    cfg = cortador_automatico.carregar_config_cortes()
    pasta_id = request.args.get("pasta_id") or (cfg.get("drive") or {}).get("pasta_id")
    res = google_drive.subir_cortes_locais_pendentes(pasta_id_raiz=pasta_id)
    return jsonify(res)


@app.post("/api/cortes/<youtube_id>/disparar")
@app.post("/api/cortes_automaticos/<youtube_id>/disparar")
def disparar_cortes_manuais(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    info = acervo_local.ler(youtube_id, "info.json") or video_resumido(youtube_id)
    blocos = blocos_do_video(youtube_id)
    if not blocos:
        return _erro("Este vídeo ainda não possui blocos gerados", 400)
    try:
        frases = frases_do_video(youtube_id)
    except Exception:
        frases = None
    resultado = cortador_automatico.processar_blocos_automaticamente(
        fila=fila,
        youtube_id=youtube_id,
        info=info,
        blocos=blocos,
        frases=frases,
        exportar_crus=True,
        exportar_9x16=True,
        refazer=False,
    )
    return jsonify({"ok": True, "resultado": resultado})


@app.post("/api/cortes/<youtube_id>/refazer")
def refazer_cortes_video(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    info = acervo_local.ler(youtube_id, "info.json") or video_resumido(youtube_id)
    blocos = blocos_do_video(youtube_id)
    if not blocos:
        return _erro("Este vídeo ainda não possui blocos gerados", 400)
    try:
        frases = frases_do_video(youtube_id)
    except Exception:
        frases = None
    resultado = cortador_automatico.processar_blocos_automaticamente(
        fila=fila,
        youtube_id=youtube_id,
        info=info,
        blocos=blocos,
        frases=frases,
        exportar_crus=True,
        exportar_9x16=True,
        refazer=True,
    )
    return jsonify({"ok": True, "refazendo": True, "resultado": resultado})


@app.post("/api/cortes/<youtube_id>/limpar-cache")
def limpar_cache_video_cortes(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    bytes_liberados = cortador_automatico.liberar_cache_video(youtube_id)
    return jsonify({
        "ok": True,
        "youtube_id": youtube_id,
        "bytes_liberados": bytes_liberados,
        "mb_liberados": round(bytes_liberados / (1024 * 1024), 2),
    })


@app.post("/api/cortes/limpar-cache-geral")
def limpar_cache_geral_cortes():
    bytes_liberados = cortador_automatico.limpar_cache_cortes_concluidos()
    return jsonify({
        "ok": True,
        "bytes_liberados": bytes_liberados,
        "mb_liberados": round(bytes_liberados / (1024 * 1024), 2),
    })


@app.post("/api/videos/<youtube_id>/exportar-lote")
def exportar_lote(youtube_id):
    if not ID_YOUTUBE.match(youtube_id):
        return _erro("id de vídeo inválido", 400)
    dados = request.get_json(silent=True) or {}
    blocos_do_chub = blocos_do_video(youtube_id)
    if not blocos_do_chub:
        return _erro("Este vídeo ainda não tem blocos disponíveis no Chub.", 409)
    formato = _ler_formato(dados)
    estilo = render.normalizar_estilo(dados.get("estilo"))
    com_legenda = bool(dados.get("com_legenda", True))
    com_headline = bool(dados.get("com_headline", True))
    com_marca = bool(dados.get("com_marca", True))
    manter_bruto = bool(dados.get("manter_bruto", False))
    video = video_resumido(youtube_id)
    tarefa = fila.adicionar_exportacao_lote(
        youtube_id, video["titulo"], blocos_do_chub, formato, estilo,
        com_legenda=com_legenda, com_headline=com_headline, com_marca=com_marca, manter_bruto=manter_bruto,
    )
    return jsonify({"tarefa": tarefa}), 202


@app.get("/api/trechos/<ident>")
def ver_trecho(ident):
    tarefa = fila.ver(ident)
    return jsonify({"tarefa": tarefa}) if tarefa else _erro("trecho não encontrado", 404)


def _arquivo_de_download(caminho):
    """Caminho absoluto se o arquivo ou pasta existe e está dentro de downloads ou output/cortes."""
    if not caminho:
        return None
    absoluto = os.path.abspath(caminho)
    if not os.path.exists(absoluto):
        return None
    norm_abs = os.path.normcase(absoluto)
    pastas_permitidas = (
        os.path.normcase(os.path.abspath(config.PASTA_DOWNLOADS)),
        os.path.normcase(os.path.abspath(config.PASTA_OUTPUT_CORTES)),
    )
    if not any(norm_abs.startswith(p) for p in pastas_permitidas):
        return None
    return absoluto


def _mostrar_no_explorer(caminho):
    if os.path.isdir(caminho):
        os.startfile(caminho)
    else:
        subprocess.Popen(f'explorer /select,"{caminho}"')


@app.post("/api/cortes/pasta")
def abrir_pasta_cortes():
    os.makedirs(config.PASTA_OUTPUT_CORTES, exist_ok=True)
    os.startfile(config.PASTA_OUTPUT_CORTES)
    return jsonify({"ok": True})


@app.post("/api/trechos/<ident>/mostrar")
def mostrar_na_pasta(ident):
    tarefa = fila.ver(ident)
    if not tarefa:
        return _erro("tarefa não encontrada", 404)
    caminho = _arquivo_de_download(tarefa.get("arquivo_cortes")) or _arquivo_de_download(tarefa.get("arquivo"))
    if not caminho:
        return _erro("arquivo não encontrado", 404)
    _mostrar_no_explorer(caminho)
    return jsonify({"ok": True})


@app.post("/api/trechos/<ident>/abrir")
def abrir_video_exportado(ident):
    tarefa = fila.ver(ident)
    if not tarefa:
        return _erro("tarefa não encontrada", 404)
    caminho = _arquivo_de_download(tarefa.get("arquivo_cortes")) or _arquivo_de_download(tarefa.get("arquivo"))
    if not caminho or not os.path.exists(caminho):
        return _erro("arquivo não encontrado", 404)
    try:
        os.startfile(caminho)
        return jsonify({"ok": True})
    except Exception as erro:
        return _erro(f"não foi possível abrir o arquivo: {erro}", 500)


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


# ---------- gravação e fatiamento de lives ----------

@app.get("/api/live/status")
def live_status():
    return jsonify(gravador_live.gravador.obter_status())


@app.post("/api/live/iniciar")
def live_iniciar():
    dados = request.get_json(silent=True) or {}
    url = str(dados.get("url", "")).strip()
    if not url:
        return _erro("informe a URL ou ID da transmissão ao vivo", 400)
    playlist_id = str(dados.get("playlist_id", "")).strip() or None
    dvr = bool(dados.get("dvr", True))
    duracao_chunk_s = int(dados.get("duracao_chunk_s", 1800))
    if duracao_chunk_s < 30 or duracao_chunk_s > 7200:
        return _erro("duração do bloco deve estar entre 30 segundos e 2 horas", 400)
    try:
        res = gravador_live.gravador.iniciar_gravacao(url, playlist_id=playlist_id, dvr=dvr, duracao_chunk_s=duracao_chunk_s)
        return jsonify({"ok": True, "sessao": res}), 202
    except Exception as erro:
        return _erro(str(erro), 400)


@app.post("/api/live/parar")
def live_parar():
    dados = request.get_json(silent=True) or {}
    sessao_id = dados.get("sessao_id")
    res = gravador_live.gravador.parar_gravacao(sessao_id)
    return jsonify({"ok": True, "resultado": res})


@app.post("/api/live/partes/<int:parte_id>/reprocessar")
def live_reprocessar_parte(parte_id):
    try:
        res = gravador_live.gravador.reprocessar_parte(parte_id)
        return jsonify(res)
    except Exception as erro:
        return _erro(str(erro), 400)


@app.post("/api/live/partes/<int:parte_id>/abrir")
def live_abrir_parte(parte_id):
    with gravador_live._conectar() as conn:
        row = conn.execute("SELECT * FROM partes_live WHERE id = ?", (parte_id,)).fetchone()
        if not row:
            return _erro("parte não encontrada", 404)
        caminho = row["caminho_video"]
        if not os.path.exists(caminho):
            pasta = os.path.dirname(caminho)
            if os.path.exists(pasta):
                caminho = pasta
            else:
                return _erro("arquivo de vídeo já foi limpo pela retenção", 404)
        _mostrar_no_explorer(caminho)
        return jsonify({"ok": True})


@app.post("/api/live/login-youtube")
def live_login_youtube():
    try:
        res = gravador_live.gravador.abrir_navegador_login()
        return jsonify(res)
    except Exception as erro:
        return _erro(str(erro), 500)


@app.post("/api/live/limpar-antigos")
def live_limpar_antigos():
    res = gravador_live.gravador.limpar_retencao_48h()
    return jsonify({"ok": True, "resultado": res})


def main():
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"Indomavel em http://127.0.0.1:{config.PORTA}")
    app.run(host="127.0.0.1", port=config.PORTA, debug=False, threaded=True)


if __name__ == "__main__":
    main()
