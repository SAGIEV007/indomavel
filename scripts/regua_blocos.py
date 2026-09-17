"""Régua dos blocos: compara a divisão do Indomável (Gemini) com a do Chub nos mesmos vídeos.

Uso: .venv\\Scripts\\python.exe scripts\\regua_blocos.py [id_youtube ...]

Usa as frases do próprio Chub, para medir só a divisão em blocos. Salva o resultado
em relatorios\\regua_blocos_<data>.md e .json. O vídeo de Joinville (a75_NxjOnDE)
fica fora do padrão porque o exemplo do prompt saiu dele.
"""

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indomavel import blocador, config  # noqa: E402
from indomavel.chub import Chub  # noqa: E402
from indomavel.gemini import GeminiErro  # noqa: E402

VIDEOS_PADRAO = ["DxN0m8JN94w", "Eu8t2aEOLfo", "M4RmnqK4vQY"]
TOLERANCIA_FRASES = 2


def casamentos(origem, alvo, tolerancia=TOLERANCIA_FRASES):
    """Quantos inícios de `origem` têm um início de `alvo` a até `tolerancia` frases (cada um usado uma vez)."""
    livres = sorted(alvo)
    acertos = 0
    for inicio in sorted(origem):
        if not livres:
            break
        mais_perto = min(livres, key=lambda outro: abs(outro - inicio))
        if abs(mais_perto - inicio) <= tolerancia:
            livres.remove(mais_perto)
            acertos += 1
    return acertos


def sobreposicao(a, b):
    comum = min(a["frase_final"], b["frase_final"]) - max(a["frase_inicial"], b["frase_inicial"]) + 1
    total = max(a["frase_final"], b["frase_final"]) - min(a["frase_inicial"], b["frase_inicial"]) + 1
    return max(0, comum) / total


def comparar(blocos_chub, nossos):
    inicios_chub = [b["frase_inicial"] for b in blocos_chub]
    inicios_nossos = [b["frase_inicial"] for b in nossos]
    precisao = casamentos(inicios_nossos, inicios_chub) / len(nossos) if nossos else 0.0
    revocacao = casamentos(inicios_chub, inicios_nossos) / len(blocos_chub) if blocos_chub else 0.0
    f1 = 2 * precisao * revocacao / (precisao + revocacao) if precisao + revocacao else 0.0
    pares = []
    for do_chub in blocos_chub:
        melhor = max(nossos, key=lambda nosso: sobreposicao(do_chub, nosso), default=None)
        if melhor and sobreposicao(do_chub, melhor) >= 0.5:
            pares.append((do_chub, melhor))

    def concordancia(campo):
        return round(sum(c[campo] == n[campo] for c, n in pares) / len(pares), 3) if pares else None

    def mediana(blocos):
        return round(statistics.median(b["duracao"] for b in blocos), 1) if blocos else None

    return {
        "blocos_chub": len(blocos_chub),
        "blocos_nossos": len(nossos),
        "precisao_inicios": round(precisao, 3),
        "revocacao_inicios": round(revocacao, 3),
        "f1_inicios": round(f1, 3),
        "blocos_do_chub_com_par": len(pares),
        "concordancia_renan_falando": concordancia("renan_falando"),
        "concordancia_precisa_contexto": concordancia("precisa_contexto"),
        "duracao_mediana_chub_s": mediana(blocos_chub),
        "duracao_mediana_nossa_s": mediana(nossos),
        "prontos_chub": sum(b["pronto"] for b in blocos_chub),
        "prontos_nossos": sum(b["pronto"] for b in nossos),
    }


def _mmss(segundos):
    total = int(segundos)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def relatorio_md(resultados, carimbo):
    linhas = [f"# Régua dos blocos — {carimbo}", "",
              f"Início de bloco conta como acerto quando fica a até {TOLERANCIA_FRASES} frases de um início do Chub.", "",
              "| Vídeo | Blocos Chub | Blocos nossos | Precisão | Revocação | F1 | Renan falando igual | Precisa contexto igual | Mediana Chub | Mediana nossa | Modelos | Tempo |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |"]
    for item in resultados:
        r = item["resumo"]
        if "erro" in r:
            linhas.append(f"| {r['titulo'][:40]} | erro: {r['erro'][:80]} |")
            continue
        linhas.append(
            f"| {r['titulo'][:40]} | {r['blocos_chub']} | {r['blocos_nossos']} | {r['precisao_inicios']:.0%} | "
            f"{r['revocacao_inicios']:.0%} | {r['f1_inicios']:.0%} | {r['concordancia_renan_falando']} | "
            f"{r['concordancia_precisa_contexto']} | {r['duracao_mediana_chub_s']}s | {r['duracao_mediana_nossa_s']}s | "
            f"{', '.join(r['modelos'])} | {r['segundos']}s |"
        )
    for item in resultados:
        if "erro" in item["resumo"]:
            continue
        linhas += ["", f"## {item['resumo']['titulo']}", "", "| Chub | Indomável |", "| --- | --- |"]
        chub_linhas = [f"{_mmss(b['inicio'])} · {b['titulo']}" for b in item["chub"]]
        nossas_linhas = [f"{_mmss(b['inicio'])} · {b['titulo']}" for b in item["nossos"]]
        for indice in range(max(len(chub_linhas), len(nossas_linhas))):
            esquerda = chub_linhas[indice] if indice < len(chub_linhas) else ""
            direita = nossas_linhas[indice] if indice < len(nossas_linhas) else ""
            linhas.append(f"| {esquerda.replace('|', '/')} | {direita.replace('|', '/')} |")
    return "\n".join(linhas) + "\n"


def main():
    ids = sys.argv[1:] or VIDEOS_PADRAO
    chub = Chub()
    resultados = []
    for youtube_id in ids:
        transcricao = chub.transcricao(youtube_id)
        frases = transcricao["frases"]
        titulo = transcricao["video"]["titulo"] or youtube_id
        blocos_chub = chub.blocos(youtube_id)
        print(f"{titulo}: {len(frases)} frases, {len(blocos_chub)} blocos no Chub", flush=True)
        inicio = time.time()
        try:
            nossos, _, modelos = blocador.dividir(
                frases, f"VÍDEO: {titulo}",
                ao_progredir=lambda p: print(f"   {p:.0%}", flush=True),
            )
        except GeminiErro as erro:
            print(f"   FALHOU: {erro}")
            resultados.append({"resumo": {"youtube_id": youtube_id, "titulo": titulo, "erro": str(erro)}, "nossos": [], "chub": blocos_chub})
            continue
        resumo = comparar(blocos_chub, nossos)
        resumo.update({"youtube_id": youtube_id, "titulo": titulo, "frases": len(frases),
                       "segundos": round(time.time() - inicio), "modelos": modelos})
        print("   " + json.dumps(resumo, ensure_ascii=False), flush=True)
        resultados.append({"resumo": resumo, "nossos": nossos, "chub": blocos_chub})

    os.makedirs(config.PASTA_RELATORIOS, exist_ok=True)
    carimbo = time.strftime("%Y-%m-%d_%H%M")
    base = os.path.join(config.PASTA_RELATORIOS, f"regua_blocos_{carimbo}")
    with open(base + ".json", "w", encoding="utf-8") as saida:
        json.dump(resultados, saida, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as saida:
        saida.write(relatorio_md(resultados, carimbo))
    print("Relatório:", base + ".md")
    return 0 if all("erro" not in item["resumo"] for item in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
