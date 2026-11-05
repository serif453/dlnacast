"""Chromecast bağlantı mantığı testleri (sahte pychromecast ile; ağ gerekmez)."""
import asyncio
import sys
import types
import unittest
import uuid
from unittest import mock

from dlnacast import cast


class SahteHata(Exception):
    pass


class SahteInfo:
    def __init__(self, name, host):
        self.friendly_name, self.host = name, host


class SahteCC:
    """Sahte Chromecast: `fail` kadar oynatma denemesi başarısız olur."""
    olusturulan = []

    def __init__(self, info, fail):
        self.cast_info, self.uuid = info, None
        self.started = self.disconnected = False
        self.fail = fail
        self.played = []
        self.media_controller = self
        SahteCC.olusturulan.append(self)

    def start(self):
        self.started = True

    def wait(self, timeout=None):
        if self.fail:
            raise SahteHata("bağlanamadı")

    def play_media(self, url, ctype, title=None):
        self.played.append((url, ctype, title))

    def block_until_active(self, timeout=None):
        pass

    def disconnect(self, blocking=True):
        self.disconnected = True


def sahte_modul(devices, fail_counts):
    """pychromecast, pychromecast.discovery/error ve zeroconf yerine geçen modüller."""
    pc = types.ModuleType("pychromecast")
    sayac = {"n": 0}

    def from_info(info, zc, tries=None, retry_wait=None, timeout=None):
        i = sayac["n"]
        sayac["n"] += 1
        return SahteCC(info, fail_counts[i] if i < len(fail_counts) else 0)

    pc.get_chromecast_from_cast_info = from_info
    disc = types.ModuleType("pychromecast.discovery")

    class Browser:
        def __init__(self, listener, zc, hosts):
            self.devices, self.hosts = devices, hosts
            self.host_browser = mock.Mock()

        def start_discovery(self):
            pass

        def stop_discovery(self):
            pass

    disc.CastBrowser, disc.SimpleCastListener = Browser, lambda: None
    err = types.ModuleType("pychromecast.error")
    err.PyChromecastError = SahteHata
    zc = types.ModuleType("zeroconf")
    zc.Zeroconf = mock.Mock
    return {"pychromecast": pc, "pychromecast.discovery": disc,
            "pychromecast.error": err, "zeroconf": zc}


class CastTest(unittest.TestCase):
    def setUp(self):
        SahteCC.olusturulan = []
        self.u = uuid.uuid4()
        self.devices = {self.u: SahteInfo("Salon TV", "192.168.1.40")}
        self.patch_sleep = mock.patch.object(cast.time, "sleep")
        self.patch_sleep.start()

    def tearDown(self):
        self.patch_sleep.stop()

    def run_with(self, fail_counts, fn):
        with mock.patch.dict(sys.modules, sahte_modul(self.devices, fail_counts)):
            m = cast.CastManager()
            found = asyncio.run(m.discover(["192.168.1.40"]))
            return m, found, fn(m)

    def test_bulunan_cihaza_hemen_baglanir(self):
        m, found, _ = self.run_with([], lambda m: None)
        self.assertEqual(found, [("Salon TV", str(self.u))])
        self.assertTrue(SahteCC.olusturulan[0].started)
        self.assertEqual(m.known_hosts(), ["192.168.1.40"])

    def test_ilk_deneme_basarisizsa_yeniden_baglanir(self):
        key = str(self.u)
        m, _, _ = self.run_with([1, 0], lambda m: m._play_blocking(key, "http://x/a.mp4", None, "A"))
        ilk, ikinci = SahteCC.olusturulan
        self.assertTrue(ilk.disconnected)
        self.assertEqual(ikinci.played, [("http://x/a.mp4", "video/mp4", "A")])

    def test_iki_deneme_de_basarisizsa_turkce_hata(self):
        key = str(self.u)

        def oynat(m):
            with self.assertRaises(RuntimeError) as ctx:
                m._play_blocking(key, "http://x/a.mp3", "audio/mpeg", "")
            return str(ctx.exception)

        _, _, mesaj = self.run_with([1, 1], oynat)
        self.assertIn("Chromecast'e bağlanılamadı", mesaj)

    def test_yenilemede_baglanti_koparilmaz(self):
        def yenile(m):
            asyncio.run(m.discover([]))
            return None

        self.run_with([], yenile)
        self.assertEqual(len(SahteCC.olusturulan), 1)
        self.assertFalse(SahteCC.olusturulan[0].disconnected)


if __name__ == "__main__":
    unittest.main()
