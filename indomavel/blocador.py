"""Divide uma transcrição em blocos no formato do Chub, com o Gemini.

Serve para vídeos que o Chub não acompanha. O padrão (fronteiras, regiões
ignoradas, campos e tom) foi copiado dos blocos reais do Chub, e
scripts/regua_blocos.py mede a diferença contra ele.
"""

import re

from . import gemini
from .chub import DURACAO_SHORT_S, potencial

CATEGORIAS = [
    "Eleições e Política", "Segurança Pública", "Gestão Pública", "Corrupção", "Economia", "Costumes",
    "Política Internacional", "Agropecuária e Tecnologia", "Infraestrutura", "Liberdade de Expressão",
    "Educação", "Saúde", "Outro",
]
RISCOS = ["juridico_sensivel", "linguagem_ofensiva", "ataque_pessoal", "violencia", "dado_a_conferir"]
JANELA_FRASES = 320
MINIMO_S = 15.0

INSTRUCAO = f"""Você divide transcrições de vídeos do Renan Santos (MBL, Partido Missão) em blocos editoriais, no mesmo padrão do acervo do Campaign Hub. Editores usam os blocos para achar cortes para redes sociais.

Cada linha da transcrição é uma frase no formato "[índice] (mm:ss) texto". ">>" no começo marca troca de locutor detectada pela legenda automática; não diz quem fala. A legenda é automática e tem erros de reconhecimento.

FRONTEIRAS
- Bloco é um trecho contínuo com um assunto ou tese e o seu desenvolvimento completo. Mude de bloco quando mudar o assunto, a tese ou a pergunta respondida.
- Blocos têm no mínimo 15 segundos. No acervo, metade dos blocos tem até 90 segundos e, em lives e conversas, a mediana fica perto de 55 segundos; poucos passam de 5 minutos. Divida sempre que a conversa mudar de assunto, de pergunta ou de pessoa em foco, desde que o trecho tenha ideia própria. Só mantenha um bloco longo quando for um único argumento contínuo.
- O bloco começa na frase que abre a ideia (inclua a pergunta que a provoca quando ela for necessária para entender) e termina na frase que conclui a ideia.
- O que não tem conteúdo editorial vira região ignorada, com o motivo: conversa casual, ajustes técnicos, saudações, pedidos de like, doação ou divulgação, despedidas, leitura de chat sem tese, passagem de fala, música.
- Blocos e regiões ignoradas não se sobrepõem e seguem a ordem das frases. Use somente índices que aparecem na transcrição enviada.

CAMPOS DE CADA BLOCO
- titulo: a tese do bloco numa frase afirmativa, sem clickbait. Ex.: "Renan propõe transformar cada eleitor em multiplicador para chegar ao 2º turno".
- resumo: 2 ou 3 frases fiéis ao que é dito, em terceira pessoa.
- categoria: exatamente uma de: {", ".join(CATEGORIAS)}.
- temas: 2 a 5 temas curtos.
- renan_falando: true somente quando o próprio texto deixa claro que a voz dominante do bloco é do Renan: alguém se dirige a ele pelo nome e ele responde, ele se identifica, ou o vídeo é ele falando sozinho sem outro participante. Em lives, entrevistas e comícios com mais de uma voz, use false se isso não estiver explícito. No acervo, só cerca de metade dos blocos tem true.
- nota_locutores: uma frase sobre quem parece falar e o que sustenta ou não essa atribuição.
- precisa_contexto: true quando o bloco depende de algo de fora dele: pessoas ou fatos citados sem apresentação ("ele", "aquele caso", "o que falamos antes"), notícias recentes pressupostas, a pergunta que veio antes. Em lives e conversas isso é o mais comum (cerca de 2 em cada 3 blocos do acervo). false só quando qualquer pessoa entende o bloco sozinho.
- autossuficiencia (0 a 100): o quanto o bloco se sustenta sozinho. motivo_autossuficiencia: uma frase explicando.
- densidade (0 a 100): o quanto o bloco concentra falas fortes e aproveitáveis para corte.
- pergunta: a pergunta que o bloco responde.
- cortes_possiveis: quantos cortes curtos (20 a 90 segundos) e independentes cabem no bloco, de 0 a 5.
- riscos: zero ou mais de: {", ".join(RISCOS)}.
- destaques: 1 a 5 frases mais fortes do bloco, com o índice da frase e o motivo editorial.

EXEMPLO DE CAMPOS (bloco real do acervo, comício em Joinville):
titulo: "Coragem como exercício para construir a Missão e honrar os filhos"
resumo: "O orador afirma que a coragem não era uma característica natural sua, mas foi desenvolvida por meio do exercício e da repetição, como nas lutas. Ele convoca os apoiadores a assumir riscos na construção da Missão, que ainda é pequena e enfrenta forças maiores, relacionando essa postura ao exemplo dado aos filhos e às filhas."
pergunta: "Como a coragem deve ser exercitada para construir a Missão e honrar os filhos?"
destaque: "Eu preciso de vocês corajosos." -> "Transforma a reflexão em convocação direta aos apoiadores para agir com coragem."
"""

