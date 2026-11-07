"""Dönüştürme (transcode) testleri. Karar mantığı ffmpeg gerektirmez; akış testi ffmpeg varsa çalışır."""
import http.client
import os
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit

from dlnacast import donustur
from dlnacast.localserver import LocalServer


def bilgi(video="", ses="", kap="", piksel="yuv420p", sure=60.0):
    return donustur.Bilgi(video=video, piksel=piksel if video else "", ses=ses, kap=kap, sure=sure)


class KararTest(unittest.TestCase):
    def test_ffprobe_ciktisi_okunur_kapak_atlanir(self):
        veri = {"streams": [
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "disposition": {}},
            {"codec_type": "audio", "codec_name": "dts"}],
            "format": {"format_name": "matroska,webm", "duration": "123.4"}}
        b = donustur.bilgi_oku(veri)
        self.assertEqual((b.video, b.piksel, b.ses, b.kap, b.sure),
                         ("hevc", "yuv420p10le", "dts", "matroska,webm", 123.4))

    def test_sadece_kapakli_muzik_ses_sayilir(self):
        veri = {"streams": [{"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}},
                            {"codec_type": "audio", "codec_name": "flac"}], "format": {}}
        self.assertEqual(donustur.bilgi_oku(veri).video, "")

    def test_dlna_anahtar_kapaliysa_dokunma(self):
        p = donustur.planla(bilgi("hevc", "dts", "matroska"), donustur.DLNA, zorla=False)
        self.assertEqual(p.yontem, donustur.DOGRUDAN)

    def test_dlna_h264_kap_degisir(self):
        p = donustur.planla(bilgi("h264", "ac3", "matroska,webm"), donustur.DLNA)
        self.assertEqual((p.yontem, p.mime, p.video_kopya, p.ses_kopya), (donustur.KAP, "video/mpeg", True, True))

    def test_dlna_h264_dts_ses_cevrilir(self):
        p = donustur.planla(bilgi("h264", "dts", "matroska"), donustur.DLNA)
        self.assertEqual((p.yontem, p.ses_kopya), (donustur.KAP, False))

    def test_dlna_hevc_ve_10bit_tam(self):
        self.assertEqual(donustur.planla(bilgi("hevc", "aac", "mp4"), donustur.DLNA).yontem, donustur.TAM)
        self.assertEqual(donustur.planla(bilgi("h264", "aac", "mp4", piksel="yuv420p10le"),
                                         donustur.DLNA).yontem, donustur.TAM)

    def test_dlna_muzik_mp3(self):
        p = donustur.planla(bilgi(ses="flac", kap="flac"), donustur.DLNA)
        self.assertEqual((p.yontem, p.mime, p.sadece_ses), (donustur.TAM, "audio/mpeg", True))

    def test_chromecast_uyumlu_dokunulmaz(self):
        p = donustur.planla(bilgi("h264", "aac", "mov,mp4,m4a,3gp,3g2,mj2"), donustur.CHROMECAST)
        self.assertEqual(p.yontem, donustur.DOGRUDAN)
        self.assertEqual(donustur.planla(bilgi(ses="mp3", kap="mp3"), donustur.CHROMECAST).yontem,
                         donustur.DOGRUDAN)

    def test_chromecast_mkv_kap_degisir(self):
        p = donustur.planla(bilgi("h264", "ac3", "matroska,webm"), donustur.CHROMECAST)
        self.assertEqual((p.yontem, p.mime, p.ses_kopya), (donustur.KAP, "video/mp4", False))

    def test_chromecast_hevc_tam(self):
        self.assertEqual(donustur.planla(bilgi("hevc", "aac", "matroska"), donustur.CHROMECAST).yontem,
                         donustur.TAM)

    def test_komut(self):
        p = donustur.planla(bilgi("hevc", "aac", "matroska"), donustur.CHROMECAST)
        k = donustur.komut("/a b.mkv", p, 90, ffmpeg="ffmpeg")
        self.assertLess(k.index("-ss"), k.index("-i"))
        self.assertEqual(k[k.index("-i") + 1], "/a b.mkv")
        self.assertIn("libx264", k)
        self.assertIn("+frag_keyframe+empty_moov+default_base_moof", k)
        self.assertEqual(k[-1], "pipe:1")
        p = donustur.planla(bilgi("h264", "aac", "matroska"), donustur.DLNA)
        k = donustur.komut("x.mkv", p, ffmpeg="ffmpeg")
        self.assertNotIn("-ss", k)
        self.assertEqual(k[k.index("-c:v") + 1], "copy")
        self.assertEqual(k[k.index("-f") + 1], "mpegts")

    def test_didl_kacislar(self):
        d = donustur.didl("http://1.2.3.4/t/x/a&b.ts", "video/mpeg", "A <B>")
        self.assertIn("a&amp;b.ts", d)
        self.assertIn("A &lt;B&gt;", d)
        self.assertIn("http-get:*:video/mpeg:DLNA.ORG_OP=00;DLNA.ORG_CI=1", d)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg yok")
class AkisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.mkv = os.path.join(cls.tmp, "deneme.mkv")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                        "-f", "lavfi", "-i", "sine", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "ac3", cls.mkv], check=True)
        cls.srv = LocalServer()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _get(self, url, method="GET", headers=None):
        u = urlsplit(url)
        c = http.client.HTTPConnection("127.0.0.1", u.port, timeout=30)
        c.request(method, u.path, headers=headers or {})
        r = c.getresponse()
        return r, (r.read() if method == "GET" else b"")

    def test_mpegts_akisi(self):
        b = donustur.incele(self.mkv)
        self.assertEqual((b.video, b.ses), ("h264", "ac3"))
        url = self.srv.add_transcode(self.mkv, donustur.planla(b, donustur.DLNA))
        self.assertTrue(url.endswith("/deneme.ts"))
        r, data = self._get(url)
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "video/mpeg"))
        self.assertIsNone(r.getheader("Content-Length"))
        self.assertEqual(data[0], 0x47)  # MPEG-TS eşitleme baytı
        self.assertGreater(len(data), 10000)

    def test_head_ve_range(self):
        b = donustur.incele(self.mkv)
        url = self.srv.add_transcode(self.mkv, donustur.planla(b, donustur.CHROMECAST))
        r, _ = self._get(url, "HEAD")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "video/mp4"))
        self.assertIn("DLNA.ORG_CI=1", r.getheader("contentFeatures.dlna.org"))
        r, _ = self._get(url, headers={"Range": "bytes=5000-"})
        self.assertEqual(r.status, 416)
        r, data = self._get(url, headers={"Range": "bytes=0-"})
        self.assertEqual(r.status, 200)
        self.assertEqual(data[4:8], b"ftyp")

    def test_bilinmeyen_anahtar_404(self):
        self.srv.start()
        r, _ = self._get(f"http://127.0.0.1:{self.srv.port}/t/yok/a.ts")
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main()
