"""Testes de conformidade estrita para a estrutura hierárquica exigida pelo usuário:
Raiz / [Nome do Evento Consolidado] / [Corte cru, Corte com headline, Corte com headline e legenda] / Arquivos FernandoXX
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from indomavel import config, google_drive


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
        [google_drive] / [Nome do Evento] / Corte com headline / (vídeo)
        [google_drive] / [Nome do Evento] / Corte cru / (vídeo cru + srt + headlines.txt)
        Sem pasta intermediária Fernando01!
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
        nome_evento_esperado = "FUTURO GLORIOSO TOUR - SANTA CATARINA"
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

        pasta_evento = os.path.join(self.pasta_drive_local, nome_evento_esperado)
        pasta_hl = os.path.join(pasta_evento, "Corte com headline")
        pasta_cru = os.path.join(pasta_evento, "Corte cru")

        # Verifica que o evento e as subpastas de modelo existem
        self.assertTrue(os.path.isdir(pasta_evento))
        self.assertTrue(os.path.isdir(pasta_hl))
        self.assertTrue(os.path.isdir(pasta_cru))

        # Não deve haver subpasta intermediária Fernando01
        self.assertFalse(os.path.exists(os.path.join(pasta_evento, "Fernando01")))

        # Corte com headline deve ter o MP4 com headline
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl, "Fernando01_sem_legenda.mp4")))
        # Corte cru deve ter o MP4 cru, o SRT e as sugestões de headlines
        self.assertTrue(os.path.isfile(os.path.join(pasta_cru, "Fernando01_headlines.txt")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_cru, "Fernando01_cru.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_cru, "Fernando01_cru.srt")))

        # Modalidades desativadas NÃO devem ter pastas criadas
        self.assertFalse(os.path.exists(os.path.join(pasta_evento, "Corte com legenda")))
        self.assertFalse(os.path.exists(os.path.join(pasta_evento, "Corte com headline e legenda")))

    def test_upload_nuvem_hierarquico(self):
        """Verifica se no Google Drive na nuvem a hierarquia é:
        Raiz / [Nome do Evento] / [Subpastas de modelo] / Arquivos
        Sem pasta intermediária FernandoXX!
        """
        pasta_corte = os.path.join(self.temp_dir, "Fernando02")
        os.makedirs(pasta_corte, exist_ok=True)
        with open(os.path.join(pasta_corte, "Fernando02_sem_legenda.mp4"), "wb") as f:
            f.write(b"video_bytes")
        with open(os.path.join(pasta_corte, "Fernando02_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("headline_txt")
        with open(os.path.join(pasta_corte, "Fernando02_cru.mp4"), "wb") as f:
            f.write(b"cru_bytes")

        titulo_video = "FUTURO GLORIOSO TOUR - SANTA CATARINA - Parte 6"
        nome_evento_esperado = "FUTURO GLORIOSO TOUR - SANTA CATARINA"
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

        # 1. Pasta do evento criada na raiz
        id_pasta_evento = f"id_drive_{nome_evento_esperado.replace(' ', '_')}"
        self.assertIn(f"raiz_id_123/{nome_evento_esperado}", pastas_criadas)

        # 2. NÃO deve existir pasta intermediária Fernando02 na nuvem
        self.assertNotIn(f"{id_pasta_evento}/Fernando02", pastas_criadas)

        # 3. Subpastas de modelo criadas DIRETAMENTE dentro da pasta do evento
        self.assertIn(f"{id_pasta_evento}/Corte com headline", pastas_criadas)
        self.assertIn(f"{id_pasta_evento}/Corte cru", pastas_criadas)

        # 4. Arquivos enviados para as subpastas corretas diretamente sob o evento
        id_dest_hl = pastas_criadas[f"{id_pasta_evento}/Corte com headline"]
        id_dest_cru = pastas_criadas[f"{id_pasta_evento}/Corte cru"]

        destinos_por_arq = {u["nome"]: u["id_destino"] for u in uploads_feitos}
        self.assertEqual(destinos_por_arq["Fernando02_sem_legenda.mp4"], id_dest_hl)
        self.assertEqual(destinos_por_arq["Fernando02_headlines.txt"], id_dest_cru)
        self.assertEqual(destinos_por_arq["Fernando02_cru.mp4"], id_dest_cru)

    def test_organizacao_tres_modalidades_quando_legenda_e_headline_selecionadas(self):
        """Verifica a regra das 3 subpastas diretamente sob o evento:
        - Corte cru (onde sempre vai ter sugestões de headlines, vídeo cru e srt)
        - Corte com headline (vídeo com headline)
        - Corte com headline e legenda (vídeo com headline e legenda)
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
        nome_evento_esperado = "FUTURO GLORIOSO TOUR - SANTA CATARINA"
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
        pasta_evento = os.path.join(self.pasta_drive_local, nome_evento_esperado)
        pasta_hl = os.path.join(pasta_evento, "Corte com headline")
        pasta_hl_leg = os.path.join(pasta_evento, "Corte com headline e legenda")
        pasta_orig = os.path.join(pasta_evento, "Corte cru")

        # 3 subpastas criadas diretamente sob o evento
        self.assertTrue(os.path.isdir(pasta_hl))
        self.assertTrue(os.path.isdir(pasta_hl_leg))
        self.assertTrue(os.path.isdir(pasta_orig))

        # Sem pasta Fernando03 intermediária
        self.assertFalse(os.path.exists(os.path.join(pasta_evento, "Fernando03")))

        # Conteúdos corretos em cada pasta
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl, "Fernando03_sem_legenda.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_hl_leg, "Fernando03_com_legenda.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando03_headlines.txt")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando03_cru.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(pasta_orig, "Fernando03_cru.srt")))


if __name__ == "__main__":
    unittest.main()

