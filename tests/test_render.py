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

    def test_card_empurra_o_video_para_baixo_em_3_4(self):
        dados = render.layout("3:4", self.estilo)
        self.assertEqual((dados["largura"], dados["altura"]), (1080, 1440))
        altura_card = dados["card"]["altura"]
        self.assertGreater(altura_card, 150)
        self.assertEqual(dados["video"], {"x": 0, "y": altura_card, "w": 1080, "h": 1440 - altura_card})
        self.assertTrue(altura_card < dados["legenda"]["y"] < 1440)

    def test_sem_card_o_video_ocupa_tudo(self):
        dados = render.layout("9:16", render.normalizar_estilo({"card": False, "headline": "x"}))
        self.assertEqual(dados["card"]["altura"], 0)
        self.assertEqual(dados["video"]["h"], 1920)

    def test_16_9_limpo_sem_card(self):
        dados = render.layout("16:9", render.normalizar_estilo({"card": False, "headline": ""}))
        self.assertEqual(dados["card"]["altura"], 0)
        self.assertEqual(dados["video"], {"x": 0, "y": 0, "w": 1920, "h": 1080})

    def test_16_9_ignora_card_mesmo_quando_ativado(self):
        dados = render.layout("16:9", render.normalizar_estilo({"card": True, "tag": "EM ALTA!", "headline": "Headline Longa"}))
        self.assertEqual(dados["card"]["altura"], 0)
        self.assertEqual(dados["card"]["itens"], [])
        self.assertEqual(dados["video"], {"x": 0, "y": 0, "w": 1920, "h": 1080})

    def test_encoder_video_retorna_lista_valida(self):
        enc = render.encoder_video()
        self.assertIsInstance(enc, list)
        self.assertIn("-c:v", enc)
        self.assertTrue("libx264" in enc or "h264_nvenc" in enc)

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

    def test_cortar_topo_normalizacao_e_limites(self):
        padrao = render.normalizar_estilo({})
        self.assertEqual(padrao["cortar_topo"], 0)
        negativo = render.normalizar_estilo({"cortar_topo": -50})
        self.assertEqual(negativo["cortar_topo"], 0)
        excesso = render.normalizar_estilo({"cortar_topo": 999})
        self.assertEqual(excesso["cortar_topo"], 300)
        valido = render.normalizar_estilo({"cortar_topo": 85})
        self.assertEqual(valido["cortar_topo"], 85)

    def test_filtro_com_cortar_topo_mascara_gc_superior(self):
        estilo = render.normalizar_estilo({"headline": "Teste Anti GC", "cortar_topo": 80})
        dados = render.layout("1:1", estilo)
        filtro = render.filtro_ffmpeg(dados, estilo, 1920, 1080, com_legenda=False)
        # O filtro crop deve iniciar em corte_y >= 80 para expurgar a tarja/GC do topo
        self.assertIn("crop=1080:", filtro)
        import re
        match = re.search(r"crop=\d+:\d+:\d+:(\d+)", filtro)
        self.assertIsNotNone(match)
        corte_y = int(match.group(1))
        self.assertGreaterEqual(corte_y, 80)

    def test_notificar_telemetria_retrocompativel(self):
        chamado_simples = []
        render._notificar(lambda msg: chamado_simples.append(msg), "Teste Simples")
        self.assertEqual(chamado_simples, ["Teste Simples"])

        chamado_rico = {}
        def callback_rico(msg, progresso=None, etapa=None, etapa_nome=None, eta_s=None):
            chamado_rico.update({"msg": msg, "progresso": progresso, "etapa": etapa, "etapa_nome": etapa_nome, "eta_s": eta_s})

        render._notificar(callback_rico, "Renderizando", progresso=75.5, etapa="renderizando", etapa_nome="Render 1080p", eta_s=12)
        self.assertEqual(chamado_rico["progresso"], 75.5)
        self.assertEqual(chamado_rico["etapa"], "renderizando")
        self.assertEqual(chamado_rico["eta_s"], 12)

    def test_filtro_limites_geometria_completa(self):
        import re
        for formato in ["9:16", "3:4", "4:5", "1:1", "16:9"]:
            for cortar_topo in [0, 50, 120, 250]:
                for eq_x in [-1.0, 0.0, 1.0]:
                    for eq_y in [-1.0, 0.0, 1.0]:
                        for zoom in [1.0, 1.4, 2.0]:
                            estilo = render.normalizar_estilo({
                                "cortar_topo": cortar_topo,
                                "enquadramento_x": eq_x,
                                "enquadramento_y": eq_y,
                                "zoom": zoom,
                                "card": formato != "16:9",
                                "headline": "Headline Teste",
                            })
                            dados = render.layout(formato, estilo)
                            filtro = render.filtro_ffmpeg(dados, estilo, 1920, 1080, com_legenda=False)
                            # scale=L:A,crop=W:H:X:Y
                            match_scale = re.search(r"scale=(\d+):(\d+)", filtro)
                            match_crop = re.search(r"crop=(\d+):(\d+):(\d+):(\d+)", filtro)
                            self.assertIsNotNone(match_scale)
                            self.assertIsNotNone(match_crop)
                            largura_esc, altura_esc = int(match_scale.group(1)), int(match_scale.group(2))
                            w, h, x, y = (int(match_crop.group(i)) for i in range(1, 5))
                            # Garante que as dimensões do crop são válidas e dentro dos limites da escala
                            self.assertEqual(w, dados["video"]["w"])
                            self.assertEqual(h, dados["video"]["h"])
                            self.assertGreaterEqual(x, 0)
                            self.assertGreaterEqual(y, round(cortar_topo * dados["escala"]))
                            self.assertLessEqual(x + w, largura_esc)
                            self.assertLessEqual(y + h, altura_esc)


if __name__ == "__main__":
    unittest.main()
