"""Alıcı olay bildirimi (GENA / LastChange) testleri. Sahte oynatıcı + gerçek HTTP (127.0.0.1)."""
import asyncio
import http.client
import queue
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dlnacast import olay
from dlnacast.renderer import AVT, RCS, MediaRenderer

from test_renderer import FakePlayer, soap

E_NS = "{urn:schemas-upnp-org:event-1-0}"


class _Sink(BaseHTTPRequestHandler):
    """Abone tarafı: gelen NOTIFY'ları kuyruğa koyar."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_NOTIFY(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.got.put((dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def parse_event(body: bytes):
    """propertyset -> {değişken: değer}; LastChange varsa içini açar."""
    root = ET.fromstring(body)
    out = {}
    for prop in root.iter(E_NS + "property"):
        for el in prop:
            if el.tag == "LastChange":
                ev = ET.fromstring(el.text)
                for inst in ev:
                    for var in inst:
                        out[var.tag.rsplit("}", 1)[-1]] = var.get("val")
            else:
                out[el.tag] = el.text or ""
    return out


class EventTest(unittest.TestCase):
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
        self.r.start(advertise=False)
        self.sink = ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
        self.sink.daemon_threads = True
        self.sink.got = queue.Queue()
        threading.Thread(target=self.sink.serve_forever, daemon=True).start()
        self.cb = f"http://127.0.0.1:{self.sink.server_address[1]}/olay"

    def tearDown(self):
        self.r.stop()
        self.sink.shutdown()
        self.sink.server_close()

    def req(self, method, path, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.r.port, timeout=5)
        try:
            conn.request(method, path, headers=headers)
            resp = conn.getresponse()
            resp.read()
            return resp.status, dict(resp.getheaders())
        finally:
            conn.close()

    def subscribe(self, svc="avt", cb=None):
        return self.req("SUBSCRIBE", f"/event/{svc}", {
            "CALLBACK": f"<{cb or self.cb}>", "NT": "upnp:event", "TIMEOUT": "Second-300"})

    def avt(self, action, **a):
        conn = http.client.HTTPConnection("127.0.0.1", self.r.port, timeout=5)
        try:
            conn.request("POST", "/control/avt", body=soap(AVT, action, **a), headers={
                "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{AVT}#{action}"'})
            conn.getresponse().read()
        finally:
            conn.close()

    def rcs(self, action, **a):
        conn = http.client.HTTPConnection("127.0.0.1", self.r.port, timeout=5)
        try:
            conn.request("POST", "/control/rcs", body=soap(RCS, action, **a), headers={
                "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{RCS}#{action}"'})
            conn.getresponse().read()
        finally:
            conn.close()

    def next_event(self, timeout=4.0):
        headers, body = self.sink.got.get(timeout=timeout)
        return headers, parse_event(body)

    def wait_for(self, key, value, timeout=4.0):
        """İstenen değeri taşıyan olay gelene kadar bekler."""
        while True:
            _h, ev = self.next_event(timeout)
            if ev.get(key) == value:
                return ev

    # ---- abonelik ----
    def test_subscribe_sends_initial_full_state(self):
        status, h = self.subscribe()
        self.assertEqual(status, 200)
        self.assertTrue(h["SID"].startswith("uuid:"))
        self.assertEqual(h["TIMEOUT"], "Second-300")
        headers, ev = self.next_event()
        self.assertEqual(headers["SID"], h["SID"])
        self.assertEqual(headers["SEQ"], "0")
        self.assertEqual(headers["NTS"], "upnp:propchange")
        self.assertEqual(ev["TransportState"], "NO_MEDIA_PRESENT")
        self.assertIn("CurrentTransportActions", ev)

    def test_play_pause_stop_are_evented(self):
        self.subscribe()
        self.next_event()  # ilk tam durum
        self.avt("SetAVTransportURI", CurrentURI="http://192.168.1.5/film.mkv", CurrentURIMetaData="")
        ev = self.wait_for("TransportState", "STOPPED")
        self.assertEqual(ev["AVTransportURI"], "http://192.168.1.5/film.mkv")
        self.avt("Play", Speed="1")
        ev = self.wait_for("TransportState", "PLAYING")
        self.assertEqual(ev["CurrentTrackDuration"], "0:02:00")
        self.assertNotIn("AVTransportURI", ev)  # sadece değişenler gönderilir
        self.avt("Pause")
        self.wait_for("TransportState", "PAUSED_PLAYBACK")
        self.avt("Stop")
        self.wait_for("TransportState", "STOPPED")

    def test_track_end_is_noticed_by_polling(self):
        self.subscribe()
        self.next_event()
        self.avt("SetAVTransportURI", CurrentURI="http://192.168.1.5/a.mp3", CurrentURIMetaData="")
        self.avt("Play", Speed="1")
        self.wait_for("TransportState", "PLAYING")
        self.player.state = "STOPPED"  # parça kendiliğinden bitti (komut yok)
        self.wait_for("TransportState", "STOPPED")

    def test_rendering_control_volume_event(self):
        self.subscribe("rcs")
        _h, ev = self.next_event()
        self.assertEqual(ev["Volume"], "100")
        self.assertEqual(ev["Mute"], "0")
        self.rcs("SetVolume", Channel="Master", DesiredVolume="40")
        self.wait_for("Volume", "40")
        self.rcs("SetMute", Channel="Master", DesiredMute="1")
        self.wait_for("Mute", "1")

    def test_cms_event_has_sink_protocols(self):
        self.subscribe("cms")
        _h, ev = self.next_event()
        self.assertIn("video/mp4", ev["SinkProtocolInfo"])

    def test_renew_and_unsubscribe(self):
        _s, h = self.subscribe()
        sid = h["SID"]
        self.next_event()
        status, h2 = self.req("SUBSCRIBE", "/event/avt", {"SID": sid, "TIMEOUT": "Second-100"})
        self.assertEqual(status, 200)
        self.assertEqual(h2["SID"], sid)
        self.assertEqual(h2["TIMEOUT"], "Second-100")
        self.assertEqual(self.req("UNSUBSCRIBE", "/event/avt", {"SID": sid})[0], 200)
        self.assertEqual(self.r.events.count, 0)
        self.assertEqual(self.req("UNSUBSCRIBE", "/event/avt", {"SID": sid})[0], 412)
        self.assertEqual(self.req("SUBSCRIBE", "/event/avt", {"SID": sid})[0], 412)

    # ---- güvenlik ve hatalı istekler ----
    def test_callback_to_other_host_is_rejected(self):
        status, _h = self.subscribe(cb="http://10.255.255.1:8080/olay")
        self.assertEqual(status, 412)
        self.assertEqual(self.r.events.count, 0)

    def test_bad_requests(self):
        self.assertEqual(self.req("SUBSCRIBE", "/event/avt", {"CALLBACK": f"<{self.cb}>"})[0], 412)
        self.assertEqual(self.req("SUBSCRIBE", "/event/yok", {
            "CALLBACK": f"<{self.cb}>", "NT": "upnp:event"})[0], 404)
        self.assertEqual(self.req("SUBSCRIBE", "/event/avt", {
            "SID": "uuid:x", "NT": "upnp:event"})[0], 400)

    def test_subscription_limit(self):
        for _ in range(olay.MAX_SUBS):
            self.assertEqual(self.subscribe()[0], 200)
        self.assertEqual(self.subscribe()[0], 503)


class HelperTest(unittest.TestCase):
    def test_parse_callbacks(self):
        self.assertEqual(olay.parse_callbacks("<http://a:1/x><http://b/y>"), ["http://a:1/x", "http://b/y"])
        self.assertEqual(olay.parse_callbacks("<file:///etc/passwd>"), [])

    def test_parse_timeout(self):
        self.assertEqual(olay.parse_timeout("Second-300"), 300)
        self.assertEqual(olay.parse_timeout("Second-5"), olay.MIN_TIMEOUT)
        self.assertEqual(olay.parse_timeout("infinite"), olay.MAX_TIMEOUT)
        self.assertEqual(olay.parse_timeout(""), olay.MAX_TIMEOUT)

    def test_last_change_escapes_values(self):
        body = olay.event_body("avt", {"CurrentTrackMetaData": '<DIDL a="1">&</DIDL>'})
        self.assertEqual(parse_event(body)["CurrentTrackMetaData"], '<DIDL a="1">&</DIDL>')


if __name__ == "__main__":
    unittest.main()
