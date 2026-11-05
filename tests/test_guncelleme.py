"""Sürüm karşılaştırma testleri (ağ gerekmez)."""
import unittest

from dlnacast import __version__
from dlnacast.guncelleme import en_yeni, surum_tuple


class SurumTest(unittest.TestCase):
    def test_surum_tuple(self):
        self.assertEqual(surum_tuple("v0.10.2"), (0, 10, 2))
        self.assertEqual(surum_tuple("1.2"), (1, 2))
        self.assertEqual(surum_tuple("deneme"), ())
        self.assertEqual(surum_tuple("v1.0-beta"), ())

    def test_en_yeni_sayisal_siralar(self):
        self.assertEqual(en_yeni(["v0.9.0", "v0.10.0", "v0.2.1", "baska"]), "v0.10.0")
        self.assertIsNone(en_yeni(["baska"]))

    def test_kurulu_surum_gecerli(self):
        self.assertTrue(surum_tuple(__version__))


if __name__ == "__main__":
    unittest.main()
