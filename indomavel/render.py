"""Exporta o corte pronto para publicar: formato, card com etiqueta e headline, legenda palavra por palavra e rodapé.

A parte fixa (card e rodapé) vira uma imagem PNG do tamanho do vídeo. A tela mostra
essa mesma imagem por cima do player (/api/moldura.png) e o ffmpeg a sobrepõe ao
exportar, para o preview e o arquivo final baterem.
"""

import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

from . import config, legenda, youtube

PASTA_FONTES = os.path.join(config.PASTA_WEB, "static", "fonts")
FORMATOS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}
FONTE_TAG = "Montserrat-Black.ttf"
FONTE_HEADLINE = "Montserrat-ExtraBold.ttf"
FONTE_RODAPE = "Montserrat-SemiBold.ttf"
FONTES_LEGENDA = {"bebas": "BebasNeue-Regular.ttf", "montserrat": "Montserrat-Black.ttf"}
RODAPE_PADRAO = "Propaganda eleitoral | Renan Santos | 68.455.718/0001-06 | Partido Missão"
COR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Medidas em pixels para o lado menor do vídeo com 1080 px; o layout multiplica pela escala do formato.
ESTILO_PADRAO = {
    "card": True,
    "tag": "EM ALTA!",
    "headline": "",
    "tamanho_tag": 60,
    "tamanho_headline": 44,
    "legenda": True,
    "fonte_legenda": "bebas",
    "tamanho_legenda": 104,
    "cor_legenda": "#FFFFFF",
    "cor_destaque": "#FFD60A",
    "destacar_palavra": True,
    "contorno": 7,
    "posicao_legenda": 0.72,
    "maiusculas": True,
    "max_palavras": 3,
    "rodape": True,
    "texto_rodape": RODAPE_PADRAO,
    "zoom": 1.0,
    "enquadramento_x": 0.0,
    "enquadramento_y": 0.0,
}
_LIMITES = {
    "tamanho_tag": (20, 140), "tamanho_headline": (20, 110), "tamanho_legenda": (30, 220), "contorno": (0, 20),
    "posicao_legenda": (0.05, 0.97), "max_palavras": (1, 6), "zoom": (1.0, 3.0),
    "enquadramento_x": (-1.0, 1.0), "enquadramento_y": (-1.0, 1.0),
}


def normalizar_estilo(dados):
    estilo = dict(ESTILO_PADRAO)
    for chave, valor in (dados or {}).items():
        if chave not in estilo:
            continue
        padrao = ESTILO_PADRAO[chave]
        try:
            if isinstance(padrao, bool):
                estilo[chave] = bool(valor)
            elif isinstance(padrao, int):
                estilo[chave] = int(round(float(valor)))
            elif isinstance(padrao, float):
                estilo[chave] = float(valor)
            else:
                estilo[chave] = str(valor)[:300]
        except (TypeError, ValueError):
            continue
    for chave, (minimo, maximo) in _LIMITES.items():
        estilo[chave] = max(minimo, min(maximo, estilo[chave]))
    for chave in ("cor_legenda", "cor_destaque"):
        if not COR.match(estilo[chave]):
            estilo[chave] = ESTILO_PADRAO[chave]
    if estilo["fonte_legenda"] not in FONTES_LEGENDA:
        estilo["fonte_legenda"] = ESTILO_PADRAO["fonte_legenda"]
    return estilo


def _fonte(arquivo, tamanho):
    return ImageFont.truetype(os.path.join(PASTA_FONTES, arquivo), max(1, int(tamanho)))


def quebrar_linhas(texto, arquivo_fonte, tamanho, largura_maxima, maximo_linhas=4):
    fonte = _fonte(arquivo_fonte, tamanho)
    linhas = []
    for paragrafo in texto.splitlines():
        atual = ""
        for palavra in paragrafo.split():
            candidata = f"{atual} {palavra}".strip()
            if atual and fonte.getlength(candidata) > largura_maxima:
                linhas.append(atual)
                atual = palavra
            else:
                atual = candidata
        if atual:
            linhas.append(atual)
    if len(linhas) > maximo_linhas:
        linhas = linhas[:maximo_linhas]
        linhas[-1] = linhas[-1].rstrip(".,;:") + "…"
    return linhas


