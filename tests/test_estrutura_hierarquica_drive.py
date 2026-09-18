"""Testes de conformidade estrita para a estrutura hierárquica exigida pelo usuário:
Raiz / [Nome do Vídeo] / [Subpasta para cada Modalidade Selecionada]
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, call

from indomavel import config, google_drive, cortador_automatico


class TestEstruturaHierarquicaDrive(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_hierarquia_drive_")
        self.pasta_drive_local = os.path.join(self.temp_dir, "google_drive")
        os.makedirs(self.pasta_drive_local, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_organizacao_local_duas_modalidades_ativas(self):
        """Atualmente ativo: headline inclusa (sem_legenda) + original (cru).

        Verifica se os arquivos ficam em:
        [google_drive] / [Nome do Vídeo] / Cortes com headline/ (vídeo + headlines.txt)
        [google_drive] / [Nome do Vídeo] / Cortes originais/ (vídeo cru + srt)
        """
        pasta_corte = os.path.join(self.temp_dir, "Fernando01")
        os.makedirs(pasta_corte, exist_ok=True)

        with open(os.path.join(pasta_corte, "Fernando01_sem_legenda.mp4"), "wb") as f:
            f.write(b"video_headline_1x1")
        with open(os.path.join(pasta_corte, "Fernando01_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("Sugestoes de headlines")
        with open(os.path.join(pasta_corte, "Fernando01_cru.mp4"), "wb") as f:
            f.write(b"video_cru")
        with open(os.path.join(pasta_corte, "Fernando01_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:10,000\nFala\n")

        titulo_video = "FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 7"
        variacoes = {
            "com_legenda": False,
            "sem_legenda": True,
            "so_legenda": False,
            "cru": True,
            "headlines": True,
        }

        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "obter_token_acesso", return_value=(None, "sem_token")):
                res = google_drive.enviar_pacote_drive(
                    pasta_corte=pasta_corte,
                    prefixo="Fernando01",
                    variacoes_ativas=variacoes,
                    titulo_video=titulo_video,
                )

        self.assertTrue(res["sucesso"])
        self.assertEqual(res["modo"], "fallback_local")

        pasta_video = os.path.join(self.pasta_drive_local, titulo_video)
        pasta_hl = os.path.join(pasta_video, "Cortes com headline")
        pasta_cru = os.path.join(pasta_video, "Cortes originais")

        # Verifica que as duas pastas existem
        self.assertTrue(os.path.isdir(pasta_hl))
        self.assertTrue(os.path.isdir(pasta_cru))

        # Cortes com headline deve ter o MP4 com headline e o arquivo TXT de sugestões
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl, "Fernando01_sem_legenda.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl, "Fernando01_headlines.txt")))

        # Cortes originais deve ter o MP4 cru e o SRT
        self.assertTrue(os.path.isfile(os.path.join(pasta_cru, "Fernando01_cru.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_cru, "Fernando01_cru.srt")))

        # Modalidades desativadas NÃO devem ter pastas criadas
        self.assertFalse(os.path.exists(os.path.join(pasta_video, "Cortes com legenda")))
        self.assertFalse(os.path.exists(os.path.join(pasta_video, "Cortes com headline e legenda")))

    def test_upload_nuvem_hierarquico(self):
        """Verifica se no Google Drive na nuvem a pasta do vídeo é criada na raiz e as subpastas dentro dela."""
        pasta_corte = os.path.join(self.temp_dir, "Fernando02")
        os.makedirs(pasta_corte, exist_ok=True)
        with open(os.path.join(pasta_corte, "Fernando02_sem_legenda.mp4"), "wb") as f:
            f.write(b"video_bytes")
        with open(os.path.join(pasta_corte, "Fernando02_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("headline_txt")
        with open(os.path.join(pasta_corte, "Fernando02_cru.mp4"), "wb") as f:
            f.write(b"cru_bytes")

        titulo_video = "FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 6"
        variacoes = {"sem_legenda": True, "headlines": True, "cru": True}

        pastas_criadas = {}

        def mock_obter_ou_criar(nome, id_pai, token):
            chave = f"{id_pai}/{nome}"
            id_gerado = f"id_drive_{nome.replace(' ', '_')}"
            pastas_criadas[chave] = id_gerado
            return id_gerado

        uploads_feitos = []

        def mock_enviar_arquivo(caminho, nome, id_destino, token):
            uploads_feitos.append({"nome": nome, "id_destino": id_destino})
            return {"id": f"id_arq_{nome}", "tamanho": os.path.getsize(caminho)}

        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "obter_token_acesso", return_value=("token_valido_999", None)):
                with patch.object(google_drive, "obter_ou_criar_pasta_drive", side_effect=mock_obter_ou_criar):
                    with patch.object(google_drive, "enviar_arquivo_drive", side_effect=mock_enviar_arquivo):
                        res = google_drive.enviar_pacote_drive(
                            pasta_corte=pasta_corte,
                            prefixo="Fernando02",
                            variacoes_ativas=variacoes,
                            pasta_id_raiz="raiz_id_123",
                            titulo_video=titulo_video,
                        )

        self.assertTrue(res["sucesso"])
        self.assertEqual(res["modo"], "nuvem")

        # 1. Pasta do vídeo criada na raiz
        id_pasta_video = f"id_drive_{titulo_video.replace(' ', '_')}"
        self.assertIn(f"raiz_id_123/{titulo_video}", pastas_criadas)

        # 2. Subpastas de modalidade criadas dentro da pasta do vídeo
        self.assertIn(f"{id_pasta_video}/Cortes com headline", pastas_criadas)
        self.assertIn(f"{id_pasta_video}/Cortes originais", pastas_criadas)

        # 3. Arquivos enviados para as subpastas corretas
        id_dest_hl = pastas_criadas[f"{id_pasta_video}/Cortes com headline"]
        id_dest_cru = pastas_criadas[f"{id_pasta_video}/Cortes originais"]

        destinos_por_arq = {u["nome"]: u["id_destino"] for u in uploads_feitos}
        self.assertEqual(destinos_por_arq["Fernando02_sem_legenda.mp4"], id_dest_hl)
        self.assertEqual(destinos_por_arq["Fernando02_headlines.txt"], id_dest_hl)
        self.assertEqual(destinos_por_arq["Fernando02_cru.mp4"], id_dest_cru)

    def test_organizacao_tres_modalidades_quando_legenda_e_headline_selecionadas(self):
        """Verifica a regra: 'se eu marcar legenda e headlines já serão 3 pastas,
        uma com o corte com headline apenas(e um arquivo de texto com outras sugestões de headlines),
        outra pasta com headline e legenda e outra pasta com o corte original'.
        """
        pasta_corte = os.path.join(self.temp_dir, "Fernando03")
        os.makedirs(pasta_corte, exist_ok=True)

        with open(os.path.join(pasta_corte, "Fernando03_com_legenda.mp4"), "wb") as f:
            f.write(b"video_com_legenda")
        with open(os.path.join(pasta_corte, "Fernando03_sem_legenda.mp4"), "wb") as f:
            f.write(b"video_com_headline_apenas")
        with open(os.path.join(pasta_corte, "Fernando03_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("Sugestoes extras")
        with open(os.path.join(pasta_corte, "Fernando03_cru.mp4"), "wb") as f:
            f.write(b"video_cru")
        with open(os.path.join(pasta_corte, "Fernando03_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nTexto\n")

        titulo_video = "FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 5"
        variacoes = {
            "com_legenda": True,
            "sem_legenda": True,
            "so_legenda": False,
            "cru": True,
            "headlines": True,
        }

        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "obter_token_acesso", return_value=(None, "sem_token")):
                res = google_drive.enviar_pacote_drive(
                    pasta_corte=pasta_corte,
                    prefixo="Fernando03",
                    variacoes_ativas=variacoes,
                    titulo_video=titulo_video,
                )

        self.assertTrue(res["sucesso"])
        pasta_video = os.path.join(self.pasta_drive_local, titulo_video)
        pasta_hl = os.path.join(pasta_video, "Cortes com headline")
        pasta_hl_leg = os.path.join(pasta_video, "Cortes com headline e legenda")
        pasta_orig = os.path.join(pasta_video, "Cortes originais")

        # 3 pastas criadas
        self.assertTrue(os.path.isdir(pasta_hl))
        self.assertTrue(os.path.isdir(pasta_hl_leg))
        self.assertTrue(os.path.isdir(pasta_orig))

        # Conteúdos corretos em cada pasta
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl, "Fernando03_sem_legenda.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl, "Fernando03_headlines.txt")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl_leg, "Fernando03_com_legenda.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando03_cru.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando03_cru.srt")))

    def test_organizacao_apenas_legenda_duas_pastas(self):
        """Verifica a regra: 'se eu marcar vídeo com legenda vai ter uma pasta com o corte original e outra com o corte legendado'.
        Quando sem_legenda=False e com_legenda=True, deve gerar exatamente 2 pastas:
        - 'Cortes com legenda'
        - 'Cortes originais'
        """
        pasta_corte = os.path.join(self.temp_dir, "Fernando04")
        os.makedirs(pasta_corte, exist_ok=True)

        with open(os.path.join(pasta_corte, "Fernando04_com_legenda.mp4"), "wb") as f:
            f.write(b"video_legendado")
        with open(os.path.join(pasta_corte, "Fernando04_cru.mp4"), "wb") as f:
            f.write(b"video_cru")
        with open(os.path.join(pasta_corte, "Fernando04_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nTexto\n")

        titulo_video = "FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 4"
        variacoes = {
            "com_legenda": True,
            "sem_legenda": False,
            "so_legenda": False,
            "cru": True,
            "headlines": False,
        }

        with patch.object(config, "PASTA_GOOGLE_DRIVE", self.pasta_drive_local):
            with patch.object(google_drive, "obter_token_acesso", return_value=(None, "sem_token")):
                res = google_drive.enviar_pacote_drive(
                    pasta_corte=pasta_corte,
                    prefixo="Fernando04",
                    variacoes_ativas=variacoes,
                    titulo_video=titulo_video,
                )

        self.assertTrue(res["sucesso"])
        pasta_video = os.path.join(self.pasta_drive_local, titulo_video)
        pasta_leg = os.path.join(pasta_video, "Cortes com legenda")
        pasta_orig = os.path.join(pasta_video, "Cortes originais")

        # Exatamente 2 pastas criadas
        self.assertTrue(os.path.isdir(pasta_leg))
        self.assertTrue(os.path.isdir(pasta_orig))
        self.assertFalse(os.path.exists(os.path.join(pasta_video, "Cortes com headline")))
        self.assertFalse(os.path.exists(os.path.join(pasta_video, "Cortes com headline e legenda")))

        self.assertTrue(os.path.isfile(os.path.join(pasta_leg, "Fernando04_com_legenda.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando04_cru.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando04_cru.srt")))


if __name__ == "__main__":
    unittest.main()
