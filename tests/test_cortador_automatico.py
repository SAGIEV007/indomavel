"""Testes do cortador automático (cortes crus e 9:16) e endpoints de feedback."""

import unittest
from unittest.mock import MagicMock, patch

from indomavel import cortador_automatico, servidor


class TestCortadorAutomatico(unittest.TestCase):
    def setUp(self):
        servidor.app.config["TESTING"] = True
        self.cliente = servidor.app.test_client()

    def test_score_bloco(self):
        bloco_excelente = {
            "potencial": 85.0,
            "duracao": 45.0,
            "renan_falando": True,
            "precisa_contexto": False,
            "destaques": [{"texto": "frase de impacto"}],
        }
        score_bom = cortador_automatico.score_bloco(bloco_excelente)

        bloco_fraco = {
            "potencial": 40.0,
            "duracao": 180.0,
            "renan_falando": False,
            "precisa_contexto": True,
            "destaques": [],
        }
        score_ruim = cortador_automatico.score_bloco(bloco_fraco)

        self.assertGreater(score_bom, score_ruim)
        self.assertGreaterEqual(score_bom, 130.0)

    def test_selecionar_top_blocos(self):
        blocos = [
            {"id": "b1", "potencial": 90.0, "duracao": 50.0, "renan_falando": True, "precisa_contexto": False},
            {"id": "b2", "potencial": 40.0, "duracao": 300.0, "renan_falando": False, "precisa_contexto": True},
            {"id": "b3", "potencial": 80.0, "duracao": 40.0, "renan_falando": True, "precisa_contexto": False},
            {"id": "b4", "potencial": 70.0, "duracao": 60.0, "renan_falando": False, "precisa_contexto": False},
        ]
        top = cortador_automatico.selecionar_top_blocos(blocos, limite=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["id"], "b1")
        self.assertEqual(top[1]["id"], "b3")

    def test_processar_blocos_automaticamente(self):
        fila_mock = MagicMock()
        fila_mock.adicionar_blocos_crus.return_value = {"id": "tarefa_crus_1"}
        fila_mock.adicionar_exportacao_lote.return_value = {"id": "tarefa_lote_1"}

        blocos = [
            {"id": "b1", "titulo": "Momento Forte", "inicio": 10.0, "fim": 55.0, "duracao": 45.0,
             "potencial": 85.0, "renan_falando": True, "precisa_contexto": False},
        ]
        info = {"titulo": "Live Especial 24hrs"}

        with patch("indomavel.cortador_automatico.salvar_status_cortes"):
            with patch("indomavel.cortador_automatico.obter_status_cortes", return_value=None):
                with patch.object(cortador_automatico.executor_sequencial, "enfileirar", return_value={"youtube_id": "vid_teste01"}):
                    resultado = cortador_automatico.processar_blocos_automaticamente(
                    fila=fila_mock,
                    youtube_id="vid_teste01",
                    info=info,
                    blocos=blocos,
                    exportar_crus=True,
                    exportar_9x16=True,
                    max_9x16=2,
                )

                self.assertIsNotNone(resultado)
                self.assertIn("crus", resultado)
                self.assertIn("reels_9x16", resultado)
                self.assertEqual(resultado["crus"]["tarefa_id"], "tarefa_crus_1")
                self.assertEqual(resultado["reels_9x16"]["tarefa_id"], "tarefa_lote_1")
                fila_mock.adicionar_blocos_crus.assert_called_once()
                fila_mock.adicionar_exportacao_lote.assert_called_once()

    def test_endpoint_feedback_headline(self):
        resp = self.cliente.post("/api/headlines/feedback", json={
            "youtube_id": "vid_teste01",
            "bloco_id": "b1",
            "tag": "EM ALTA!",
            "headline": "Renan Santos rebate críticas em sabatina",
            "angulo": "confronto",
            "acao": "aprovado",
        })
        self.assertEqual(resp.status_code, 200)
        dados = resp.get_json()
        self.assertTrue(dados.get("ok"))
        self.assertEqual(dados["registro"]["tag"], "EM ALTA!")

    def test_endpoint_status_cortes_automaticos(self):
        resp = self.cliente.get("/api/cortes_automaticos/vid_teste01")
        self.assertEqual(resp.status_code, 200)
        dados = resp.get_json()
        self.assertIn("status", dados)


if __name__ == "__main__":
    unittest.main()
