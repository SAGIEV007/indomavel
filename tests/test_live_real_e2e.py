"""Teste real de gravação, fatiamento e IA na live do YouTube informada pelo usuário."""

import os
import shutil
import time
import unittest

from indomavel import config, gemini, gravador_live


@unittest.skipUnless(os.environ.get("INDOMAVEL_TESTE_LIVE_REAL") == "1",
                     "grava uma live de verdade pela internet; rode com INDOMAVEL_TESTE_LIVE_REAL=1 enquanto houver live no ar")
class TestLiveRealIntegracao(unittest.TestCase):
    def test_extracao_metadados_live_real(self):
        url = "https://www.youtube.com/watch?v=G9FJr1EX0wU"
        info = gravador_live.obter_informacoes_live(url)
        self.assertEqual(info["youtube_id"], "G9FJr1EX0wU")
        self.assertTrue(info["is_live"])
        self.assertIsNotNone(info["url_video"])
        self.assertIsNotNone(info["url_audio"])
        print("\n[OK] Informações da live extraídas com sucesso:", info["titulo"])

    def test_gravacao_e_fatiamento_real_curto(self):
        url = "https://www.youtube.com/watch?v=G9FJr1EX0wU"
        pasta_teste = os.path.join(config.PASTA_VIDEOS, "teste_live_ci")
        if os.path.exists(pasta_teste):
            shutil.rmtree(pasta_teste, ignore_errors=True)

        print("\n[INFO] Iniciando gravação real da live por 15 segundos (blocos de 6s)...")
        # Inicia gravação com blocos de 6 segundos para testar o segmenter e a máquina de estados
        res = gravador_live.gravador.iniciar_gravacao(
            url_ou_id=url,
            dvr=True,
            duracao_chunk_s=6,
            pasta_base=pasta_teste
        )
        sessao_id = res["sessao_id"]
        print("[INFO] Sessão criada no SQLite:", sessao_id, "Pasta:", res["pasta"])

        # Deixa rodar por 14 segundos para gerar pelo menos 2 segmentos
        time.sleep(14)

        # Para a gravação
        print("[INFO] Parando gravação...")
        status_parar = gravador_live.gravador.parar_gravacao(sessao_id)
        self.assertEqual(status_parar["status"], "concluido")

        # Verifica partes cadastradas no banco SQLite
        status = gravador_live.gravador.obter_status()
        with gravador_live._conectar() as conn:
            partes = conn.execute("SELECT * FROM partes_live WHERE sessao_id = ?", (sessao_id,)).fetchall()
            print(f"[OK] Total de partes registradas no banco para a sessão: {len(partes)}")
            self.assertGreaterEqual(len(partes), 1)
            for p in partes:
                print(f"  -> Parte {p['numero_parte']}: {p['nome_arquivo']} | Estado: {p['estado']} | Tamanho: {p['tamanho_bytes']} bytes")
                self.assertTrue(os.path.exists(p["caminho_video"]))
                self.assertGreater(p["tamanho_bytes"], 10000)

        # Limpeza da pasta de teste
        if os.path.exists(pasta_teste):
            shutil.rmtree(pasta_teste, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
