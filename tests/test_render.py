"""Layout, moldura, legenda ASS e filtro do vídeo exportado."""

import os
import tempfile
import unittest

from indomavel import legenda, render


class TestEstilo(unittest.TestCase):
    def test_valores_fora_do_limite_e_cores_invalidas_voltam_ao_padrao(self):
        estilo = render.normalizar_estilo({"tamanho_legenda": 9999, "cor_destaque": "amarelo", "fonte_legenda": "comic",
                                           "zoom": 0.2, "card": 0, "chave_desconhecida": 1})
        self.assertEqual(estilo["tamanho_legenda"], 220)
        self.assertEqual(estilo["cor_destaque"], render.ESTILO_PADRAO["cor_destaque"])
        self.assertEqual(estilo["fonte_legenda"], "bebas")
        self.assertEqual(estilo["zoom"], 1.0)
        self.assertFalse(estilo["card"])
        self.assertNotIn("chave_desconhecida", estilo)


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.estilo = render.normalizar_estilo({"tag": "EM ALTA!", "headline": "Renan Santos propõe transformar cada eleitor em multiplicador para chegar ao segundo turno"})

    def test_card_empurra_o_video_para_baixo(self):
        dados = render.layout("4:5", self.estilo)
        self.assertEqual((dados["largura"], dados["altura"]), (1080, 1350))
        altura_card = dados["card"]["altura"]
        self.assertGreater(altura_card, 150)
        self.assertEqual(dados["video"], {"x": 0, "y": altura_card, "w": 1080, "h": 1350 - altura_card})
        self.assertTrue(altura_card < dados["legenda"]["y"] < 1350)
        linhas = [i for i in dados["card"]["itens"] if i["tipo"] == "headline"]
        self.assertTrue(1 <= len(linhas) <= 4)

    def test_sem_card_o_video_ocupa_tudo(self):
        dados = render.layout("9:16", render.normalizar_estilo({"card": False, "headline": "x"}))
        self.assertEqual(dados["card"]["altura"], 0)
        self.assertEqual(dados["video"]["h"], 1920)

    def test_moldura_tem_card_branco_e_video_transparente(self):
        dados = render.layout("1:1", self.estilo)
        imagem = render.desenhar_moldura(dados)
        self.assertEqual(imagem.size, (1080, 1080))
        self.assertEqual(imagem.getpixel((3, 3)), (255, 255, 255, 255))
        meio_do_video = (540, dados["card"]["altura"] + dados["video"]["h"] // 2)
        self.assertEqual(imagem.getpixel(meio_do_video)[3], 0)


class TestLegendaExportada(unittest.TestCase):
    def test_trechos_curtos_com_uma_palavra_em_destaque(self):
        palavras = [{"t": 10.0 + i * 0.4, "p": p, "troca": False} for i, p in enumerate(
            "Cada um dos meus eleitores é um multiplicador.".split())]
        trechos = legenda.trechos_de_palavras(palavras, 10.0, 20.0)
        self.assertTrue(all(len(t["palavras"]) <= 3 for t in trechos))
        juntos = [p for t in trechos for p in t["palavras"]]
        self.assertEqual(juntos, "Cada um dos meus eleitores é um multiplicador.".split())
        ultimo = trechos[-1]
        self.assertEqual(ultimo["palavras"][ultimo["destaque"]], "multiplicador.")
        self.assertTrue(all(10.0 <= t["inicio"] < t["fim"] <= 20.0 for t in trechos))

    def test_ass_tem_estilo_posicao_e_destaque(self):
        estilo = render.normalizar_estilo({"headline": "Teste"})
        dados = render.layout("9:16", estilo)
        trechos = [{"inicio": 10.5, "fim": 11.4, "palavras": ["foi", "trocada", "por"], "destaque": 1}]
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "legenda.ass")
            render.escrever_ass(caminho, dados, estilo, trechos, 10.0, 30.0)
            with open(caminho, encoding="utf-8") as arquivo:
                texto = arquivo.read()
        self.assertIn("Style: Legenda,Bebas Neue,", texto)
        self.assertIn("Dialogue: 0,0:00:00.50,0:00:01.40,Legenda", texto)
        self.assertIn(f"\\pos(540,{dados['legenda']['y']})", texto)
        self.assertIn("FOI {\\c&H0AD6FF&}TROCADA{\\c&HFFFFFF&} POR", texto)

    def test_filtro_encaixa_o_video_abaixo_do_card(self):
        estilo = render.normalizar_estilo({"headline": "Teste"})
        dados = render.layout("4:5", estilo)
        filtro = render.filtro_ffmpeg(dados, estilo, 1920, 1080, com_legenda=True)
        self.assertIn(f"pad=1080:1350:0:{dados['card']['altura']}:white", filtro)
        self.assertIn(f"crop=1080:{dados['video']['h']}:", filtro)
        self.assertTrue(filtro.endswith("ass=legenda.ass:fontsdir=fontes[saida]"))


if __name__ == "__main__":
    unittest.main()
