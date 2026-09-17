"""Testes para filtros táticos e contagem de blocos_qa no Chub."""

import unittest
from indomavel.chub import Chub, _video


class TestChubFiltros(unittest.TestCase):
    def test_video_dict_inclui_blocos_qa(self):
        linha = {
            "youtube_id": "abc12345678",
            "titulo": "Vídeo Teste",
            "publicado_em": "2026-09-15T00:00:00Z",
            "duracao_s": "3600",
            "blocos": "12",
            "blocos_qa": "4",
        }
        v = _video(linha)
        self.assertEqual(v["blocos"], 12)
        self.assertEqual(v["blocos_qa"], 4)

    def test_videos_recentes_monta_filtros_taticos(self):
        class ChubSpy(Chub):
            def __init__(self):
                self.consultas = []

            def sql(self, consulta, usar_cache=True, limite=500):
                self.consultas.append(consulta)
                return []

        chub = ChubSpy()

        chub.videos_recentes(filtro_tatico="virais")
        self.assertIn("density_rank > 80", chub.consultas[-1])
        self.assertIn("blocos_qa", chub.consultas[-1])

        chub.videos_recentes(filtro_tatico="stf")
        self.assertIn("stf", chub.consultas[-1])

        chub.videos_recentes(filtro_tatico="economia")
        self.assertIn("economia", chub.consultas[-1])

        chub.videos_recentes(filtro_tatico="curtos")
        self.assertIn("duration_s <= 90", chub.consultas[-1])

        chub.videos_recentes(so_com_blocos=True)
        self.assertIn("exists (select 1 from blocks b", chub.consultas[-1])


if __name__ == "__main__":
    unittest.main()
