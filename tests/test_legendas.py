"""Legenda automática do YouTube virando frases no formato do Chub."""

import json
import os
import tempfile
import unittest

from indomavel import legendas

JSON3 = {
    "events": [
        {"tStartMs": 0, "segs": [{"utf8": "Olá,"}, {"utf8": " pessoal.", "tOffsetMs": 400}]},
        {"tStartMs": 1000, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 1200, "segs": [{"utf8": ">> Tudo", "isSpeakerChange": 1}, {"utf8": " bem", "tOffsetMs": 300}]},
        {"tStartMs": 4000, "segs": [{"utf8": "[música]"}]},
        {"tStartMs": 5000, "segs": [{"utf8": "depois"}, {"utf8": " da", "tOffsetMs": 200}, {"utf8": " pausa?", "tOffsetMs": 400}]},
    ]
}


class TestLegendas(unittest.TestCase):
    def test_id_do_link(self):
        casos = {
            "https://www.youtube.com/watch?v=DxN0m8JN94w&t=10s": "DxN0m8JN94w",
            "https://youtu.be/DxN0m8JN94w?si=abc": "DxN0m8JN94w",
            "https://www.youtube.com/live/DxN0m8JN94w?feature=share": "DxN0m8JN94w",
            "https://www.youtube.com/shorts/DxN0m8JN94w": "DxN0m8JN94w",
            "  DxN0m8JN94w ": "DxN0m8JN94w",
            "https://www.google.com/": None,
            "": None,
        }
        for texto, esperado in casos.items():
            self.assertEqual(legendas.id_do_link(texto), esperado, texto)

    def test_frases_quebram_como_no_chub(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "legenda.pt-orig.json3")
            with open(caminho, "w", encoding="utf-8") as arquivo:
                json.dump(JSON3, arquivo)
            palavras = legendas.palavras_do_json3(caminho)
        frases = legendas.frases_das_palavras(palavras)
        self.assertEqual([f["texto"] for f in frases], ["Olá, pessoal.", "Tudo bem", "depois da pausa?"])
        self.assertEqual([f["inicio"] for f in frases], [0.0, 1.2, 5.0])
        self.assertEqual([f["fim"] for f in frases], [1.2, 5.0, 7.0])
        self.assertEqual([f["i"] for f in frases], [0, 1, 2])
        self.assertTrue(frases[1]["troca"])
        self.assertFalse(frases[2]["troca"])


if __name__ == "__main__":
    unittest.main()
