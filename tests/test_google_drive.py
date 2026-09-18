"""Testes automatizados para o conector Google Drive API v3 e organização por categoria."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from indomavel import config, google_drive


class TestGoogleDrive(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_gdrive_")
        self.pasta_drive_local = os.path.join(self.temp_dir, "google_drive")
        self.pasta_dados = os.path.join(self.temp_dir, "dados")
        os.makedirs(self.pasta_drive_local, exist_ok=True)
        os.makedirs(self.pasta_dados, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_identificar_categoria_arquivo(self):
        self.assertEqual(google_drive.identificar_categoria_arquivo("Fernando01_com_legenda.mp4"), "com_legenda")
        self.assertEqual(google_drive.identificar_categoria_arquivo("Fernando02_sem_legenda.mp4"), "sem_legenda")
        self.assertEqual(google_drive.identificar_categoria_arquivo("Fernando03_so_legenda.mp4"), "so_legenda")
        self.assertEqual(google_drive.identificar_categoria_arquivo("Fernando04_cru.mp4"), "cru")
        self.assertEqual(google_drive.identificar_categoria_arquivo("Fernando04_cru.srt"), "cru")
        self.assertEqual(google_drive.identificar_categoria_arquivo("Fernando05_headlines.txt"), "headlines")

    def test_status_conexao_fallback_local(self):
        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "localizar_arquivo_credenciais", return_value=None):
                status = google_drive.status_conexao()
                self.assertFalse(status["conectado"])
                self.assertEqual(status["modo"], "fallback_local")
                self.assertEqual(status["pasta_id"], google_drive.ID_PASTA_PADRAO)
                self.assertIn("fallback local", status["mensagem"].lower())

    def test_enviar_pacote_drive_fallback_local(self):
        pasta_corte = os.path.join(self.temp_dir, "Fernando01")
        os.makedirs(pasta_corte, exist_ok=True)

        # Cria arquivos simulados do corte
        with open(os.path.join(pasta_corte, "Fernando01_com_legenda.mp4"), "wb") as f:
            f.write(b"video_com_legenda")
        with open(os.path.join(pasta_corte, "Fernando01_so_legenda.mp4"), "wb") as f:
            f.write(b"video_so_legenda")
        with open(os.path.join(pasta_corte, "Fernando01_cru.mp4"), "wb") as f:
            f.write(b"video_cru")
        with open(os.path.join(pasta_corte, "Fernando01_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nTexto\n")
        with open(os.path.join(pasta_corte, "Fernando01_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("Headlines")

        variacoes = {
            "com_legenda": True,
            "sem_legenda": False,
            "so_legenda": True,
            "cru": True,
            "headlines": True,
        }

        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "obter_token_acesso", return_value=(None, "sem credenciais")):
                res = google_drive.enviar_pacote_drive(pasta_corte, "Fernando01", variacoes_ativas=variacoes)
                self.assertTrue(res["sucesso"])
                self.assertEqual(res["modo"], "fallback_local")

                # Verifica se organizou nas subpastas de categoria no destino local
                pasta_com = os.path.join(self.pasta_drive_local, "Com headline e legenda")
                pasta_so = os.path.join(self.pasta_drive_local, "Só legenda")
                pasta_cru = os.path.join(self.pasta_drive_local, "Cortes crus")
                pasta_hl = os.path.join(self.pasta_drive_local, "Headlines")
                pasta_sem = os.path.join(self.pasta_drive_local, "Com headline e sem legenda")

                self.assertTrue(os.path.exists(os.path.join(pasta_com, "Fernando01_com_legenda.mp4")))
                self.assertTrue(os.path.exists(os.path.join(pasta_so, "Fernando01_so_legenda.mp4")))
                self.assertTrue(os.path.exists(os.path.join(pasta_cru, "Fernando01_cru.mp4")))
                self.assertTrue(os.path.exists(os.path.join(pasta_cru, "Fernando01_cru.srt")))
                self.assertTrue(os.path.exists(os.path.join(pasta_hl, "Fernando01_headlines.txt")))
                # A pasta sem legenda NÃO deve conter arquivo porque estava desativada
                self.assertFalse(os.path.exists(os.path.join(pasta_sem, "Fernando01_sem_legenda.mp4")))

    def test_enviar_pacote_drive_nuvem_sucesso(self):
        pasta_corte = os.path.join(self.temp_dir, "Fernando02")
        os.makedirs(pasta_corte, exist_ok=True)
        with open(os.path.join(pasta_corte, "Fernando02_com_legenda.mp4"), "wb") as f:
            f.write(b"bytes_video")

        variacoes = {"com_legenda": True}

        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "obter_token_acesso", return_value=("fake_token_123", None)):
                with patch.object(google_drive, "obter_ou_criar_pasta_drive", return_value="folder_drive_id_99"):
                    with patch.object(google_drive, "enviar_arquivo_drive", return_value={"id": "file_drive_id_101", "tamanho": 11}):
                        res = google_drive.enviar_pacote_drive(pasta_corte, "Fernando02", variacoes_ativas=variacoes)
                        self.assertTrue(res["sucesso"])
                        self.assertEqual(res["modo"], "nuvem")
                        self.assertIn("Fernando02_com_legenda.mp4", res["arquivos"])
                        self.assertEqual(res["arquivos"]["Fernando02_com_legenda.mp4"]["status"], "enviado_nuvem")
                        self.assertEqual(res["arquivos"]["Fernando02_com_legenda.mp4"]["drive_id"], "file_drive_id_101")

    def test_salvar_credenciais(self):
        with patch.object(config, "PASTA_DADOS", self.pasta_dados):
            with patch.object(google_drive, "obter_token_acesso", return_value=("token_valido", None)):
                res = google_drive.salvar_credenciais({"type": "service_account", "project_id": "test-proj"})
                self.assertTrue(res["conectado"])
                self.assertTrue(os.path.exists(os.path.join(self.pasta_dados, "google_drive_credentials.json")))


if __name__ == "__main__":
    unittest.main()
