"""Testes específicos para personalizações de proporção (1:1), variações opcionais e integridade no Drive."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from indomavel import config, cortador_automatico, render


class TestCortadorPersonalizacoes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_cortador_pers_")
        self.pasta_drive = os.path.join(self.temp_dir, "google_drive")
        self.pasta_videos = os.path.join(self.temp_dir, "videos")
        self.pasta_downloads = os.path.join(self.temp_dir, "downloads")
        self.pasta_dados = os.path.join(self.temp_dir, "dados")

        os.makedirs(self.pasta_drive, exist_ok=True)
        os.makedirs(self.pasta_videos, exist_ok=True)
        os.makedirs(self.pasta_downloads, exist_ok=True)
        os.makedirs(self.pasta_dados, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_gerar_pacote_corte_com_1x1_e_so_legenda(self):
        pasta_corte = os.path.join(self.pasta_drive, "Fernando10")
        prefixo = "Fernando10"
        bloco = {
            "id": "b_teste10",
            "titulo": "Explosão de Gastos Públicos",
            "inicio": 10.0,
            "fim": 50.0,
            "renan_falando": True,
            "resumo": "Análise fiscal contundente",
        }
        info = {"titulo": "Live Econômica 24hrs"}

        chamadas_render = []

        def mock_exportar(youtube_id, inicio, fim, formato, estilo, trechos, pasta_saida, titulo, ao_progredir, nome_arquivo):
            chamadas_render.append({"formato": formato, "estilo": estilo, "nome_arquivo": nome_arquivo})
            caminho = os.path.join(pasta_saida, nome_arquivo)
            with open(caminho, "wb") as f:
                f.write(b"mp4_mock_data")
            return caminho

        def mock_recortar(caminho_fonte, inicio, fim, caminho_cru):
            with open(caminho_cru, "wb") as f:
                f.write(b"cru_mock_data")
            return inicio

        caminho_fonte = os.path.join(self.temp_dir, "fonte.mp4")
        with open(caminho_fonte, "wb") as f:
            f.write(b"fonte_mock_bytes")

        # Configuração: formato 1:1, sem_legenda desligado, so_legenda ligado
        cfg = {
            "proporcao": "1:1",
            "variacoes": {
                "com_legenda": True,
                "sem_legenda": False,
                "so_legenda": True,
                "cru": True,
                "headlines": True,
            },
            "visual": {
                "marca_dagua": False,
                "cortar_topo": 140,
                "tamanho_headline": 44,
            },
            "drive": {"pasta_id": "test_folder", "upload_ativo": False}
        }

        with patch("indomavel.render.exportar", side_effect=mock_exportar):
            with patch("indomavel.recorte.recortar_sem_perda", side_effect=mock_recortar):
                pacote = cortador_automatico.gerar_pacote_corte(
                    youtube_id="teste_vid10",
                    info=info,
                    bloco=bloco,
                    pasta_corte=pasta_corte,
                    prefixo=prefixo,
                    caminho_fonte=caminho_fonte,
                    config_cortes=cfg,
                )

        self.assertEqual(pacote["formato"], "1:1")
        self.assertIn("com_legenda", pacote["arquivos"])
        self.assertIn("so_legenda", pacote["arquivos"])
        self.assertIn("cru", pacote["arquivos"])
        self.assertIn("headlines", pacote["arquivos"])
        # Como sem_legenda estava desligado, NÃO deve ter sido gerado
        self.assertNotIn("sem_legenda", pacote["arquivos"])
        self.assertFalse(os.path.exists(os.path.join(pasta_corte, f"{prefixo}_sem_legenda.mp4")))

        # Confere se os renders chamados usaram o formato 1:1
        for chamada in chamadas_render:
            self.assertEqual(chamada["formato"], "1:1")

        # Confere que o render de so_legenda operou com card=False e legenda=True
        chamada_so = next(c for c in chamadas_render if c["nome_arquivo"] == f"{prefixo}_so_legenda.mp4")
        self.assertFalse(chamada_so["estilo"]["card"])
        self.assertTrue(chamada_so["estilo"]["legenda"])

        # Confere integridade através de verificar_pacote_completo
        self.assertTrue(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo))

    def test_verificar_pacote_completo_dinamico_falha_se_faltar_ativo(self):
        pasta_corte = os.path.join(self.pasta_drive, "Fernando11")
        os.makedirs(pasta_corte, exist_ok=True)
        prefixo = "Fernando11"

        # Cria apenas o corte cru
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.mp4"), "wb") as f:
            f.write(b"cru_data")

        # Se so_legenda era exigido mas não foi criado, deve retornar False
        variacoes = {"cru": True, "so_legenda": True}
        self.assertFalse(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo, variacoes))

        # Se apenas cru era exigido, mas falta o cru.srt:
        variacoes_so_cru = {"cru": True}
        self.assertFalse(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo, variacoes_so_cru))

        # Ao criar o cru.srt com > 0 bytes:
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:01,000\nOi\n")
        self.assertTrue(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo, variacoes_so_cru))


if __name__ == "__main__":
    unittest.main()
