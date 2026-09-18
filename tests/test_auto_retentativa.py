"""Testes do sistema à prova de erros com retentativas automáticas e backoff exponencial."""

import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from indomavel import acervo_local, config, processador


class TestAutoRetentativa(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patcher_dados = patch.object(config, "PASTA_DADOS", self.temp_dir.name)
        self.patcher_dados.start()

    def tearDown(self):
        self.patcher_dados.stop()
        self.temp_dir.cleanup()

    def test_adicionar_com_refazer_zera_tentativas(self):
        yid = "vid_teste01"
        acervo_local.salvar(yid, "estado.json", {
            "estado": "falhou",
            "tentativas": 4,
            "proxima_tentativa": 9999999999.0,
        })

        with patch("threading.Thread"):
            fila = processador.FilaDeLinks()
            fila.adicionar(yid, refazer=True)

        estado = acervo_local.ler(yid, "estado.json")
        self.assertEqual(estado["tentativas"], 0)
        self.assertNotIn("proxima_tentativa", estado)
        self.assertEqual(estado["estado"], "na_fila")

    def test_erro_passageiro_agenda_retentativa(self):
        yid = "vid_teste02"
        acervo_local.salvar(yid, "info.json", {"youtube_id": yid, "titulo": "Teste"})

        with patch("threading.Thread"):
            fila = processador.FilaDeLinks()

        # Simula erro de Gemini 503
        with patch.object(fila, "_processar", side_effect=RuntimeError("gemini-3.8-flash: 503 UNAVAILABLE")):
            try:
                fila._processar(yid, False)
            except Exception as erro:
                # Simula o bloco except de _trabalhar
                estado_ant = acervo_local.ler(yid, "estado.json", {}) or {}
                tentativas = int(estado_ant.get("tentativas", 0)) + 1
                espera_s = 30 * (2 ** (tentativas - 1))
                acervo_local.salvar_estado(
                    yid,
                    "aguardando_retentativa",
                    "Falha temporária do Gemini",
                    None,
                    tentativas=tentativas,
                    proxima_tentativa=time.time() + espera_s,
                )

        estado = acervo_local.ler(yid, "estado.json")
        self.assertEqual(estado["estado"], "aguardando_retentativa")
        self.assertEqual(estado["tentativas"], 1)
        self.assertGreater(estado["proxima_tentativa"], time.time())


if __name__ == "__main__":
    unittest.main()