def layout(formato, estilo):
    """Posições em pixels do vídeo final: card, área do vídeo, legenda e rodapé."""
    largura, altura = FORMATOS[formato]
    escala = min(largura, altura) / 1080
    margem_x, margem_topo, margem_base, espaco = (round(v * escala) for v in (44, 34, 30, 8))
    itens = []
    altura_card = 0
    tag = estilo["tag"].strip()
    headline = estilo["headline"].strip()
    if estilo["card"] and (tag or headline):
        y = margem_topo
        if tag:
            tamanho = round(estilo["tamanho_tag"] * escala)
            itens.append({"tipo": "tag", "texto": tag, "x": margem_x, "y": y, "tamanho": tamanho})
            y += round(tamanho * 1.15) + espaco
        if headline:
            tamanho = round(estilo["tamanho_headline"] * escala)
            for linha in quebrar_linhas(headline, FONTE_HEADLINE, tamanho, largura - 2 * margem_x):
                itens.append({"tipo": "headline", "texto": linha, "x": margem_x, "y": y, "tamanho": tamanho})
                y += round(tamanho * 1.24)
        altura_card = y + margem_base
    area_video = {"x": 0, "y": altura_card, "w": largura, "h": altura - altura_card}
    rodape = None
    if estilo["rodape"] and estilo["texto_rodape"].strip():
        tamanho = round(21 * escala)
        rodape = {"texto": estilo["texto_rodape"].strip(), "tamanho": tamanho, "y": altura - round(18 * escala) - tamanho}
    return {
        "formato": formato,
        "largura": largura,
        "altura": altura,
        "escala": escala,
        "card": {"altura": altura_card, "itens": itens},
        "video": area_video,
        "legenda": {
            "y": altura_card + round(area_video["h"] * estilo["posicao_legenda"]),
            "tamanho": round(estilo["tamanho_legenda"] * escala),
            "contorno": round(estilo["contorno"] * escala),
        },
        "rodape": rodape,
    }


