"""Müzik/video ayrımı testleri (mpv gerekmez)."""
import unittest

from dlnacast.localplayer import is_audio


class MuzikMiTest(unittest.TestCase):
    def test_mime_once_gelir(self):
        self.assertTrue(is_audio("http://x/dosya", "audio/mpeg"))
        self.assertFalse(is_audio("http://x/sarki.mp3", "video/mp4"))

    def test_uzantidan(self):
        self.assertTrue(is_audio("http://192.168.1.5:8200/MediaItems/Dipsiz%20Kuyum.MP3"))
        self.assertTrue(is_audio("/home/serif/Müzik/şarkı.flac"))
        self.assertFalse(is_audio("http://x/film.mkv"))
        self.assertFalse(is_audio("http://x/MediaItems/123"))  # bilinmiyor: video sayılır


if __name__ == "__main__":
    unittest.main()
