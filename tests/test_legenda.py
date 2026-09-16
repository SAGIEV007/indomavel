"""Legenda .srt de uma linha a partir das frases do Chub."""

import unittest

from indomavel import legenda

FRASES = [
    {"inicio": 10.0, "fim": 14.0, "texto": "Mas nós vamos despertar porque o que eu vi nas últimas viagens me deu muita energia."},
    {"inicio": 14.0, "fim": 16.0, "texto": "Os jovens já estão virando seus pais."},
    {"inicio": 30.0, "fim": 31.0, "texto": "Fora do trecho."},
]


class TestLegenda(unittest.TestCase):
    def test_pedacos_cabem_numa_linha_sem_perder_palavras(self):
        texto = FRASES[0]["texto"]
        partes = legenda.pedacos(texto)
        self.assertGreater(len(partes), 1)
        self.assertTrue(all(len(parte) <= legenda.MAX_CARACTERES for parte in partes))
        self.assertEqual(" ".join(partes).split(), texto.split())

    def test_deixas_ficam_dentro_do_trecho_e_comecam_do_zero(self):
        deixas = legenda.deixas(FRASES, 12.0, 15.0)
        self.assertTrue(deixas)
        self.assertGreaterEqual(deixas[0][0], 0.0)
        self.assertLessEqual(deixas[-1][1], 3.0)
        self.assertFalse(any("Fora" in texto for _, _, texto in deixas))

    def test_srt_numera_e_marca_os_tempos(self):
        texto = legenda.srt(FRASES, 10.0, 16.0)
        self.assertTrue(texto.startswith("1\n00:00:00,000 --> 00:00:"))
        self.assertIn("\n2\n", texto)


class TestSrtSeparado(unittest.TestCase):
    def test_srt_dos_trechos_curtos_relativo_ao_corte(self):
        trechos = [
            {"inicio": 100.5, "fim": 101.4, "palavras": ["foi", "trocada,", "por"], "destaque": 1},
            {"inicio": 101.4, "fim": 102.0, "palavras": ["crime", "organizado."], "destaque": 0},
        ]
        texto = legenda.srt_de_trechos(trechos, 100.0, 110.0, maiusculas=True)
        self.assertEqual(texto, "1\n00:00:00,500 --> 00:00:01,400\nFOI TROCADA POR\n\n2\n00:00:01,400 --> 00:00:02,000\nCRIME ORGANIZADO\n")


if __name__ == "__main__":
    unittest.main()
