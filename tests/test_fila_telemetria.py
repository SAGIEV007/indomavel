"""Testes para fila de exportação paralela, telemetria de progresso e anti-GC."""

import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from indomavel import config, render, servidor
from indomavel.tarefas import Fila


class TestFilaTelemetria(unittest.TestCase):
    def test_tarefa_inicia_com_campos_de_telemetria(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        tarefa = fila.adicionar_exportacao(
            "TESTE_VID1",
            10.0,
            25.0,
            "Corte Teste Telemetria",
            "1:1",
            {"cortar_topo": 60, "enquadramento_y": 0.2},
        )
        self.assertEqual(tarefa["estado"], "na_fila")
        self.assertEqual(tarefa["etapa"], "na_fila")
        self.assertEqual(tarefa["etapa_nome"], "Aguardando na fila")
        self.assertEqual(tarefa["progresso"], 0.0)
        self.assertIsNone(tarefa["eta_s"])
        self.assertIsNone(tarefa["iniciado_em"])
        self.assertEqual(tarefa["estilo"]["cortar_topo"], 60)

    def test_progresso_exportacao_atualiza_campos(self):
        fila = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        tarefa = fila.adicionar_exportacao("TESTE_VID2", 0.0, 30.0, "Corte Teste", "9:16", {})
        ident = tarefa["id"]

        fila._progresso_exportacao(
            ident,
            "Renderizando 1080p… 65%",
            progresso=65.0,
            etapa="renderizando",
            etapa_nome="Renderizando vídeo 1080p",
            eta_s=14,
        )

        atualizada = fila.ver(ident)
        self.assertEqual(atualizada["progresso"], 65.0)
        self.assertEqual(atualizada["etapa"], "renderizando")
        self.assertEqual(atualizada["etapa_nome"], "Renderizando vídeo 1080p")
        self.assertEqual(atualizada["eta_s"], 14)
        self.assertIn("65%", atualizada["mensagem"])

    def test_servidor_estilo_contem_cortar_topo(self):
        with servidor.app.test_client() as cliente:
            res = cliente.get("/api/estilo")
            self.assertEqual(res.status_code, 200)
            dados = res.get_json()
            self.assertIn("cortar_topo", dados["estilo"])
            self.assertEqual(dados["estilo"]["cortar_topo"], 0)

    def test_servidor_exportar_aceita_cortar_topo(self):
        fila_parada = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
        with patch.object(servidor, "fila", fila_parada), servidor.app.test_client() as cliente:
            payload = {
                "youtube_id": "TESTE123456",
                "inicio": 5.0,
                "fim": 15.0,
                "formato": "1:1",
                "estilo": {"cortar_topo": 75, "zoom": 1.1},
                "titulo": "Corte Anti-GC",
            }
            res = cliente.post("/api/exportar", json=payload)
            self.assertEqual(res.status_code, 202)
            dados = res.get_json()
            self.assertIn("tarefa", dados)
            tarefa = dados["tarefa"]
            self.assertEqual(tarefa["estilo"]["cortar_topo"], 75)
            self.assertEqual(tarefa["estilo"]["zoom"], 1.1)

    def test_servidor_abrir_arquivo_inexistente_retorna_404(self):
        with servidor.app.test_client() as cliente:
            res = cliente.post("/api/trechos/id_inexistente_xyz/abrir")
            self.assertEqual(res.status_code, 404)

    def test_servidor_abrir_arquivo_existente_sucesso(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            caminho_teste = os.path.join(config.PASTA_OUTPUT_CORTES, "teste_corte_valido.mp4")
            os.makedirs(config.PASTA_OUTPUT_CORTES, exist_ok=True)
            with open(caminho_teste, "wb") as f:
                f.write(b"video dummy")
            try:
                fila_parada = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=0)
                tarefa = fila_parada.adicionar_exportacao(
                    "TESTE_ABRIR", 0.0, 10.0, "Corte Abrir", "1:1", {}
                )
                ident = tarefa["id"]
                fila_parada._atualizar(ident, estado="pronta", arquivo_cortes=caminho_teste)

                with patch.object(servidor, "fila", fila_parada), patch("os.startfile") as mock_startfile:
                    with servidor.app.test_client() as cliente:
                        res = cliente.post(f"/api/trechos/{ident}/abrir")
                        self.assertEqual(res.status_code, 200)
                        self.assertTrue(res.get_json()["ok"])
                        mock_startfile.assert_called_once_with(os.path.abspath(caminho_teste))
            finally:
                if os.path.exists(caminho_teste):
                    try:
                        os.remove(caminho_teste)
                    except Exception:
                        pass

    def test_fila_executa_multiplas_tarefas_concorrentes(self):
        """Três cortes enfileirados de uma vez: dois rodam ao mesmo tempo e todos terminam."""
        import time
        rodando = []
        maximo_simultaneo = []
        trava = threading.Lock()

        def exportar_falso(fila_self, ident, tarefa, pasta):
            with trava:
                rodando.append(ident)
                maximo_simultaneo.append(len(rodando))
            time.sleep(0.2)
            with trava:
                rodando.remove(ident)
            fila_self._atualizar(ident, estado="pronta", progresso=100.0)

        with patch.object(Fila, "_exportar", autospec=True, side_effect=exportar_falso):
            fila_multi = Fila(lambda yid: [], lambda yid, ini, fim, est: [], max_trabalhadores=2)
            tarefas = [
                fila_multi.adicionar_exportacao("VID1", 0.0, 10.0, "Corte 1", "9:16", {}),
                fila_multi.adicionar_exportacao("VID2", 0.0, 10.0, "Corte 2", "1:1", {}),
                fila_multi.adicionar_exportacao("VID3", 0.0, 10.0, "Corte 3", "3:4", {}),
            ]
            # Enfileirar não espera a exportação: as três voltam na hora, na fila.
            self.assertTrue(all(t["estado"] == "na_fila" for t in tarefas))

            limite = time.time() + 5
            while time.time() < limite and any(fila_multi.ver(t["id"])["estado"] != "pronta" for t in tarefas):
                time.sleep(0.02)

            self.assertEqual([fila_multi.ver(t["id"])["estado"] for t in tarefas], ["pronta"] * 3)
            self.assertEqual(max(maximo_simultaneo), 2)

    def test_cortar_topo_limite_maximo_300(self):
        estilo = render.normalizar_estilo({"cortar_topo": 300})
        self.assertEqual(estilo["cortar_topo"], 300)
        dados = render.layout("1:1", estilo)
        filtro = render.filtro_ffmpeg(dados, estilo, 1920, 1080, com_legenda=False)
        self.assertIn("crop=1080:", filtro)
        import re
        match = re.search(r"crop=\d+:\d+:\d+:(\d+)", filtro)
        self.assertIsNotNone(match)
        corte_y = int(match.group(1))
        self.assertGreaterEqual(corte_y, 300)


if __name__ == "__main__":
    unittest.main()

