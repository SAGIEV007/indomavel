"""Testes para o seletor de 3 ângulos de headline e fallback heurístico."""

import unittest
from indomavel import headlines


class TestHeadlinesAngulos(unittest.TestCase):
    def test_headlines_heuristicas_gera_tres_angulos(self):
        opcoes = headlines.headlines_heuristicas(
            titulo="Renan Santos desafia o STF e fala sobre liberdade",
            resumo="Renan critica decisões recentes da Suprema Corte",
            categoria="Política",
            temas=["STF", "Liberdade de Expressão"],
            destaques=["Não vamos aceitar que calem a nossa voz no Congresso"],
        )
        self.assertEqual(len(opcoes), 3)
        angulos = {o["angulo"] for o in opcoes}
        self.assertEqual(angulos, {"noticioso", "confronto", "citacao"})

        noticioso = next(o for o in opcoes if o["angulo"] == "noticioso")
        self.assertEqual(noticioso["tag"], "EM ALTA!")
        self.assertIn("Renan Santos", noticioso["headline"])

        confronto = next(o for o in opcoes if o["angulo"] == "confronto")
        self.assertEqual(confronto["tag"], "CONFRONTO!")
        self.assertIn("Renan Santos", confronto["headline"])

        citacao = next(o for o in opcoes if o["angulo"] == "citacao")
        self.assertEqual(citacao["tag"], "MANDOU A REAL!!")
        self.assertTrue(citacao["headline"].startswith("“"))

    def test_sugerir_fallback_quando_gemini_falha(self):
        def falhador(*args, **kwargs):
            raise RuntimeError("Gemini 503 Overloaded")

        resultado = headlines.sugerir(
            titulo="Debate Histórico com Renan Santos",
            resumo="Discussão sobre rumos do país",
            texto_trecho="Aqui está a fala completa",
            categoria="Eleições",
            temas=["Debate"],
            destaques=["A verdade vencerá"],
            gerar=falhador,
        )
        self.assertEqual(len(resultado["opcoes"]), 3)
        self.assertEqual(resultado["modelo"], "heuristicas_locais")
        angulos = [o["angulo"] for o in resultado["opcoes"]]
        self.assertIn("noticioso", angulos)
        self.assertIn("confronto", angulos)
        self.assertIn("citacao", angulos)


if __name__ == "__main__":
    unittest.main()
