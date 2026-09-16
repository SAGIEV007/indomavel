"""Configuração do Indomável: caminhos do projeto, valores do .env e programas externos."""

import glob
import os
import shutil

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ler_env(caminho):
    valores = {}
    if not os.path.exists(caminho):
        return valores
    with open(caminho, encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores


_ENV = _ler_env(os.path.join(RAIZ, ".env"))


def valor(chave, padrao=""):
    return os.environ.get(chave) or _ENV.get(chave) or padrao


def _achar_programa(nome, candidatos=()):
    achado = shutil.which(nome)
    if achado:
        return achado
    for padrao in candidatos:
        for caminho in glob.glob(os.path.expandvars(padrao)):
            if os.path.isfile(caminho):
                return caminho
    return None


CHUB_MCP_URL = valor("CHUB_MCP_URL")
GOOGLE_API_KEY = valor("GOOGLE_API_KEY")
PORTA = int(valor("PORTA", "5055"))
PASTA_DOWNLOADS = valor("PASTA_DOWNLOADS", os.path.join(RAIZ, "downloads"))
# Vídeos inteiros baixados na maior qualidade, uma vez só, para recortar todos os blocos sem voltar ao YouTube.
# Ficam no D: (PASTA_VIDEOS no .env) porque uma live de 3 h passa de 5 GB e o C: tem pouco espaço.
PASTA_VIDEOS = valor("PASTA_VIDEOS", os.path.join(RAIZ, "videos"))
PASTA_DADOS = valor("PASTA_DADOS", os.path.join(RAIZ, "dados"))
PASTA_RELATORIOS = os.path.join(RAIZ, "relatorios")
PASTA_WEB = os.path.join(RAIZ, "web")

# Em ordem de preferência. Os modelos 3.x do plano gratuito às vezes respondem 503 (alta demanda):
# nesse caso o programa tenta o próximo da lista.
GEMINI_MODELOS = [
    nome.strip()
    for nome in valor("GEMINI_MODELOS", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-flash-latest,gemini-2.5-flash").split(",")
    if nome.strip()
]
WHISPER_MODELO = valor("WHISPER_MODELO", "small")

FFMPEG = _achar_programa("ffmpeg", [
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe",
])
NODE = _achar_programa("node", [r"%LOCALAPPDATA%\hermes\node\node.exe"])
