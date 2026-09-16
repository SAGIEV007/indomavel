"""Conexão com o Chub contra um servidor falso: toda falha precisa virar ChubErro."""

import http.server
import json
import threading
import unittest

from indomavel.chub import Chub, ChubErro, potencial, texto_sql

LINHAS = {"rows": [{"agora": "2026-09-15T00:00:00Z", "blocos_ativos": "7"}], "rowCount": 1}


class _ChubFalso(http.server.BaseHTTPRequestHandler):
    modo = "json"
    chamadas = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        tipo = type(self)
        tipo.chamadas += 1
        pedido = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if tipo.modo == "404":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        if tipo.modo == "erro_ferramenta":
            resultado = {"isError": True, "content": [{"type": "text", "text": "query inválida"}]}
        else:
            resultado = {"content": [{"type": "text", "text": json.dumps(LINHAS)}]}
        corpo = json.dumps({"jsonrpc": "2.0", "id": pedido["id"], "result": resultado}).encode()
        self.send_response(200)
        if tipo.modo == "stream":
            self.send_header("Content-Type", "text/event-stream")
            corpo = b"event: message\ndata: " + corpo + b"\n\n"
        else:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


class TestChub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ChubFalso)
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/mcp/chave" % cls.servidor.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def setUp(self):
        _ChubFalso.modo = "json"
        _ChubFalso.chamadas = 0

    def test_resposta_json(self):
        self.assertEqual(Chub(self.url).agora()["blocos_ativos"], "7")

    def test_resposta_em_stream(self):
        _ChubFalso.modo = "stream"
        self.assertEqual(Chub(self.url).agora()["blocos_ativos"], "7")

    def test_chave_recusada_vira_erro(self):
        _ChubFalso.modo = "404"
        with self.assertRaises(ChubErro) as contexto:
            Chub(self.url).sql("select 1")
        self.assertIn("HTTP 404", str(contexto.exception))

    def test_erro_da_ferramenta_vira_erro(self):
        _ChubFalso.modo = "erro_ferramenta"
        with self.assertRaises(ChubErro) as contexto:
            Chub(self.url).sql("select 1")
        self.assertIn("query inválida", str(contexto.exception))

    def test_servidor_fora_do_ar_vira_erro(self):
        with self.assertRaises(ChubErro):
            Chub("http://127.0.0.1:9/mcp/x", tempo_limite=3).sql("select 1")

    def test_cache_respeita_a_validade(self):
        com_cache = Chub(self.url, validade_cache=60)
        com_cache.sql("select 1")
        com_cache.sql("select 1")
        self.assertEqual(_ChubFalso.chamadas, 1)
        sem_cache = Chub(self.url, validade_cache=0)
        sem_cache.sql("select 1")
        sem_cache.sql("select 1")
        self.assertEqual(_ChubFalso.chamadas, 3)

    def test_texto_sql_escapa_aspas(self):
        self.assertEqual(texto_sql("d'Ávila"), "'d''Ávila'")

    def test_potencial_usa_os_pesos_da_pauta(self):
        self.assertEqual(potencial({"densityRank": 100, "selfContainedRank": 100, "possibleCuts": 4}), 100.0)
        self.assertEqual(potencial({"densityRank": 0, "selfContainedRank": 0, "possibleCuts": 0}), 0.0)


if __name__ == "__main__":
    unittest.main()