_INTERVALO = {
    "type": "object",
    "properties": {
        "frase_inicial": {"type": "integer"},
        "frase_final": {"type": "integer"},
        "motivo": {"type": "string"},
    },
    "required": ["frase_inicial", "frase_final", "motivo"],
}

ESQUEMA = {
    "type": "object",
    "properties": {
        "blocos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "frase_inicial": {"type": "integer"},
                    "frase_final": {"type": "integer"},
                    "titulo": {"type": "string"},
                    "resumo": {"type": "string"},
                    "categoria": {"type": "string", "enum": CATEGORIAS},
                    "temas": {"type": "array", "items": {"type": "string"}},
                    "renan_falando": {"type": "boolean"},
                    "nota_locutores": {"type": "string"},
                    "precisa_contexto": {"type": "boolean"},
                    "autossuficiencia": {"type": "integer"},
                    "motivo_autossuficiencia": {"type": "string"},
                    "densidade": {"type": "integer"},
                    "pergunta": {"type": "string"},
                    "cortes_possiveis": {"type": "integer"},
                    "riscos": {"type": "array", "items": {"type": "string", "enum": RISCOS}},
                    "destaques": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"frase": {"type": "integer"}, "motivo": {"type": "string"}},
                            "required": ["frase", "motivo"],
                        },
                    },
                },
                "required": [
                    "frase_inicial", "frase_final", "titulo", "resumo", "categoria", "temas", "renan_falando",
                    "nota_locutores", "precisa_contexto", "autossuficiencia", "motivo_autossuficiencia", "densidade",
                    "pergunta", "cortes_possiveis", "riscos", "destaques",
                ],
            },
        },
        "ignorados": {"type": "array", "items": _INTERVALO},
    },
    "required": ["blocos", "ignorados"],
}


def _mmss(segundos):
    total = int(segundos)
    horas, resto = divmod(total, 3600)
    return f"{horas}:{resto // 60:02d}:{resto % 60:02d}" if horas else f"{resto // 60:02d}:{resto % 60:02d}"


def linhas_da_transcricao(frases):
    return "\n".join(
        f"[{frase['i']}] ({_mmss(frase['inicio'])}) {'>> ' if frase.get('troca') else ''}{frase['texto']}"
        for frase in frases
    )


def _limitar(valor, minimo, maximo):
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return minimo


def _validos(itens, primeiro, ultimo):
    """Intervalos dentro da janela, em ordem e sem sobreposição."""
    bons = []
    for item in sorted(
        (i for i in itens if isinstance(i.get("frase_inicial"), int) and isinstance(i.get("frase_final"), int)),
        key=lambda i: i["frase_inicial"],
    ):
        inicio = max(item["frase_inicial"], primeiro)
        fim = min(item["frase_final"], ultimo)
        if bons and inicio <= bons[-1]["frase_final"]:
            inicio = bons[-1]["frase_final"] + 1
        if inicio > fim:
            continue
        bons.append({**item, "frase_inicial": inicio, "frase_final": fim})
    return bons


def dividir(frases, contexto, ao_progredir=None, janela=JANELA_FRASES, gerar=None):
    """Blocos e regiões ignoradas para frases {i, inicio, fim, texto, troca}. Devolve (blocos, ignorados, modelos)."""
    if not frases:
        return [], [], []
    gerar = gerar or gemini.gerar_json
    posicao_por_indice = {frase["i"]: posicao for posicao, frase in enumerate(frases)}
    total = len(frases)
    posicao = 0
    brutos, ignorados, modelos = [], [], []
    while posicao < total:
        fatia = frases[posicao:posicao + janela]
        ultima = posicao + janela >= total
        primeiro, ultimo = fatia[0]["i"], fatia[-1]["i"]
        conteudo = (
            f"{contexto}\n\nTRANSCRIÇÃO (frases {primeiro} a {ultimo}"
            f"{'' if ultima else '; o vídeo continua depois da última frase'}):\n{linhas_da_transcricao(fatia)}"
        )
        dados, modelo = gerar(INSTRUCAO, conteudo, ESQUEMA)
        if modelo not in modelos:
            modelos.append(modelo)
        novos = _validos(dados.get("blocos") or [], primeiro, ultimo)
        novos_ignorados = _validos(dados.get("ignorados") or [], primeiro, ultimo)
        if ultima:
            proxima = total
        elif len(novos) > 1:
            # O último bloco pode ter sido cortado pelo fim da janela: ele é refeito na janela seguinte.
            corte = novos.pop()["frase_inicial"]
            novos_ignorados = [r for r in novos_ignorados if r["frase_final"] < corte]
            proxima = posicao_por_indice[corte]
        elif novos:
            proxima = posicao_por_indice[novos[-1]["frase_final"]] + 1
        else:
            proxima = posicao + janela
        brutos.extend(novos)
        ignorados.extend(novos_ignorados)
        posicao = max(proxima, posicao + 1)
        if ao_progredir:
            ao_progredir(min(posicao, total) / total)
    return _finalizar(brutos, ignorados, frases) + (modelos,)


