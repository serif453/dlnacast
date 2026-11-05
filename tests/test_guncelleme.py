"""Sürüm karşılaştırma testleri (ağ gerekmez)."""
import unittest

from dlnacast import __version__
from dlnacast.guncelleme import surum_metinden, surum_tuple


class SurumTest(unittest.TestCase):
    def test_surum_tuple(self):
        self.assertEqual(surum_tuple("v0.10.2"), (0, 10, 2))
        self.assertEqual(surum_tuple("1.2"), (1, 2))
        self.assertEqual(surum_tuple("deneme"), ())
        self.assertEqual(surum_tuple("v1.0-beta"), ())

    def test_sayisal_karsilastirma(self):
        self.assertGreater(surum_tuple("0.10.0"), surum_tuple("0.9.3"))

    def test_surum_metinden(self):
        self.assertEqual(surum_metinden('__version__ = "1.4.2"\nREPO = "x"\n'), "1.4.2")
        self.assertIsNone(surum_metinden("bos"))

    def test_kurulu_surum_gecerli(self):
        self.assertTrue(surum_tuple(__version__))


if __name__ == "__main__":
    unittest.main()
