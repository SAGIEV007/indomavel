"""Testes do gravador automático de lives, fatiador em 30 min, SQLite e retenção."""

import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from indomavel import gravador_live


class TestGravadorLive(unittest.TestCase):
    def setUp(self):
        self.pasta_temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.pasta_temp.name, "test_lives.sqlite3")
        self.patcher_db = patch("indomavel.gravador_live._caminho_db", return_value=self.db_path)
        self.patcher_db.start()
        gravador_live.iniciar_banco()
        self.gerenciador = gravador_live.GerenciadorGravacaoLive()

    def tearDown(self):
        self.patcher_db.stop()
        import gc
        gc.collect()
        try:
            self.pasta_temp.cleanup()
        except Exception:
            pass

    def test_banco_inicia_tabelas_corretamente(self):
        with gravador_live._conectar() as conn:
            sessoes = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessoes_live'").fetchone()
            partes = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='partes_live'").fetchone()
            self.assertIsNotNone(sessoes)
            self.assertIsNotNone(partes)

    def test_obter_status_inicial(self):
        status = self.gerenciador.obter_status()
        self.assertFalse(status["gravando"])
        self.assertIsNone(status["sessao_ativa"])
        self.assertEqual(status["partes"], [])

    def test_cadastro_e_reconciliacao_de_partes(self):
        with gravador_live._conectar() as conn:
            cursor = conn.execute("""
                INSERT INTO sessoes_live (youtube_id, url_live, pasta_destino, estado, criado_em, atualizado_em)
                VALUES ('G9FJr1EX0wU', 'https://youtube.com/watch?v=G9FJr1EX0wU', ?, 'gravando', ?, ?)
            """, (self.pasta_temp.name, time.time(), time.time()))
            sessao_id = cursor.lastrowid
            conn.commit()

        video_path = os.path.join(self.pasta_temp.name, "parte_000.mp4")
        with open(video_path, "wb") as f:
            f.write(b"x" * 60000)

        self.gerenciador._cadastrar_parte(sessao_id, "parte_000.mp4", video_path, 0.0, 1800.0)

        with gravador_live._conectar() as conn:
            row = conn.execute("SELECT * FROM partes_live WHERE sessao_id = ?", (sessao_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["numero_parte"], 1)
            self.assertEqual(row["estado"], "gravado")
            self.assertEqual(row["duracao_s"], 1800.0)

    def test_retencao_48h_remove_arquivo_e_mantem_metadados(self):
        with gravador_live._conectar() as conn:
            cursor = conn.execute("""
                INSERT INTO sessoes_live (youtube_id, url_live, pasta_destino, estado, criado_em, atualizado_em)
                VALUES ('G9FJr1EX0wU', 'https://youtube.com/watch?v=G9FJr1EX0wU', ?, 'concluido', ?, ?)
            """, (self.pasta_temp.name, time.time(), time.time()))
            sessao_id = cursor.lastrowid

            video_path = os.path.join(self.pasta_temp.name, "parte_antiga.mp4")
            with open(video_path, "wb") as f:
                f.write(b"antigo" * 1000)

            tempo_antigo = time.time() - (49 * 3600)  # 49 horas atrás
            conn.execute("""
                INSERT INTO partes_live (
                    sessao_id, youtube_id, numero_parte, nome_arquivo, caminho_video,
                    estado, youtube_video_id, youtube_url, resumo, capitulos, transcricao,
                    enviado_em, limpo_disco, criado_em, atualizado_em
                ) VALUES (?, 'G9FJr1EX0wU', 1, 'parte_antiga.mp4', ?, 'enviado', 'yt123',
                          'https://youtu.be/yt123', 'Resumo permanente', '00:00 Início', 'Transcrição permanente',
                          ?, 0, ?, ?)
            """, (sessao_id, video_path, tempo_antigo, tempo_antigo, tempo_antigo))
            conn.commit()

        self.assertTrue(os.path.exists(video_path))
        res = self.gerenciador.limpar_retencao_48h()
        self.assertEqual(res["removidos"], 1)
        self.assertFalse(os.path.exists(video_path))

        with gravador_live._conectar() as conn:
            row = conn.execute("SELECT * FROM partes_live WHERE sessao_id = ?", (sessao_id,)).fetchone()
            self.assertEqual(row["limpo_disco"], 1)
            self.assertEqual(row["resumo"], "Resumo permanente")
            self.assertEqual(row["capitulos"], "00:00 Início")

    def test_reprocessar_parte_reseta_estado(self):
        with gravador_live._conectar() as conn:
            cursor = conn.execute("""
                INSERT INTO sessoes_live (youtube_id, url_live, pasta_destino, estado, criado_em, atualizado_em)
                VALUES ('G9FJr1EX0wU', 'https://youtube.com/watch?v=G9FJr1EX0wU', ?, 'concluido', ?, ?)
            """, (self.pasta_temp.name, time.time(), time.time()))
            sessao_id = cursor.lastrowid
            c = conn.execute("""
                INSERT INTO partes_live (
                    sessao_id, youtube_id, numero_parte, nome_arquivo, caminho_video, estado, erro_mensagem
                ) VALUES (?, 'G9FJr1EX0wU', 1, 'parte_000.mp4', 'dummy.mp4', 'erro', 'Erro simulado')
            """, (sessao_id,))
            parte_id = c.lastrowid
            conn.commit()

        # Sem a linha de processamento em segundo plano: ela pegaria a parte na hora (e o arquivo de mentira dá erro).
        with patch.object(self.gerenciador, "_iniciar_thread_ia") as iniciar_thread:
            res = self.gerenciador.reprocessar_parte(parte_id)
        iniciar_thread.assert_called_once()
        self.assertTrue(res["ok"])

        with gravador_live._conectar() as conn:
            row = conn.execute("SELECT estado, erro_mensagem FROM partes_live WHERE id = ?", (parte_id,)).fetchone()
            self.assertEqual(row["estado"], "gravado")
            self.assertEqual(row["erro_mensagem"], "")


if __name__ == "__main__":
    unittest.main()
