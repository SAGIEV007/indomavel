"""Testes do sincronizador e dos endpoints da playlist de lives gravadas."""

import unittest
from unittest.mock import MagicMock, patch

from indomavel import servidor
from indomavel.sincronizador_playlist import extrair_id_playlist


class TestSincronizadorPlaylist(unittest.TestCase):
    def setUp(self):
        servidor.app.config["TESTING"] = True
        self.cliente = servidor.app.test_client()

    def test_extrair_id_playlist(self):
        self.assertEqual(extrair_id_playlist("PLCyyYEMeI6VE"), "PLCyyYEMeI6VE")
        self.assertEqual(
            extrair_id_playlist("https://youtube.com/playlist?list=PLCyyYEMeI6VE&si=NS3IQssBLSbmEU8g"),
            "PLCyyYEMeI6VE",
        )
        self.assertEqual(
            extrair_id_playlist("https://www.youtube.com/watch?v=abc&list=PL1234567890"),
            "PL1234567890",
        )

    def test_listar_playlist_endpoint(self):
        resp = self.cliente.get("/api/playlist")
        self.assertEqual(resp.status_code, 200)
        dados = resp.get_json()
        self.assertIn("videos", dados)
        self.assertIn("status", dados)
        self.assertIsInstance(dados["videos"], list)
        self.assertEqual(dados["status"]["playlist_id"], "PLCyyYEMeI6VE")

    def test_status_playlist_endpoint(self):
        resp = self.cliente.get("/api/playlist/status")
        self.assertEqual(resp.status_code, 200)
        dados = resp.get_json()
        self.assertEqual(dados["playlist_id"], "PLCyyYEMeI6VE")
        self.assertIn("ultima_checagem", dados)

    def test_sincronizar_agora_endpoint(self):
        with patch.object(servidor.sincronizador, "sincronizar_agora", return_value={"ok": True, "mensagem": "Iniciado"}):
            resp = self.cliente.post("/api/playlist/sincronizar")
            self.assertEqual(resp.status_code, 200)
            dados = resp.get_json()
            self.assertTrue(dados["ok"])


if __name__ == "__main__":
    unittest.main()
