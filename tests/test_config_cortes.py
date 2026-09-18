"""Testes para o motor de configuração de cortes automáticos e endpoints da API."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from indomavel import config, cortador_automatico, google_drive, servidor


class TestConfigCortes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_cfg_cortes_")
        self.pasta_dados = os.path.join(self.temp_dir, "dados")
        os.makedirs(self.pasta_dados, exist_ok=True)
        servidor.app.config["TESTING"] = True
        self.cliente = servidor.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_carregar_config_padrao_quando_arquivo_nao_existe(self):
        with patch.object(config, "PASTA_DADOS", self.pasta_dados):
            cfg = cortador_automatico.carregar_config_cortes()
            self.assertEqual(cfg["proporcao"], "1:1")
            self.assertTrue(cfg["variacoes"]["com_legenda"])
            self.assertFalse(cfg["variacoes"]["sem_legenda"])  # Desmarcado por padrão conforme solicitado
            self.assertTrue(cfg["variacoes"]["so_legenda"])
            self.assertTrue(cfg["variacoes"]["cru"])
            self.assertTrue(cfg["variacoes"]["headlines"])
            self.assertEqual(cfg["visual"]["cortar_topo"], 140)
            self.assertEqual(cfg["drive"]["pasta_id"], google_drive.ID_PASTA_PADRAO)

    def test_salvar_e_recuperar_config(self):
        with patch.object(config, "PASTA_DADOS", self.pasta_dados):
            novos = {
                "proporcao": "9:16",
                "variacoes": {
                    "com_legenda": True,
                    "sem_legenda": True,
                    "so_legenda": False,
                },
                "visual": {
                    "marca_dagua": True,
                    "cortar_topo": 180,
                    "tamanho_headline": 50,
                },
                "drive": {
                    "pasta_id": "custom_folder_id_xyz",
                },
                "max_cortes_por_video": 6,
            }
            salvo = cortador_automatico.salvar_config_cortes(novos)
            self.assertEqual(salvo["proporcao"], "9:16")
            self.assertTrue(salvo["variacoes"]["sem_legenda"])
            self.assertFalse(salvo["variacoes"]["so_legenda"])
            self.assertTrue(salvo["visual"]["marca_dagua"])
            self.assertEqual(salvo["visual"]["cortar_topo"], 180)
            self.assertEqual(salvo["drive"]["pasta_id"], "custom_folder_id_xyz")
            self.assertEqual(salvo["max_cortes_por_video"], 6)

            # Recarrega do disco para confirmar persistência
            lido = cortador_automatico.carregar_config_cortes()
            self.assertEqual(lido["proporcao"], "9:16")
            self.assertEqual(lido["visual"]["cortar_topo"], 180)

    def test_endpoints_config_cortes(self):
        with patch.object(config, "PASTA_DADOS", self.pasta_dados):
            # GET /api/automacao/config-cortes
            resp_get = self.cliente.get("/api/automacao/config-cortes")
            self.assertEqual(resp_get.status_code, 200)
            dados_get = resp_get.get_json()
            self.assertTrue(dados_get["ok"])
            self.assertEqual(dados_get["config"]["proporcao"], "1:1")

            # POST /api/automacao/config-cortes
            payload = {
                "proporcao": "4:5",
                "variacoes": {"sem_legenda": True},
            }
            resp_post = self.cliente.post("/api/automacao/config-cortes", json=payload)
            self.assertEqual(resp_post.status_code, 200)
            dados_post = resp_post.get_json()
            self.assertTrue(dados_post["ok"])
            self.assertEqual(dados_post["config"]["proporcao"], "4:5")
            self.assertTrue(dados_post["config"]["variacoes"]["sem_legenda"])

    def test_endpoints_drive(self):
        with patch.object(config, "PASTA_DADOS", self.pasta_dados):
            # GET /api/drive/status
            resp_status = self.cliente.get("/api/drive/status")
            self.assertEqual(resp_status.status_code, 200)
            d_status = resp_status.get_json()
            self.assertIn("conectado", d_status)
            self.assertIn("modo", d_status)
            self.assertEqual(d_status["pasta_id"], google_drive.ID_PASTA_PADRAO)

            # POST /api/drive/configurar
            resp_cfg = self.cliente.post("/api/drive/configurar", json={"pasta_id": "nova_pasta_id_drive_456"})
            self.assertEqual(resp_cfg.status_code, 200)
            self.assertTrue(resp_cfg.get_json()["ok"])

            # POST /api/drive/testar
            resp_test = self.cliente.post("/api/drive/testar")
            self.assertEqual(resp_test.status_code, 200)


if __name__ == "__main__":
    unittest.main()
