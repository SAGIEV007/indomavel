"""Testes para a matriz de exportação flexível e exportação em lote."""

import os
import tempfile
import unittest
from unittest.mock import patch

from indomavel import servidor, tarefas
from indomavel.tarefas import Fila


class TestExportacaoLote(unittest.TestCase):
    def test_adicionar_exportacao_lote_filtra_cortes_validos(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        blocos = [
            {"inicio": 0.0, "fim": 60.0, "titulo": "Corte Ideal 1", "duracao": 60.0},
            {"inicio": 100.0, "fim": 150.0, "titulo": "Corte Ideal 2", "duracao": 50.0},
            {"inicio": 200.0, "fim": 400.0, "titulo": "Bloco Gigante", "duracao": 200.0},
            {"inicio": 500.0, "fim": 510.0, "titulo": "Micro Bloco", "duracao": 10.0},
            {"inicio": 600.0, "fim": 675.0, "titulo": "Corte Perfeito", "duracao": 75.0},
        ]
        tarefa = fila.adicionar_exportacao_lote(
            "TESTE123456",
            "Vídeo de Teste",
            blocos,
            formato="9:16",
            estilo={"tag": "EM ALTA!", "headline": "Teste"},
            com_legenda=True,
            com_headline=True,
            manter_bruto=False,
        )
        self.assertEqual(tarefa["tipo"], "exportacao_lote")
        self.assertEqual(tarefa["formato"], "9:16")
        self.assertTrue(tarefa["com_legenda"])
        self.assertTrue(tarefa["com_headline"])
        self.assertFalse(tarefa["manter_bruto"])

        # Deve filtrar apenas os 3 blocos válidos (entre 20s e 90s)
        titulos_filtrados = [b["titulo"] for b in tarefa["blocos"]]
        self.assertEqual(titulos_filtrados, ["Corte Ideal 1", "Corte Ideal 2", "Corte Perfeito"])

    def test_adicionar_exportacao_lote_com_toggles_desligados(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        blocos = [{"inicio": 0.0, "fim": 45.0, "titulo": "Corte Sem Legenda Sem Headline"}]
        tarefa = fila.adicionar_exportacao_lote(
            "TESTE654321",
            "Vídeo Sem Adicionais",
            blocos,
            formato="16:9",
            estilo={},
            com_legenda=False,
            com_headline=False,
            manter_bruto=True,
        )
        self.assertFalse(tarefa["com_legenda"])
        self.assertFalse(tarefa["com_headline"])
        self.assertTrue(tarefa["manter_bruto"])
        self.assertEqual(tarefa["formato"], "16:9")

    def test_adicionar_exportacao_lote_prioriza_faixa_ideal(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        blocos = [
            {"inicio": 0.0, "fim": 25.0, "titulo": "Corte de 25s", "duracao": 25.0},
            {"inicio": 50.0, "fim": 110.0, "titulo": "Corte Ideal 60s", "duracao": 60.0},
            {"inicio": 150.0, "fim": 235.0, "titulo": "Corte de 85s", "duracao": 85.0},
            {"inicio": 300.0, "fim": 350.0, "titulo": "Corte Ideal 50s", "duracao": 50.0},
        ]
        tarefa = fila.adicionar_exportacao_lote("TEST999", "Vídeo Sweet Spot", blocos, "9:16", {})
        titulos = [b["titulo"] for b in tarefa["blocos"]]
        # Os dois da faixa ideal 45-75s devem vir antes dos de 25s e 85s
        self.assertEqual(titulos[:2], ["Corte Ideal 60s", "Corte Ideal 50s"])
        self.assertEqual(titulos[2:], ["Corte de 25s", "Corte de 85s"])

    def test_adicionar_exportacao_com_trechos_customizados(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        trechos = [{"inicio": 1.0, "fim": 2.0, "palavras": ["teste", "editado"], "destaque": 0}]
        tarefa = fila.adicionar_exportacao(
            "TEST777", 0.0, 10.0, "Corte Custom", "9:16", {}, trechos=trechos
        )
        self.assertEqual(tarefa["trechos"], trechos)

    def test_lote_com_marca_por_padrao(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        blocos = [{"inicio": 0.0, "fim": 45.0, "titulo": "Corte"}]
        tarefa = fila.adicionar_exportacao_lote("TESTEMARCA1", "Vídeo", blocos, "9:16", {})
        self.assertTrue(tarefa["com_marca"])
        self.assertTrue(tarefa["estilo"]["rodape"])

    def test_lote_sem_marca_desliga_rodape(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        blocos = [{"inicio": 0.0, "fim": 45.0, "titulo": "Corte"}]
        tarefa = fila.adicionar_exportacao_lote("TESTEMARCA2", "Vídeo", blocos, "9:16", {"rodape": True}, com_marca=False)
        self.assertFalse(tarefa["com_marca"])
        self.assertFalse(tarefa["estilo"]["rodape"])

    def _rodar_lote(self, com_marca):
        """Roda _exportar_lote sem baixar nem renderizar: devolve os estilos e pastas que chegariam ao render."""
        chamadas = []

        def exportar_falso(youtube_id, inicio, fim, formato, estilo, trechos, pasta_saida, titulo, ao_progredir=None):
            chamadas.append({"estilo": dict(estilo), "pasta": pasta_saida})
            os.makedirs(pasta_saida, exist_ok=True)
            caminho = os.path.join(pasta_saida, titulo + ".mp4")
            with open(caminho, "wb") as arquivo:
                arquivo.write(b"mp4")
            return caminho

        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        blocos = [{"inicio": 0.0, "fim": 45.0, "titulo": "Corte A"}, {"inicio": 60.0, "fim": 110.0, "titulo": "Corte B"}]
        tarefa = fila.adicionar_exportacao_lote("TESTEMARCA3", "Vídeo Marca", blocos, "9:16", {}, com_legenda=False,
                                                com_headline=False, com_marca=com_marca)
        with tempfile.TemporaryDirectory() as temporaria:
            with patch.object(tarefas.config, "PASTA_OUTPUT_CORTES", os.path.join(temporaria, "output")),                     patch.object(tarefas.render, "exportar", side_effect=exportar_falso):
                fila._exportar_lote(tarefa["id"], fila.ver(tarefa["id"]), os.path.join(temporaria, "downloads"))
            final = fila.ver(tarefa["id"])
        return chamadas, final

    def test_exportar_lote_sem_marca_renderiza_sem_rodape_em_pasta_propria(self):
        chamadas, final = self._rodar_lote(com_marca=False)
        self.assertEqual(len(chamadas), 2)
        self.assertTrue(all(c["estilo"]["rodape"] is False for c in chamadas))
        self.assertTrue(all(os.path.basename(c["pasta"]) == "sem marca" for c in chamadas))
        self.assertEqual(final["estado"], "pronta")
        self.assertIn("sem marca d'água", final["mensagem"])

    def test_exportar_lote_com_marca_renderiza_com_rodape(self):
        chamadas, final = self._rodar_lote(com_marca=True)
        self.assertTrue(all(c["estilo"]["rodape"] is True for c in chamadas))
        self.assertTrue(all(os.path.basename(c["pasta"]) == "com marca" for c in chamadas))
        self.assertIn("com marca d'água", final["mensagem"])

    def test_exportar_lote_sem_nenhum_corte_falha(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        tarefa = fila.adicionar_exportacao_lote("TESTEMARCA4", "Vídeo", [{"inicio": 0.0, "fim": 45.0, "titulo": "A"}], "9:16", {},
                                                com_legenda=False, com_headline=False)
        with tempfile.TemporaryDirectory() as temporaria:
            with patch.object(tarefas.config, "PASTA_OUTPUT_CORTES", temporaria),                     patch.object(tarefas.render, "exportar", side_effect=RuntimeError("ffmpeg quebrou")):
                with self.assertRaises(RuntimeError):
                    fila._exportar_lote(tarefa["id"], fila.ver(tarefa["id"]), os.path.join(temporaria, "d"))

    def test_servidor_repassa_com_marca(self):
        blocos = [{"inicio": 0.0, "fim": 45.0, "titulo": "A"}]
        with patch.object(servidor, "blocos_do_video", return_value=blocos),                 patch.object(servidor, "video_resumido", return_value={"titulo": "Vídeo"}),                 patch.object(servidor.fila, "adicionar_exportacao_lote", return_value={"id": "x"}) as adicionar:
            with servidor.app.test_client() as cliente:
                resposta = cliente.post("/api/videos/abcdefghijk/exportar-lote", json={"formato": "9:16", "com_marca": False})
                self.assertEqual(resposta.status_code, 202)
                self.assertIs(adicionar.call_args.kwargs["com_marca"], False)
                cliente.post("/api/videos/abcdefghijk/exportar-lote", json={"formato": "9:16"})
                self.assertIs(adicionar.call_args.kwargs["com_marca"], True)


if __name__ == "__main__":
    unittest.main()

