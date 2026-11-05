"""Küçük resim modülü testleri (ağ ve ffmpeg gerekmez)."""
import hashlib
import os
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

from dlnacast import kucukresim


class KucukResimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p1 = mock.patch.object(kucukresim, "CACHE_ROOT", self.tmp.name)
        p2 = mock.patch.object(kucukresim, "CACHE", os.path.join(self.tmp.name, "dlnacast", "kucukresim"))
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop)

    def test_sadece_http_adresleri_indirilir(self):
        for kotu in ("file:///etc/passwd", "/etc/passwd", "ftp://x/a.jpg"):
            self.assertIsNone(kucukresim.from_url(kotu))

    def test_kde_kucuk_resmi_kullanilir(self):
        video = os.path.join(self.tmp.name, "Bölüm 1.mkv")
        open(video, "w").close()
        uri = "file://" + quote(os.path.abspath(video))
        d = os.path.join(self.tmp.name, "thumbnails", "large")
        os.makedirs(d)
        png = os.path.join(d, hashlib.md5(uri.encode()).hexdigest() + ".png")
        open(png, "w").close()
        self.assertEqual(kucukresim.freedesktop(video), png)
        os.utime(png, (1, 1))  # video küçük resimden yeniyse eski resim kullanılmaz
        self.assertIsNone(kucukresim.freedesktop(video))

    def test_ffmpeg_yoksa_sessizce_gecer(self):
        video = os.path.join(self.tmp.name, "a.mp4")
        open(video, "w").close()
        with mock.patch.object(kucukresim.shutil, "which", return_value=None):
            self.assertIsNone(kucukresim.from_local(video))


if __name__ == "__main__":
    unittest.main()