def _finalizar(brutos, ignorados, frases):
    por_indice = {frase["i"]: frase for frase in frases}
    primeiro, ultimo = frases[0]["i"], frases[-1]["i"]
    blocos, curtos = [], []
    for bruto in _validos(brutos, primeiro, ultimo):
        inicial, final = por_indice[bruto["frase_inicial"]], por_indice[bruto["frase_final"]]
        duracao = round(final["fim"] - inicial["inicio"], 2)
        if duracao < MINIMO_S:
            # O Chub não tem nenhum bloco abaixo de 15 s; um trecho assim vira região ignorada.
            curtos.append({"frase_inicial": bruto["frase_inicial"], "frase_final": bruto["frase_final"],
                           "motivo": "Trecho curto demais para ser bloco."})
            continue
        blocos.append(_bloco(bruto, por_indice, inicial, final, duracao, len(blocos)))
    ocupados = [(b["frase_inicial"], b["frase_final"]) for b in blocos]
    regioes = []
    for regiao in _validos(ignorados + curtos, primeiro, ultimo):
        if any(regiao["frase_inicial"] <= fim and regiao["frase_final"] >= inicio for inicio, fim in ocupados):
            continue
        regioes.append({
            "frase_inicial": regiao["frase_inicial"],
            "frase_final": regiao["frase_final"],
            "inicio": por_indice[regiao["frase_inicial"]]["inicio"],
            "fim": por_indice[regiao["frase_final"]]["fim"],
            "motivo": regiao.get("motivo", ""),
        })
    return blocos, regioes


def _bloco(bruto, por_indice, inicial, final, duracao, ordem):
    forca = _limitar(bruto.get("densidade"), 0, 100)
    autonomia = _limitar(bruto.get("autossuficiencia"), 0, 100)
    cortes = _limitar(bruto.get("cortes_possiveis"), 0, 5)
    renan = bool(bruto.get("renan_falando"))
    precisa_contexto = bool(bruto.get("precisa_contexto"))
    destaques = []
    for destaque in bruto.get("destaques") or []:
        frase = por_indice.get(destaque.get("frase"))
        if frase and bruto["frase_inicial"] <= frase["i"] <= bruto["frase_final"]:
            destaques.append({"inicio": frase["inicio"], "fim": frase["fim"], "texto": frase["texto"],
                              "motivo": destaque.get("motivo", ""), "frase": frase["i"]})
    categoria = bruto.get("categoria") if bruto.get("categoria") in CATEGORIAS else "Outro"
    return {
        "id": f"local-{ordem}",
        "titulo": re.sub(r"\s+", " ", bruto.get("titulo") or "").strip(),
        "resumo": bruto.get("resumo") or "",
        "categoria": categoria,
        "temas": [t for t in bruto.get("temas") or [] if isinstance(t, str)][:5],
        "inicio": inicial["inicio"],
        "fim": final["fim"],
        "duracao": duracao,
        "renan_falando": renan,
        "precisa_contexto": precisa_contexto,
        "cortes_possiveis": cortes,
        "forca": forca,
        "autonomia": autonomia,
        "pergunta": bruto.get("pergunta") or "",
        "riscos": [r for r in bruto.get("riscos") or [] if r in RISCOS],
        "avisos": [],
        "nota_locutores": bruto.get("nota_locutores") or "",
        "motivo_autonomia": bruto.get("motivo_autossuficiencia") or "",
        "frase_inicial": bruto["frase_inicial"],
        "frase_final": bruto["frase_final"],
        "destaques": destaques,
        "potencial": potencial({"densityRank": forca, "selfContainedRank": autonomia, "possibleCuts": cortes}),
        "pronto": renan and not precisa_contexto and DURACAO_SHORT_S[0] <= duracao <= DURACAO_SHORT_S[1],
        "origem": "gemini",
    }
