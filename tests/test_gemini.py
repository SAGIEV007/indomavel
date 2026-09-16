"""Ordem dos modelos do Gemini e mensagens curtas de falha (sem rede)."""

import time
import unittest

from indomavel import gemini


class TestOrdem(unittest.TestCase):
    def tearDown(self):
        gemini._sobrecarregado_ate.clear()

    def test_modelo_que_falhou_ha_pouco_vai_para_o_fim_mas_continua_na_lista(self):
        gemini._sobrecarregado_ate["b"] = time.time() + 600
        self.assertEqual(gemini.ordem_dos_modelos(["a", "b", "c"]), ["a", "c", "b"])

    def test_todos_de_lado_mantem_a_ordem_original(self):
        for modelo in ("a", "b"):
            gemini._sobrecarregado_ate[modelo] = time.time() + 600
        self.assertEqual(gemini.ordem_dos_modelos(["a", "b"]), ["a", "b"])

    def test_motivo_curto(self):
        self.assertIn("cota", gemini.motivo_curto(Exception("429 RESOURCE_EXHAUSTED")))
        self.assertIn("sobrecarregados", gemini.motivo_curto(Exception("503 UNAVAILABLE")))


if __name__ == "__main__":
    unittest.main()
