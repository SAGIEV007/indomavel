"""Testes do banco de treinamento Few-Shot de headlines e active learning."""

import os
import tempfile
import unittest
from unittest.mock import patch

from indomavel import config, headlines


class TestHeadlinesTreinamento(unittest.TestCase):
    def test_carregar_banco_treinamento(self):
        banco = headlines.carregar_banco_treinamento()
        self.assertIsInstance(banco, list)
        self.assertGreaterEqual(len(banco), 5)
        primeiro = banco[0]
        self.assertIn("tag", primeiro)
        self.assertIn("headline", primeiro)

    def test_selecionar_exemplos_dinamicos_diversidade(self):
        exemplos = headlines.selecionar_exemplos_dinamicos(
            categoria="STF e Judiciário",
            temas=["moraes", "impeachment"],
            texto="Renan Santos discursa sobre ministros do STF",
            limite=5,
        )
        self.assertIsInstance(exemplos, list)
        self.assertGreater(len(exemplos), 0)
        # Cada item é uma tupla (tag, headline)
        for tag, headline in exemplos:
            self.assertIsInstance(tag, str)
            self.assertIsInstance(headline, str)

    def test_montar_instrucao(self):
        instrucao = headlines.montar_instrucao(
            categoria="Eleições e Política",
            temas=["Congresso", "Debate"],
            texto="Renan Santos comenta os bastidores da votação",
        )
        self.assertIn("Você escreve o card de topo de cortes do Renan Santos", instrucao)
        self.assertIn("Exemplos reais selecionados para este tema:", instrucao)

    def test_registrar_veredito(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, "PASTA_DADOS", tmpdir):
                registro = headlines.registrar_veredito(
                    youtube_id="teste_vid_123",
                    bloco_id="local-1",
                    headline="Renan Santos detona plano que prejudica trabalhadores",
                    tag="MANDOU A REAL!!",
                    angulo="confronto",
                    acao="aprovado",
                )
                self.assertEqual(registro["youtube_id"], "teste_vid_123")
                self.assertEqual(registro["tag"], "MANDOU A REAL!!")
                caminho = os.path.join(tmpdir, "automacao", "vereditos.jsonl")
                self.assertTrue(os.path.exists(caminho))
                with open(caminho, encoding="utf-8") as f:
                    conteudo = f.read()
                self.assertIn("Renan Santos detona plano", conteudo)


if __name__ == "__main__":
    unittest.main()
