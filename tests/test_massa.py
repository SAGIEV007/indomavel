"""Escolha dos melhores momentos para baixar e editar em massa, e a versão usada pelo .bat."""

import re
import unittest

from indomavel import massa, versao


def bloco(ident, potencial, pronto=False):
    return {"id": ident, "potencial": potencial, "pronto": pronto}


class TestEscolha(unittest.TestCase):
    def test_melhores_momentos_pelo_potencial(self):
        blocos = [bloco("a", 40), bloco("b", 90), bloco("c", 70, pronto=True), bloco("d", 10, pronto=True)]
        self.assertEqual([b["id"] for b in massa.escolher_blocos(blocos, 2, so_prontos=False)], ["b", "c"])
        self.assertEqual([b["id"] for b in massa.escolher_blocos(blocos, 5, so_prontos=True)], ["c", "d"])

    def test_um_corte_por_bloco_e_limite(self):
        cortes = [{"bloco_id": "x"}, {"bloco_id": "x"}, {"bloco_id": "y"}, {"bloco_id": "z"}]
        self.assertEqual([c["bloco_id"] for c in massa.um_corte_por_bloco(cortes, 2)], ["x", "y"])

    def test_pedido_invalido_e_recusado(self):
        robo = massa.Massa(obter_video=None, obter_blocos=None, obter_frases=None, fila=None)
        with self.assertRaises(ValueError):
            robo.criar("publicar", ["DxN0m8JN94w"], 3, False, "4:5")
        with self.assertRaises(ValueError):
            robo.criar("baixar", ["link-quebrado"], 3, False, "4:5")
        with self.assertRaises(ValueError):
            robo.criar("editar", ["DxN0m8JN94w"], 3, False, "7:3")


class TestVersao(unittest.TestCase):
    def test_versao_e_estavel(self):
        self.assertRegex(versao.VERSAO, re.compile(r"^[0-9a-f]{12}$"))
        self.assertEqual(versao.calcular(), versao.calcular())


if __name__ == "__main__":
    unittest.main()
