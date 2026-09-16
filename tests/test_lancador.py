"""O .bat quebrou no Windows por ter acentos e quebras de linha LF; isto impede que volte."""

import os
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLancador(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(RAIZ, "Iniciar_Indomavel.bat"), "rb") as arquivo:
            self.conteudo = arquivo.read()

    def test_so_caracteres_ascii(self):
        estranhos = sorted({byte for byte in self.conteudo if byte > 127})
        self.assertEqual(estranhos, [], "o cmd lê .bat byte a byte; acento desalinha as linhas")

    def test_quebras_de_linha_do_windows(self):
        linhas_lf = self.conteudo.count(b"\n")
        linhas_crlf = self.conteudo.count(b"\r\n")
        self.assertGreater(linhas_lf, 0)
        self.assertEqual(linhas_lf, linhas_crlf, "toda linha precisa terminar em CRLF")


if __name__ == "__main__":
    unittest.main()
