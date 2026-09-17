"""Todos os blocos sem edição: escolha do formato de maior qualidade, quadros-chave e comando de recorte sem perda."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from indomavel import recorte, tarefas, youtube


def formato(ident, altura=None, vcodec="none", acodec="none", fps=30, tbr=1000, abr=None, idioma=None, preferencia=None):
    return {"format_id": ident, "height": altura, "vcodec": vcodec, "acodec": acodec, "fps": fps, "tbr": tbr, "abr": abr,
            "language": idioma, "language_preference": preferencia}


# Como o YouTube listou a LIVE SURPRESA em 16/09/2026: dublagens automáticas em inglês e espanhol, com a mesma taxa
# da faixa original em português, vindo antes dela na lista.
LIVE_DUBLADA = [
    formato("270", 1080, "avc1.640028"),
    formato("140-0", acodec="mp4a.40.2", abr=129.473, idioma="en-US", preferencia=-1),
    formato("140-1", acodec="mp4a.40.2", abr=129.473, idioma="es-US", preferencia=-1),
    formato("140-2", acodec="mp4a.40.2", abr=129.473, idioma="pt-BR", preferencia=10),
    formato("251-0", acodec="opus", abr=113.77, idioma="en-US", preferencia=-1),
    formato("251-2", acodec="opus", abr=115.005, idioma="pt-BR", preferencia=10),
]


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

    def test_prefere_o_audio_original_as_dublagens_automaticas(self):
        self.assertEqual(youtube.escolher_formatos(LIVE_DUBLADA), ("270", "140-2"))

    def test_prefere_o_audio_normal_ao_drc(self):
        # Como o YouTube listou a COMEÇO DA VIAGEM: cada faixa também numa versão DRC (volume comprimido), antes na lista.
        drc = [formato("270", 1080, "avc1.640028"),
               dict(formato("140-drc", acodec="mp4a.40.2", abr=129.471, idioma="pt", preferencia=-1), quality=2.5),
               dict(formato("251-drc", acodec="opus", abr=95.66, idioma="pt", preferencia=-1), quality=2.5),
               dict(formato("140", acodec="mp4a.40.2", abr=129.471, idioma="pt", preferencia=-1), quality=3.0),
               dict(formato("251", acodec="opus", abr=94.75, idioma="pt", preferencia=-1), quality=3.0)]
        self.assertEqual(youtube.escolher_formatos(drc), ("270", "140"))

    def test_audio_original_vence_mesmo_sem_aac(self):
        sem_aac_original = [f for f in LIVE_DUBLADA if f["format_id"] != "140-2"]
        self.assertEqual(youtube.escolher_formatos(sem_aac_original), ("270", "251-2"))


class TestVideoGuardado(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.troca_pasta = patch.object(youtube.config, "PASTA_VIDEOS", self.pasta.name)
        self.troca_pasta.start()
        self.caminho = youtube.caminho_do_video("Eu8t2aEOLfo")
        with open(self.caminho, "wb") as arquivo:
            arquivo.write(b"video antigo")

    def tearDown(self):
        self.troca_pasta.stop()
        self.pasta.cleanup()

    def test_video_sem_ficha_nao_vale(self):
        """Baixado antes da correção: pode estar com a dublagem, então o recorte não usa."""
        self.assertIsNone(youtube.video_em_cache("Eu8t2aEOLfo"))

    def _youtube_falso(self):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = {"formats": LIVE_DUBLADA}
        return ydl

    def test_video_antigo_troca_so_o_audio_pelo_original(self):
        ydl = self._youtube_falso()
        with patch.object(youtube.yt_dlp, "YoutubeDL", return_value=ydl),                 patch.object(youtube, "_trocar_audio") as trocar:
            self.assertEqual(youtube.baixar_video_maximo("Eu8t2aEOLfo"), self.caminho)
        self.assertEqual(trocar.call_args.args[2], "140-2")
        ydl.download.assert_not_called()
        self.assertEqual(youtube.video_em_cache("Eu8t2aEOLfo"), self.caminho)
        with open(os.path.join(self.pasta.name, "Eu8t2aEOLfo.json"), encoding="utf-8") as arquivo:
            self.assertEqual(json.load(arquivo)["idioma_audio"], "pt-BR")

    def test_video_novo_baixa_com_o_audio_original(self):
        os.remove(self.caminho)
        ydl = self._youtube_falso()
        ydl.download.side_effect = lambda enderecos: open(self.caminho, "wb").close()
        with patch.object(youtube.yt_dlp, "YoutubeDL", return_value=ydl) as construtor:
            youtube.baixar_video_maximo("Eu8t2aEOLfo")
        self.assertEqual(construtor.call_args.args[0]["format"], "270+140-2")

    def test_video_com_ficha_nao_volta_ao_youtube(self):
        with open(os.path.join(self.pasta.name, "Eu8t2aEOLfo.json"), "w", encoding="utf-8") as arquivo:
            json.dump({"audio": "140-2", "audio_original": True}, arquivo)
        with patch.object(youtube.yt_dlp, "YoutubeDL") as construtor:
            self.assertEqual(youtube.baixar_video_maximo("Eu8t2aEOLfo"), self.caminho)
        construtor.assert_not_called()


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
