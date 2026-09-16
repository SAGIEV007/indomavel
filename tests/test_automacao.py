"""Escolha dos blocos e validação dos cortes da automação."""

import unittest

from indomavel import automacao

VIDEO = {"youtube_id": "DxN0m8JN94w", "titulo": "Live"}


def bloco(ident, inicial, final, pronto=False, renan=True, cortes=2, potencial=50.0):
    return {"id": ident, "titulo": f"bloco {ident}", "resumo": "", "frase_inicial": inicial, "frase_final": final,
            "inicio": inicial * 3.0, "fim": (final + 1) * 3.0, "duracao": (final + 1 - inicial) * 3.0, "pronto": pronto,
            "renan_falando": renan, "cortes_possiveis": cortes, "potencial": potencial}


FRASES = [{"i": i, "inicio": i * 3.0, "fim": (i + 1) * 3.0, "texto": f"frase {i}.", "troca": False} for i in range(200)]


class TestSelecao(unittest.TestCase):
    def test_prontos_primeiro_depois_longos_do_renan(self):
        blocos = [
            bloco("longo-fraco", 0, 59, potencial=40),
            bloco("pronto", 60, 75, pronto=True, potencial=30),
            bloco("longo-forte", 80, 159, potencial=90),
            bloco("longo-sem-renan", 160, 199, renan=False, potencial=99),
            bloco("ja-feito", 0, 10, pronto=True),
        ]
        escolhidos = automacao.selecionar_blocos(blocos, {"ja-feito"})
        self.assertEqual([b["id"] for b in escolhidos], ["pronto", "longo-forte", "longo-fraco"])


class TestValidacao(unittest.TestCase):
    def setUp(self):
        self.blocos = [bloco("a", 0, 59), bloco("b", 60, 75, pronto=True)]

    def test_aceita_so_cortes_dentro_do_bloco_com_duracao_boa_e_sem_sobreposicao(self):
        brutos = [
            {"bloco": "a", "frase_inicial": 2, "frase_final": 15, "tag": "em alta!", "headline": "Renan explica", "motivo": "ok"},
            {"bloco": "a", "frase_inicial": 10, "frase_final": 20, "tag": "X!", "headline": "sobreposto", "motivo": ""},
            {"bloco": "a", "frase_inicial": 30, "frase_final": 31, "tag": "X!", "headline": "curto demais", "motivo": ""},
            {"bloco": "a", "frase_inicial": 50, "frase_final": 70, "tag": "X!", "headline": "sai do bloco", "motivo": ""},
            {"bloco": "zzz", "frase_inicial": 1, "frase_final": 9, "tag": "X!", "headline": "bloco inexistente", "motivo": ""},
            {"bloco": "b", "frase_inicial": 61, "frase_final": 74, "tag": "VIRALIZOU!!", "headline": "pronto ajustado", "motivo": ""},
        ]
        cortes = automacao.validar_cortes(brutos, VIDEO, self.blocos, FRASES)
        self.assertEqual([c["headline"] for c in cortes], ["Renan explica", "pronto ajustado"])
        self.assertEqual(cortes[0]["tag"], "EM ALTA!")
        self.assertEqual((cortes[0]["inicio"], cortes[0]["fim"]), (6.0, 48.0))
        self.assertEqual([c["origem"] for c in cortes], ["dentro_de_bloco", "bloco_pronto"])

    def test_sem_gemini_bloco_longo_vira_corte_em_volta_do_momento_forte(self):
        longo = {**bloco("a", 0, 59), "destaques": [{"frase": 10, "texto": "frase 10.", "motivo": "forte"}]}
        cortes = automacao.cortes_sem_gemini(VIDEO, [longo, bloco("b", 60, 75, pronto=True)], FRASES)
        self.assertEqual([c["origem"] for c in cortes], ["dentro_de_bloco", "bloco_pronto"])
        self.assertEqual((cortes[0]["inicio"], cortes[0]["fim"]), (27.0, 54.0))
        self.assertEqual(cortes[0]["headline"], "bloco a")

    def test_sem_gemini_sobram_os_blocos_prontos_com_o_titulo(self):
        cortes = automacao.cortes_sem_gemini(VIDEO, self.blocos)
        self.assertEqual(len(cortes), 1)
        self.assertEqual(cortes[0]["headline"], "bloco b")
        self.assertEqual((cortes[0]["inicio"], cortes[0]["fim"]), (180.0, 228.0))


