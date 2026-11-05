"""Renderer testleri. GTK ve mpv gerekmez: sahte oynatıcı + gerçek HTTP (127.0.0.1).

Çalıştır:  python -m unittest discover -s tests -v
"""
import asyncio
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from dlnacast.renderer import AVT, RCS, MediaRenderer, parse_hms, fmt_hms, didl_title


class FakePlayer:
    def __init__(self) -> None:
        self.calls = []
        self.state, self.pos, self.dur, self.vol = "STOPPED", 0.0, 0.0, 100
        self.alive = False

    async def play(self, uri, mime=None, title=""):
        self.calls.append(("play", uri, title))
        self.state, self.pos, self.dur, self.alive = "PLAYING", 1.0, 120.0, True

    async def control(self, cmd):
        self.calls.append(("control", cmd))
        self.state = {"pause": "PAUSED_PLAYBACK", "resume": "PLAYING", "stop": "STOPPED"}[cmd]
        if cmd == "stop":
            self.pos = self.dur = 0.0

    async def status(self, with_volume=False):
        return self.pos, self.dur, self.state, (self.vol if with_volume else None)

    async def seek(self, seconds):
        self.calls.append(("seek", seconds))
        self.pos = seconds

    async def set_volume(self, level):
        self.calls.append(("volume", level))
        self.vol = level

    async def set_mute(self, on):
        self.calls.append(("mute", on))

    def close(self):
        pass


def soap(svc_ns, action, **args):
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    return (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            f'<s:Body><u:{action} xmlns:u="{svc_ns}"><InstanceID>0</InstanceID>{inner}</u:{action}>'
            f'</s:Body></s:Envelope>').encode()


class RendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        threading.Thread(target=cls.loop.run_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.loop.call_soon_threadsafe(cls.loop.stop)

    def setUp(self):
        self.player = FakePlayer()
        self.r = MediaRenderer("Test", "1234-uuid", self.loop, player=self.player, ip="127.0.0.1")
        self.r.start(advertise=False)  # SSDP (çoklu yayın) testte gerekmez
        self.base = f"http://127.0.0.1:{self.r.port}"

    def tearDown(self):
        self.r.stop()

    def call(self, path, ns, action, **args):
        req = urllib.request.Request(
            self.base + path, data=soap(ns, action, **args), method="POST",
            headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{ns}#{action}"'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()
        root = ET.fromstring(body)
        out = {}
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if el.text and el.text.strip():
                out[tag] = el.text
        return out

    def avt(self, action, **a):
        return self.call("/control/avt", AVT, action, **a)

    def rcs(self, action, **a):
        return self.call("/control/rcs", RCS, action, **a)

    # ---- açıklama dosyaları ----
    def test_descriptions_are_valid_xml(self):
        for p in ("/desc.xml", "/avt.xml", "/rcs.xml", "/cms.xml"):
            with urllib.request.urlopen(self.base + p, timeout=5) as resp:
                ET.fromstring(resp.read())
        with urllib.request.urlopen(self.base + "/desc.xml", timeout=5) as resp:
            text = resp.read().decode()
        self.assertIn("MediaRenderer:1", text)
        self.assertIn("uuid:1234-uuid", text)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/yok.xml", timeout=5)
        self.assertEqual(cm.exception.code, 404)

    # ---- oynatma ----
    def test_play_flow(self):
        meta = ('&lt;DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/"&gt;&lt;item&gt;'
                '&lt;dc:title&gt;Film Adı&lt;/dc:title&gt;&lt;/item&gt;&lt;/DIDL-Lite&gt;')
        self.avt("SetAVTransportURI", CurrentURI="http://192.168.1.5/a/film.mkv", CurrentURIMetaData=meta)
        self.assertEqual(self.avt("GetTransportInfo")["CurrentTransportState"], "STOPPED")
        self.avt("Play", Speed="1")
        self.assertIn(("play", "http://192.168.1.5/a/film.mkv", "Film Adı"), self.player.calls)
        self.assertEqual(self.avt("GetTransportInfo")["CurrentTransportState"], "PLAYING")
        pos = self.avt("GetPositionInfo")
        self.assertEqual(pos["RelTime"], "0:00:01")
        self.assertEqual(pos["TrackDuration"], "0:02:00")
        self.avt("Pause")
        self.assertEqual(self.avt("GetTransportInfo")["CurrentTransportState"], "PAUSED_PLAYBACK")
        self.avt("Play", Speed="1")  # duraklatılmıştan devam: yeniden yüklememeli
        self.assertEqual(sum(1 for c in self.player.calls if c[0] == "play"), 1)
        self.avt("Seek", Unit="REL_TIME", Target="0:01:30")
        self.assertIn(("seek", 90.0), self.player.calls)
        self.avt("Stop")
        self.assertEqual(self.avt("GetTransportInfo")["CurrentTransportState"], "STOPPED")

    def test_title_falls_back_to_file_name(self):
        self.avt("SetAVTransportURI", CurrentURI="http://x/m/ab%20c.mp4", CurrentURIMetaData="")
        self.avt("Play", Speed="1")
        self.assertIn(("play", "http://x/m/ab%20c.mp4", "ab c.mp4"), self.player.calls)

    def test_no_media(self):
        self.assertEqual(self.avt("GetTransportInfo")["CurrentTransportState"], "NO_MEDIA_PRESENT")
        self.assertEqual(self.avt("Play", Speed="1")["errorCode"], "701")

    def test_rejects_local_paths_and_odd_schemes(self):
        for bad in ("/etc/passwd", "file:///etc/passwd", "av://lavfi:testsrc", "edl://x"):
            self.assertEqual(self.avt("SetAVTransportURI", CurrentURI=bad,
                                      CurrentURIMetaData="")["errorCode"], "716", bad)
        self.assertEqual(self.player.calls, [])

    def test_seek_errors(self):
        self.avt("SetAVTransportURI", CurrentURI="http://x/a.mp4", CurrentURIMetaData="")
        self.assertEqual(self.avt("Seek", Unit="REL_TIME", Target="0:00:05")["errorCode"], "701")
        self.avt("Play", Speed="1")
        self.assertEqual(self.avt("Seek", Unit="REL_TIME", Target="-0:00:05")["errorCode"], "711")
        self.assertEqual(self.avt("Seek", Unit="REL_TIME", Target="abc")["errorCode"], "711")
        self.assertEqual(self.avt("Seek", Unit="FRAME", Target="1")["errorCode"], "710")

    def test_wrong_instance_id(self):
        req = urllib.request.Request(
            self.base + "/control/avt", method="POST",
            data=soap(AVT, "Stop").replace(b"<InstanceID>0", b"<InstanceID>5"))
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            self.assertIn(b"718", e.read())
        else:
            self.fail("718 bekleniyordu")

    # ---- ses ----
    def test_volume_and_mute(self):
        self.assertEqual(self.rcs("GetVolume", Channel="Master")["CurrentVolume"], "100")
        self.rcs("SetVolume", Channel="Master", DesiredVolume="35")  # mpv kapalı: sadece saklanır
        self.assertEqual(self.r.volume, 35)
        self.assertNotIn(("volume", 35), self.player.calls)
        self.avt("SetAVTransportURI", CurrentURI="http://x/a.mp4", CurrentURIMetaData="")
        self.avt("Play", Speed="1")  # başlayınca saklanan ses uygulanır
        self.assertIn(("volume", 35), self.player.calls)
        self.rcs("SetVolume", Channel="Master", DesiredVolume="500")  # sınırlanır
        self.assertEqual(self.rcs("GetVolume", Channel="Master")["CurrentVolume"], "100")
        self.assertEqual(self.rcs("SetVolume", Channel="Master", DesiredVolume="x")["errorCode"], "402")
        self.rcs("SetMute", Channel="Master", DesiredMute="1")
        self.assertEqual(self.rcs("GetMute", Channel="Master")["CurrentMute"], "1")

    # ---- bağlantı yöneticisi / hatalar ----
    def test_connection_manager(self):
        out = self.call("/control/cms", "urn:schemas-upnp-org:service:ConnectionManager:1", "GetProtocolInfo")
        self.assertIn("video/mp4", out["Sink"])

    def test_unknown_action(self):
        self.assertEqual(self.avt("Foo")["errorCode"], "401")

    def test_oversized_body_rejected(self):
        req = urllib.request.Request(self.base + "/control/avt", method="POST", data=b"x" * 1_100_000)
        with self.assertRaises((urllib.error.HTTPError, ConnectionError, urllib.error.URLError)):
            urllib.request.urlopen(req, timeout=5)

    def test_stop_server(self):
        self.r.stop()
        self.assertFalse(self.r.running)
        with self.assertRaises(Exception):
            urllib.request.urlopen(self.base + "/desc.xml", timeout=2)


class HelperTest(unittest.TestCase):
    def test_time(self):
        self.assertEqual(parse_hms("0:01:23"), 83)
        self.assertEqual(parse_hms("00:00:01.500"), 1.5)
        self.assertIsNone(parse_hms("-0:00:10"))
        self.assertIsNone(parse_hms("NOT_IMPLEMENTED"))
        self.assertEqual(fmt_hms(3725), "1:02:05")

    def test_didl(self):
        self.assertEqual(didl_title(""), "")
        self.assertEqual(didl_title("<bozuk"), "")


if __name__ == "__main__":
    unittest.main()