def desenhar_moldura(dados_layout):
    """Imagem RGBA do tamanho do vídeo com o card no topo e o rodapé; o resto é transparente."""
    imagem = Image.new("RGBA", (dados_layout["largura"], dados_layout["altura"]), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    card = dados_layout["card"]
    if card["altura"]:
        desenho.rectangle([0, 0, dados_layout["largura"], card["altura"]], fill=(255, 255, 255, 255))
        for item in card["itens"]:
            arquivo = FONTE_TAG if item["tipo"] == "tag" else FONTE_HEADLINE
            cor = (0, 0, 0, 255) if item["tipo"] == "tag" else (17, 17, 17, 255)
            desenho.text((item["x"], item["y"]), item["texto"], font=_fonte(arquivo, item["tamanho"]), fill=cor, anchor="la")
    rodape = dados_layout["rodape"]
    if rodape:
        fonte = _fonte(FONTE_RODAPE, rodape["tamanho"])
        x = dados_layout["largura"] / 2
        desenho.text((x + 2, rodape["y"] + 2), rodape["texto"], font=fonte, fill=(0, 0, 0, 140), anchor="ma")
        desenho.text((x, rodape["y"]), rodape["texto"], font=fonte, fill=(255, 255, 255, 235), anchor="ma")
    return imagem


def moldura_png(formato, estilo):
    saida = io.BytesIO()
    desenhar_moldura(layout(formato, estilo)).save(saida, "PNG", optimize=True)
    return saida.getvalue()


def _tempo_ass(segundos):
    centesimos = int(round(max(0.0, segundos) * 100))
    horas, centesimos = divmod(centesimos, 360000)
    minutos, centesimos = divmod(centesimos, 6000)
    segs, centesimos = divmod(centesimos, 100)
    return f"{horas}:{minutos:02d}:{segs:02d}.{centesimos:02d}"


def _cor_ass(cor):
    return f"&H{cor[5:7]}{cor[3:5]}{cor[1:3]}&".upper()


def _texto_ass(texto):
    return texto.replace("\\", "").replace("{", "(").replace("}", ")")


def escrever_ass(caminho, dados_layout, estilo, trechos, inicio_corte, duracao):
    arquivo_fonte = FONTES_LEGENDA[estilo["fonte_legenda"]]
    fonte = _fonte(arquivo_fonte, 1000)
    familia = fonte.getname()[0]
    subida, descida = fonte.getmetrics()
    # O tamanho no ASS é a altura da célula da fonte; a tela usa o tamanho em "em". Converte para os dois baterem.
    tamanho_ass = round(dados_layout["legenda"]["tamanho"] * (subida + descida) / 1000)
    cor = _cor_ass(estilo["cor_legenda"])
    cor_destaque = _cor_ass(estilo["cor_destaque"])
    contorno = dados_layout["legenda"]["contorno"]
    x, y = dados_layout["largura"] // 2, dados_layout["legenda"]["y"]
    linhas = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {dados_layout['largura']}", f"PlayResY: {dados_layout['altura']}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Legenda,{familia},{tamanho_ass},{cor},{cor},&H000000&,&H80000000&,0,0,0,0,100,100,0,0,1,{contorno},"
        f"{max(1, contorno // 3)},5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for trecho in trechos:
        a = trecho["inicio"] - inicio_corte
        b = min(duracao, trecho["fim"] - inicio_corte)
        if b - max(a, 0) < 0.05:
            continue
        partes = []
        for indice, palavra in enumerate(trecho["palavras"]):
            texto = _texto_ass(legenda.limpar_palavra(palavra))
            if estilo["maiusculas"]:
                texto = texto.upper()
            if not texto:
                continue
            if estilo["destacar_palavra"] and indice == trecho.get("destaque"):
                texto = "{\\c" + cor_destaque + "}" + texto + "{\\c" + cor + "}"
            partes.append(texto)
        if partes:
            linhas.append(f"Dialogue: 0,{_tempo_ass(a)},{_tempo_ass(b)},Legenda,,0,0,0,,{{\\an5\\pos({x},{y})}}{' '.join(partes)}")
    with open(caminho, "w", encoding="utf-8") as saida:
        saida.write("\n".join(linhas) + "\n")


def _sondar(caminho):
    ffprobe = os.path.join(os.path.dirname(config.FFMPEG), "ffprobe.exe") if config.FFMPEG else "ffprobe"
    resultado = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", caminho], capture_output=True, text=True, check=True,
    )
    info = json.loads(resultado.stdout)
    stream = info["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(info["format"]["duration"])


def filtro_ffmpeg(dados_layout, estilo, largura_fonte, altura_fonte, com_legenda):
    area = dados_layout["video"]
    escala = max(area["w"] / largura_fonte, area["h"] / altura_fonte) * estilo["zoom"]
    largura_escalada = max(area["w"], math.ceil(largura_fonte * escala / 2) * 2)
    altura_escalada = max(area["h"], math.ceil(altura_fonte * escala / 2) * 2)
    corte_x = round((largura_escalada - area["w"]) / 2 * (1 + estilo["enquadramento_x"]))
    corte_y = round((altura_escalada - area["h"]) / 2 * (1 + estilo["enquadramento_y"]))
    corte_x = max(0, min(largura_escalada - area["w"], corte_x))
    corte_y = max(0, min(altura_escalada - area["h"], corte_y))
    partes = [
        f"[0:v]scale={largura_escalada}:{altura_escalada},crop={area['w']}:{area['h']}:{corte_x}:{corte_y},setsar=1,"
        f"pad={dados_layout['largura']}:{dados_layout['altura']}:0:{area['y']}:white[base]",
        "[base][1:v]overlay=0:0:shortest=1" + ("[composto]" if com_legenda else "[saida]"),
    ]
    if com_legenda:
        partes.append("[composto]ass=legenda.ass:fontsdir=fontes[saida]")
    return ";".join(partes)


def exportar(youtube_id, inicio, fim, formato, estilo, trechos, pasta_saida, titulo, ao_progredir=None):
    """Baixa o trecho, compõe o vídeo final e devolve o caminho do MP4."""
    avisar = ao_progredir or (lambda mensagem: None)
    dados_layout = layout(formato, estilo)
    os.makedirs(pasta_saida, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="indomavel_") as temporaria:
        avisar("Baixando o trecho do YouTube")
        bruto = youtube.baixar_trecho(youtube_id, inicio, fim, temporaria, "trecho")
        largura_fonte, altura_fonte, duracao = _sondar(bruto)
        desenhar_moldura(dados_layout).save(os.path.join(temporaria, "moldura.png"))
        com_legenda = estilo["legenda"] and bool(trechos)
        if com_legenda:
            os.makedirs(os.path.join(temporaria, "fontes"))
            shutil.copy(os.path.join(PASTA_FONTES, FONTES_LEGENDA[estilo["fonte_legenda"]]), os.path.join(temporaria, "fontes"))
            escrever_ass(os.path.join(temporaria, "legenda.ass"), dados_layout, estilo, trechos, inicio, duracao)
        comando = [
            config.FFMPEG or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
            "-i", os.path.basename(bruto), "-loop", "1", "-i", "moldura.png",
            "-filter_complex", filtro_ffmpeg(dados_layout, estilo, largura_fonte, altura_fonte, com_legenda),
            "-map", "[saida]", "-map", "0:a?", "-t", f"{duracao:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "final.mp4",
        ]
        processo = subprocess.Popen(comando, cwd=temporaria, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for linha in processo.stdout:
            if linha.startswith("out_time_us=") and linha.strip().split("=")[1].isdigit():
                feito = int(linha.strip().split("=")[1]) / 1_000_000
                avisar(f"Montando o vídeo… {min(100, 100 * feito / duracao):.0f}%")
        erros = processo.stderr.read()
        if processo.wait() != 0:
            raise RuntimeError("o ffmpeg falhou: " + erros[-600:])
        nome = f"{youtube.nome_seguro(titulo or youtube_id)} - {formato.replace(':', 'x')} - {youtube.marca_tempo(inicio)}.mp4"
        destino = os.path.join(pasta_saida, nome)
        shutil.move(os.path.join(temporaria, "final.mp4"), destino)
    return destino
