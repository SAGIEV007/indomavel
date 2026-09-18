"""Testes automatizados para a estrutura de pacotes FernandoXX,
verificação de integridade no Drive, exclusão automática de cache e controle sequencial.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from indomavel import config, cortador_automatico, servidor, youtube


class TestEstruturaFernando(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_fernando_")
        self.pasta_drive = os.path.join(self.temp_dir, "google_drive")
        self.pasta_videos = os.path.join(self.temp_dir, "videos")
        self.pasta_downloads = os.path.join(self.temp_dir, "downloads")
        os.makedirs(self.pasta_drive, exist_ok=True)
        os.makedirs(self.pasta_videos, exist_ok=True)
        os.makedirs(self.pasta_downloads, exist_ok=True)

        servidor.app.config["TESTING"] = True
        self.cliente = servidor.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_proxima_pasta_fernando_cria_fernando01(self):
        num, pasta = cortador_automatico.proxima_pasta_fernando(self.pasta_drive)
        self.assertEqual(num, 1)
        self.assertEqual(os.path.basename(pasta), "Fernando01")
        self.assertTrue(os.path.isdir(pasta))

    def test_proxima_pasta_fernando_sequencial(self):
        os.makedirs(os.path.join(self.pasta_drive, "Fernando01"), exist_ok=True)
        os.makedirs(os.path.join(self.pasta_drive, "Fernando02"), exist_ok=True)

        num, pasta = cortador_automatico.proxima_pasta_fernando(self.pasta_drive)
        self.assertEqual(num, 3)
        self.assertEqual(os.path.basename(pasta), "Fernando03")
        self.assertTrue(os.path.isdir(pasta))

    def test_proxima_pasta_fernando_com_gaps(self):
        os.makedirs(os.path.join(self.pasta_drive, "Fernando02"), exist_ok=True)
        os.makedirs(os.path.join(self.pasta_drive, "Fernando07"), exist_ok=True)

        num, pasta = cortador_automatico.proxima_pasta_fernando(self.pasta_drive)
        self.assertEqual(num, 8)
        self.assertEqual(os.path.basename(pasta), "Fernando08")

    def test_verificar_pacote_completo_valido(self):
        pasta_corte = os.path.join(self.pasta_drive, "Fernando01")
        os.makedirs(pasta_corte, exist_ok=True)
        prefixo = "Fernando01"

        # Cria os 5 arquivos válidos com tamanho > 0
        with open(os.path.join(pasta_corte, f"{prefixo}_com_legenda.mp4"), "wb") as f:
            f.write(b"video_com_legenda_bytes")
        with open(os.path.join(pasta_corte, f"{prefixo}_sem_legenda.mp4"), "wb") as f:
            f.write(b"video_sem_legenda_bytes")
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.mp4"), "wb") as f:
            f.write(b"video_cru_bytes")
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nLegenda\n")
        with open(os.path.join(pasta_corte, f"{prefixo}_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("Sugestões de Headlines")

        self.assertTrue(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo))

    def test_verificar_pacote_completo_arquivo_faltando(self):
        pasta_corte = os.path.join(self.pasta_drive, "Fernando02")
        os.makedirs(pasta_corte, exist_ok=True)
        prefixo = "Fernando02"

        with open(os.path.join(pasta_corte, f"{prefixo}_com_legenda.mp4"), "wb") as f:
            f.write(b"video_bytes")
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.mp4"), "wb") as f:
            f.write(b"video_cru_bytes")
        # Falta _sem_legenda.mp4, _cru.srt e _headlines.txt

        self.assertFalse(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo))

    def test_verificar_pacote_completo_arquivo_vazio(self):
        pasta_corte = os.path.join(self.pasta_drive, "Fernando03")
        os.makedirs(pasta_corte, exist_ok=True)
        prefixo = "Fernando03"

        with open(os.path.join(pasta_corte, f"{prefixo}_com_legenda.mp4"), "wb") as f:
            f.write(b"video_bytes")
        with open(os.path.join(pasta_corte, f"{prefixo}_sem_legenda.mp4"), "wb") as f:
            f.write(b"")  # Vazio (0 bytes)
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.mp4"), "wb") as f:
            f.write(b"video_cru_bytes")
        with open(os.path.join(pasta_corte, f"{prefixo}_cru.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:05,000\nLegenda\n")
        with open(os.path.join(pasta_corte, f"{prefixo}_headlines.txt"), "w", encoding="utf-8") as f:
            f.write("Headlines")

        self.assertFalse(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo))

    def test_limpar_arquivos_incompletos(self):
        # Arquivo temporário que deve ser removido
        tmp_file = os.path.join(self.pasta_drive, "teste.tmp")
        with open(tmp_file, "w") as f:
            f.write("temp")

        part_file = os.path.join(self.pasta_downloads, "video.part")
        with open(part_file, "w") as f:
            f.write("partial")

        # MP4 de 0 bytes gerado por crash
        zero_mp4 = os.path.join(self.pasta_drive, "quebrado.mp4")
        with open(zero_mp4, "w") as f:
            pass

        # Pasta FernandoXX vazia deixada para trás
        pasta_vazia = os.path.join(self.pasta_drive, "Fernando99")
        os.makedirs(pasta_vazia, exist_ok=True)

        # Arquivo válido que NÃO deve ser removido
        valido_mp4 = os.path.join(self.pasta_drive, "valido.mp4")
        with open(valido_mp4, "wb") as f:
            f.write(b"conteudo_valido")

        removidos = cortador_automatico.limpar_arquivos_incompletos([self.pasta_drive, self.pasta_downloads])

        self.assertFalse(os.path.exists(tmp_file))
        self.assertFalse(os.path.exists(part_file))
        self.assertFalse(os.path.exists(zero_mp4))
        self.assertFalse(os.path.exists(pasta_vazia))
        self.assertTrue(os.path.exists(valido_mp4))
        self.assertGreaterEqual(len(removidos), 4)

    def test_liberar_cache_video(self):
        youtube_id = "test_vid_cache"
        video_bruto = os.path.join(self.pasta_videos, f"{youtube_id}.mp4")
        video_ficha = os.path.join(self.pasta_videos, f"{youtube_id}.json")
        with open(video_bruto, "wb") as f:
            f.write(b"A" * 1024 * 100)  # 100 KB
        with open(video_ficha, "w", encoding="utf-8") as f:
            json.dump({"audio_original": True}, f)

        temp_dl = os.path.join(self.pasta_downloads, f"{youtube_id}_slice.mp4")
        with open(temp_dl, "wb") as f:
            f.write(b"B" * 1024 * 50)  # 50 KB

        with patch("indomavel.config.PASTA_VIDEOS", self.pasta_videos):
            with patch("indomavel.config.PASTA_DOWNLOADS", self.pasta_downloads):
                liberados = cortador_automatico.liberar_cache_video(youtube_id)
                self.assertGreater(liberados, 0)
                self.assertFalse(os.path.exists(video_bruto))
                self.assertFalse(os.path.exists(video_ficha))
                self.assertFalse(os.path.exists(temp_dl))

    def test_gerar_pacote_corte_integracao(self):
        pasta_corte = os.path.join(self.pasta_drive, "Fernando05")
        prefixo = "Fernando05"
        bloco = {
            "id": "b1",
            "titulo": "O debate sobre responsabilidade fiscal",
            "inicio": 10.0,
            "fim": 60.0,
            "duracao": 50.0,
            "renan_falando": False,
            "locutor": "Especialista em Finanças",
            "resumo": "Análise das metas de déficit zero",
        }
        info = {"titulo": "Debate Econômico Especial"}

        def mock_exportar(youtube_id, inicio, fim, formato, estilo, trechos, pasta_saida, titulo, ao_progredir, nome_arquivo):
            caminho = os.path.join(pasta_saida, nome_arquivo)
            with open(caminho, "wb") as f:
                f.write(b"mock_mp4_content")
            return caminho

        def mock_recortar(caminho_fonte, inicio, fim, caminho_cru):
            with open(caminho_cru, "wb") as f:
                f.write(b"mock_cru_content")
            return inicio

        caminho_fonte = os.path.join(self.temp_dir, "dummy_fonte.mp4")
        with open(caminho_fonte, "wb") as f:
            f.write(b"dummy_fonte_bytes")

        with patch("indomavel.render.exportar", side_effect=mock_exportar):
            with patch("indomavel.recorte.recortar_sem_perda", side_effect=mock_recortar):
                pacote = cortador_automatico.gerar_pacote_corte(
                    youtube_id="deb_eco_0001",
                    info=info,
                    bloco=bloco,
                    pasta_corte=pasta_corte,
                    prefixo=prefixo,
                    caminho_fonte=caminho_fonte,
                )

        self.assertEqual(pacote["prefixo"], prefixo)
        self.assertTrue(cortador_automatico.verificar_pacote_completo(pasta_corte, prefixo))

        # Verifica conteúdo do arquivo de headlines
        txt_path = os.path.join(pasta_corte, f"{prefixo}_headlines.txt")
        self.assertTrue(os.path.exists(txt_path))
        with open(txt_path, "r", encoding="utf-8") as f:
            conteudo = f.read()
            self.assertIn("SUGESTÕES DE HEADLINES", conteudo)
            self.assertIn("Especialista em Finanças", conteudo)
            # Como renan_falando é False, não deve forçar Renan Santos
            self.assertNotIn("Renan Santos (Partido Missão)", conteudo)

    def test_execucao_sequencial_com_exclusao_automatica_cache(self):
        executor = cortador_automatico.ExecutorCortesSequencial()
        youtube_id = "seq_test_vid"

        # Simula arquivo bruto em cache
        video_bruto = os.path.join(self.pasta_videos, f"{youtube_id}.mp4")
        with open(video_bruto, "wb") as f:
            f.write(b"X" * 1024 * 200)

        blocos = [
            {"id": "b1", "titulo": "Bloco 1", "inicio": 0.0, "fim": 40.0, "potencial": 90.0, "renan_falando": True},
        ]
        info = {"titulo": "Live Sequencial"}

        def mock_gerar_pacote(youtube_id, info, bloco, pasta_corte, prefixo, **kwargs):
            os.makedirs(pasta_corte, exist_ok=True)
            for nome in [f"{prefixo}_com_legenda.mp4", f"{prefixo}_sem_legenda.mp4", f"{prefixo}_cru.mp4"]:
                with open(os.path.join(pasta_corte, nome), "wb") as f:
                    f.write(b"video_data")
            with open(os.path.join(pasta_corte, f"{prefixo}_cru.srt"), "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:05,000\nLegenda\n")
            with open(os.path.join(pasta_corte, f"{prefixo}_headlines.txt"), "w", encoding="utf-8") as f:
                f.write("Headlines")
            return {"pasta": pasta_corte, "prefixo": prefixo}

        with patch("indomavel.config.PASTA_GOOGLE_DRIVE", self.pasta_drive):
            with patch("indomavel.config.PASTA_VIDEOS", self.pasta_videos):
                with patch("indomavel.config.PASTA_DOWNLOADS", self.pasta_downloads):
                    with patch("indomavel.config.RETENCAO_LIMPA", True):
                        with patch("indomavel.youtube.baixar_video_maximo", return_value=video_bruto):
                            with patch("indomavel.cortador_automatico.gerar_pacote_corte", side_effect=mock_gerar_pacote):
                                executor._executar_video({
                                    "youtube_id": youtube_id,
                                    "info": info,
                                    "blocos": blocos,
                                    "frases": None,
                                    "max_cortes": 1,
                                    "refazer": False,
                                    "enfileirado_em": 0,
                                })

        status = cortador_automatico.obter_status_cortes(youtube_id)
        self.assertIsNotNone(status)
        self.assertEqual(status.get("estado"), "concluido")
        self.assertTrue(status.get("cache_liberado"))
        self.assertGreater(status.get("bytes_liberados", 0), 0)
        # O arquivo bruto deve ter sido excluído automaticamente após os cortes no Drive
        self.assertFalse(os.path.exists(video_bruto))

    def test_endpoints_cortes_e_limpeza(self):
        modo_anterior = cortador_automatico.obter_modo_automatico()
        try:
            # 1. Toggle modo automático
            resp = self.cliente.get("/api/automacao/cortes/status")
            self.assertEqual(resp.status_code, 200)
            dados = resp.get_json()
            self.assertIn("modo_automatico", dados)

            resp_toggle = self.cliente.post("/api/automacao/cortes/toggle", json={"ativo": True})
            self.assertEqual(resp_toggle.status_code, 200)
            self.assertTrue(resp_toggle.get_json()["modo_automatico"])

            # Desativa de volta
            cortador_automatico.definir_modo_automatico(False)
        finally:
            cortador_automatico.definir_modo_automatico(True)

        # 2. Endpoint de limpeza de cache específico
        with patch("indomavel.cortador_automatico.liberar_cache_video", return_value=1024 * 1024 * 50):
            resp_limpar = self.cliente.post("/api/cortes/vid_limpar1/limpar-cache")
            self.assertEqual(resp_limpar.status_code, 200)
            d_limpar = resp_limpar.get_json()
            self.assertTrue(d_limpar.get("ok"))
            self.assertEqual(d_limpar.get("mb_liberados"), 50.0)

        # 3. Endpoint de limpeza de cache geral
        with patch("indomavel.cortador_automatico.limpar_cache_cortes_concluidos", return_value=1024 * 1024 * 120):
            resp_geral = self.cliente.post("/api/cortes/limpar-cache-geral")
            self.assertEqual(resp_geral.status_code, 200)
            d_geral = resp_geral.get_json()
            self.assertTrue(d_geral.get("ok"))
            self.assertEqual(d_geral.get("mb_liberados"), 120.0)

        # 4. Status de corte via GET /api/cortes/<id>
        with patch("indomavel.cortador_automatico.obter_status_cortes", return_value={"estado": "concluido"}):
            resp_st = self.cliente.get("/api/cortes/vid_limpar1")
            self.assertEqual(resp_st.status_code, 200)
            self.assertEqual(resp_st.get_json().get("status", {}).get("estado"), "concluido")

    def test_acervo_local_inclui_status_cortes(self):
        from indomavel import acervo_local
        pasta_dados = os.path.join(self.temp_dir, "dados")
        yid = "vid_local_teste"
        pasta_v = os.path.join(pasta_dados, "videos", yid)
        os.makedirs(pasta_v, exist_ok=True)

        with open(os.path.join(pasta_v, "info.json"), "w", encoding="utf-8") as f:
            json.dump({"titulo": "Vídeo Local Teste"}, f)
        with open(os.path.join(pasta_v, "estado.json"), "w", encoding="utf-8") as f:
            json.dump({"estado": "pronto"}, f)
        with open(os.path.join(pasta_v, "cortes_automaticos.json"), "w", encoding="utf-8") as f:
            json.dump({"estado": "concluido", "feitos": 4}, f)

        with patch("indomavel.config.PASTA_DADOS", pasta_dados):
            lista = acervo_local.listar()
            self.assertEqual(len(lista), 1)
            self.assertEqual(lista[0]["youtube_id"], yid)
            self.assertIn("cortes", lista[0])
            self.assertIsNotNone(lista[0]["cortes"])
            self.assertEqual(lista[0]["cortes"].get("estado"), "concluido")
            self.assertEqual(lista[0]["cortes"].get("feitos"), 4)

    def test_remover_video_cache_multiplos_formatos(self):
        yid = "multi_vid_123"
        f_mp4 = os.path.join(self.pasta_videos, f"{yid}.mp4")
        f_json = os.path.join(self.pasta_videos, f"{yid}.json")
        f_part = os.path.join(self.pasta_videos, f"{yid}.part")
        f_webm = os.path.join(self.pasta_videos, f"{yid}.f137.webm")

        for caminho in (f_mp4, f_json, f_part, f_webm):
            with open(caminho, "wb") as f:
                f.write(b"sample_content_12345")

        with patch("indomavel.config.PASTA_VIDEOS", self.pasta_videos):
            liberados = youtube.remover_video_cache(yid)
            self.assertGreaterEqual(liberados, 4 * 20)
            self.assertFalse(os.path.exists(f_mp4))
            self.assertFalse(os.path.exists(f_json))
            self.assertFalse(os.path.exists(f_part))
            self.assertFalse(os.path.exists(f_webm))

    def test_abrir_pasta_drive_local_endpoint(self):
        with patch("os.startfile") as mock_startfile:
            resp = self.cliente.post("/api/drive/abrir-pasta-local")
            self.assertEqual(resp.status_code, 200)
            dados = resp.get_json()
            self.assertTrue(dados.get("ok"))
            mock_startfile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