class TestPedido(unittest.TestCase):
    def test_pedido_leva_tema_e_limite_de_cortes(self):
        longo = {**bloco("a", 0, 59), "categoria": "Segurança Pública", "temas": ["PCC", "facções"]}
        pronto = bloco("b", 60, 75, pronto=True)
        texto = automacao.conteudo_do_pedido(VIDEO, [longo, pronto], FRASES, cortes_por_bloco=1)
        self.assertIn("BLOCO a (ESCOLHER, até 1 corte(s))", texto)
        self.assertIn("Categoria: Segurança Pública", texto)
        self.assertIn("Temas: PCC, facções", texto)
        self.assertIn("BLOCO b (PRONTO)", texto)


class TestDecisao(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile

        self.temporaria = tempfile.TemporaryDirectory()
        self.pasta_original, self.downloads_original = automacao.PASTA, automacao.config.PASTA_DOWNLOADS
        automacao.PASTA = os.path.join(self.temporaria.name, "automacao")
        automacao.config.PASTA_DOWNLOADS = os.path.join(self.temporaria.name, "downloads")
        os.makedirs(os.path.join(automacao.config.PASTA_DOWNLOADS, "automatico"))
        self.arquivo = os.path.join(automacao.config.PASTA_DOWNLOADS, "automatico", "corte.mp4")
        with open(self.arquivo, "wb") as saida:
            saida.write(b"mp4")
        self.robo = automacao.Automacao(chub=None, fila=None, intervalo_vigia_s=3600)

    def tearDown(self):
        automacao.PASTA, automacao.config.PASTA_DOWNLOADS = self.pasta_original, self.downloads_original
        self.temporaria.cleanup()

    def test_aprovar_move_o_arquivo_e_registra_o_veredito(self):
        import os

        corte = automacao.cortes_sem_gemini(VIDEO, [bloco("b", 60, 75, pronto=True)])[0]
        corte.update(estado="para_revisar", arquivo=self.arquivo)
        self.robo._guardar_corte(corte)
        aprovado = self.robo.decidir(corte["id"], "aprovar")
        self.assertEqual(aprovado["estado"], "aprovado")
        self.assertIn(os.path.join("downloads", "aprovados"), aprovado["arquivo"])
        self.assertTrue(os.path.exists(aprovado["arquivo"]))
        self.assertFalse(os.path.exists(self.arquivo))
        with open(os.path.join(automacao.PASTA, "vereditos.jsonl"), encoding="utf-8") as registro:
            self.assertIn('"acao": "aprovar"', registro.read())

    def test_descartar_guarda_o_motivo_e_configuracao_invalida_e_limitada(self):
        corte = automacao.cortes_sem_gemini(VIDEO, [bloco("b", 60, 75, pronto=True)])[0]
        self.robo._guardar_corte(corte)
        descartado = self.robo.decidir(corte["id"], "descartar", "começa mal")
        self.assertEqual((descartado["estado"], descartado["motivo_descarte"]), ("descartado", "começa mal"))
        configuracao = self.robo.salvar_config({"dias": 99, "max_cortes_por_rodada": 0, "formato": "7:3", "intervalo_min": 7})
        self.assertEqual((configuracao["dias"], configuracao["max_cortes_por_rodada"]), (14, 1))
        self.assertEqual((configuracao["formato"], configuracao["intervalo_min"]), ("4:5", 60))


if __name__ == "__main__":
    unittest.main()
