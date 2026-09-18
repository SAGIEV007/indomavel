"""Divisão em blocos com um Gemini falso: janelas, bordas e formato de saída."""

import re
import unittest

from indomavel import blocador


def frases(quantidade, passo=2.0):
    return [{"i": i, "inicio": i * passo, "fim": (i + 1) * passo, "texto": f"frase {i}.", "troca": False}
            for i in range(quantidade)]


def bloco(inicial, final):
    return {
        "frase_inicial": inicial, "frase_final": final, "titulo": f"bloco {inicial}", "resumo": "resumo",
        "categoria": "Economia", "temas": ["tema"], "renan_falando": True, "nota_locutores": "", "precisa_contexto": False,
        "autossuficiencia": 80, "motivo_autossuficiencia": "", "densidade": 70, "pergunta": "", "cortes_possiveis": 2,
        "riscos": [], "destaques": [{"frase": inicial, "motivo": "forte"}],
    }


class GeminiFalso:
    def __init__(self, respostas):
        self.respostas = respostas
        self.janelas = []

    def __call__(self, instrucao, conteudo, esquema):
        primeiro, ultimo = map(int, re.search(r"frases (\d+) a (\d+)", conteudo).groups())
        self.janelas.append((primeiro, ultimo))
        return {"blocos": [bloco(a, b) for a, b in self.respostas[primeiro]], "ignorados": []}, "modelo-falso"


class TestBlocador(unittest.TestCase):
    def test_bloco_cortado_pela_janela_e_refeito_na_seguinte(self):
        falso = GeminiFalso({
            0: [(0, 99), (100, 249), (250, 319)],
            250: [(250, 400), (401, 560), (561, 569)],
            561: [(561, 699)],
        })
        blocos, _, modelos = blocador.dividir(frases(700), "contexto", janela=320, gerar=falso)
        self.assertEqual(falso.janelas, [(0, 319), (250, 569), (561, 699)])
        self.assertEqual([(b["frase_inicial"], b["frase_final"]) for b in blocos],
                         [(0, 99), (100, 249), (250, 400), (401, 560), (561, 699)])
        self.assertEqual(modelos, ["modelo-falso"])

    def test_bloco_curto_vira_regiao_ignorada_e_bordas_sao_consertadas(self):
        falso = GeminiFalso({0: [(0, 3), (4, 60), (55, 80), (90, 500)]})
        blocos, ignorados, _ = blocador.dividir(frases(100), "contexto", gerar=falso)
        self.assertEqual([(b["frase_inicial"], b["frase_final"]) for b in blocos], [(4, 60), (61, 80), (90, 99)])
        self.assertEqual([(r["frase_inicial"], r["frase_final"]) for r in ignorados], [(0, 3)])

    def test_bloco_sai_no_formato_da_tela(self):
        falso = GeminiFalso({0: [(0, 20)]})
        blocos, _, _ = blocador.dividir(frases(30), "contexto", gerar=falso)
        final = blocos[0]
        for campo in ("titulo", "resumo", "inicio", "fim", "duracao", "renan_falando", "precisa_contexto",
                      "cortes_possiveis", "destaques", "potencial", "pronto", "origem"):
            self.assertIn(campo, final)
        self.assertEqual(final["duracao"], 42.0)
        self.assertTrue(final["pronto"])
        self.assertEqual(final["destaques"][0]["texto"], "frase 0.")
        self.assertEqual(final["origem"], "gemini")

    def test_fallback_quando_gemini_falha(self):
        def falhador(instrucao, conteudo, esquema):
            raise RuntimeError("Gemini 403 PERMISSION_DENIED")

        blocos, ignorados, modelos = blocador.dividir(frases(60, passo=2.0), "contexto", gerar=falhador)
        self.assertGreater(len(blocos), 0)
        self.assertEqual(modelos, ["heuristicas_locais"])
        self.assertTrue(blocos[0]["renan_falando"])
        self.assertGreater(blocos[0]["duracao"], 15.0)


if __name__ == "__main__":
    unittest.main()
