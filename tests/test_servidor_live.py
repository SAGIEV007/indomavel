"""Testes dos endpoints da API de gravação e gerenciamento de lives."""

import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from indomavel import gravador_live, servidor


class TestServidorLive(unittest.TestCase):
    def setUp(self):
        servidor.app.config["TESTING"] = True
        self.cliente = servidor.app.test_client()

    def test_status_live_endpoint(self):
        with patch.object(gravador_live.gravador, "obter_status", return_value={"gravando": False, "sessao_ativa": None, "partes": []}):
            resp = self.cliente.get("/api/live/status")
            self.assertEqual(resp.status_code, 200)
            dados = resp.get_json()
            self.assertFalse(dados["gravando"])
            self.assertIsNone(dados["sessao_ativa"])

    def test_iniciar_live_sem_url_retorna_400(self):
        resp = self.cliente.post("/api/live/iniciar", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("informe a URL", resp.get_json()["erro"])

    def test_iniciar_live_duracao_invalida_retorna_400(self):
        resp = self.cliente.post("/api/live/iniciar", json={"url": "G9FJr1EX0wU", "duracao_chunk_s": 10})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("entre 30 segundos", resp.get_json()["erro"])

    def test_iniciar_live_com_sucesso(self):
        mock_res = {"sessao_id": 99, "youtube_id": "G9FJr1EX0wU", "titulo": "Live Teste", "pasta": "/temp"}
        with patch.object(gravador_live.gravador, "iniciar_gravacao", return_value=mock_res):
            resp = self.cliente.post("/api/live/iniciar", json={"url": "G9FJr1EX0wU", "dvr": True, "duracao_chunk_s": 1800})
            self.assertEqual(resp.status_code, 202)
            dados = resp.get_json()
            self.assertTrue(dados["ok"])
            self.assertEqual(dados["sessao"]["sessao_id"], 99)

    def test_parar_live_com_sucesso(self):
        with patch.object(gravador_live.gravador, "parar_gravacao", return_value={"sessao_id": 99, "status": "concluido"}):
            resp = self.cliente.post("/api/live/parar", json={"sessao_id": 99})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.get_json()["ok"])

    def test_limpar_antigos_endpoint(self):
        with patch.object(gravador_live.gravador, "limpar_retencao_48h", return_value={"removidos": 3, "bytes_liberados": 1024000}):
            resp = self.cliente.post("/api/live/limpar-antigos")
            self.assertEqual(resp.status_code, 200)
            dados = resp.get_json()
            self.assertTrue(dados["ok"])
            self.assertEqual(dados["resultado"]["removidos"], 3)

    def test_cortar_agora_endpoint(self):
        with patch.object(gravador_live.gravador, "cortar_agora", return_value={"ok": True, "mensagem": "Cortado"}):
            resp = self.cliente.post("/api/live/cortar-agora")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.get_json()["ok"])

    def test_salvar_config_endpoint(self):
        with patch.object(gravador_live.gravador, "salvar_config") as mock_save, \
             patch.object(gravador_live.gravador, "obter_status", return_value={"ok": True}):
            resp = self.cliente.post("/api/live/config", json={"url": "https://youtube.com/live/abc", "duracao_chunk_s": 1800})
            self.assertEqual(resp.status_code, 200)
            mock_save.assert_called_once()

    def test_logs_endpoint(self):
        with patch.object(gravador_live.gravador, "obter_logs", return_value=[{"msg": "test"}]):
            resp = self.cliente.get("/api/live/logs")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.get_json()["logs"]), 1)

    def test_cortar_parte_endpoint(self):
        with patch.object(gravador_live.gravador, "disparar_cortes_manuais", return_value={"ok": True, "mensagem": "Disparado"}):
            resp = self.cliente.post("/api/live/partes/1/cortar")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()

