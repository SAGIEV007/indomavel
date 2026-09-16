"""Todos os blocos sem edição: escolha do formato de maior qualidade, quadros-chave e comando de recorte sem perda."""

import unittest

from indomavel import recorte, tarefas, youtube


def formato(ident, altura=None, vcodec="none", acodec="none", fps=30, tbr=1000, abr=None):
    return {"format_id": ident, "height": altura, "vcodec": vcodec, "acodec": acodec, "fps": fps, "tbr": tbr, "abr": abr}


LIVE_1080 = [
    formato("248", 1080, "vp9"), formato("399", 1080, "av01.0.08M.08"), formato("137", 1080, "avc1.640028"),
    formato("136", 720, "avc1.4d401f"), formato("140", acodec="mp4a.40.2", abr=129), formato("251", acodec="opus", abr=110),
    formato("18", 360, "avc1.42001E", "mp4a.40.2"),
]


class TestFormato(unittest.TestCase):
    def test_na_mesma_resolucao_prefere_h264_e_audio_aac(self):
        self.assertEqual(youtube.escolher_formatos(LIVE_1080), ("137", "140"))

    def test_resolucao_maior_vence_mesmo_sem_h264(self):
        com_4k = LIVE_1080 + [formato("313", 2160, "vp9", tbr=9000)]
        self.assertEqual(youtube.escolher_formatos(com_4k), ("313", "140"))

    def test_sem_aac_usa_o_melhor_audio(self):
        sem_aac = [f for f in LIVE_1080 if f["format_id"] != "140"]
        self.assertEqual(youtube.escolher_formatos(sem_aac), ("137", "251"))


class TestRecorte(unittest.TestCase):
    def test_le_os_quadros_chave_do_ffprobe(self):
        self.assertEqual(recorte.ler_quadros_chave("601.000000,\n594.000000\n\n600.433333,\nlixo\n"), [594.0, 600.433333, 601.0])

    def test_comando_copia_sem_recomprimir_a_partir_do_quadro_chave(self):
        comando = recorte.comando_recorte("video.mp4", 600.433333, 725.5, "saida.mp4")
        self.assertEqual(comando[comando.index("-ss") + 1], "600.434")
        self.assertEqual(comando[comando.index("-to") + 1], "725.500")
        self.assertLess(comando.index("-to"), comando.index("-i"))
        self.assertEqual(comando[comando.index("-c") + 1], "copy")

    def test_nome_do_bloco_tem_numero_titulo_e_tempo(self):
        bloco = {"inicio": 721.4, "fim": 800.0, "titulo": 'Crise no STF: "guerra" de poder?'}
        self.assertEqual(tarefas.nome_do_bloco(3, bloco), "03 - Crise no STF guerra de poder (0h12m01s)")


if __name__ == "__main__":
    unittest.main()
