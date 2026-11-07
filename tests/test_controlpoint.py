"""ControlPoint cihaz okuma testleri: gerçek bir cihazdan kaydedilmiş açıklama dosyalarıyla.

tests/veri/vihome: Honor "VihomeMediaServer" cihazının dosyaları. ScheduledRecording
servisinde adlar satır sonlarıyla sarılı ("\\nSortCapabilities\\n"); eskiden bu yüzden
cihazın tamamı reddediliyordu. async-upnp-client kurulu değilse test atlanır.
"""
import asyncio
import functools
import os
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    from dlnacast.controlpoint import ControlPoint, tidy_xml
except ImportError:  # async-upnp-client yok (örn. sistem Python'u)
    ControlPoint = None

VERI = os.path.join(os.path.dirname(__file__), "veri", "vihome")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(ControlPoint is None, "async-upnp-client kurulu değil")
class DescribeTest(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=VERI))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.loc = f"http://127.0.0.1:{self.httpd.server_address[1]}/description.xml"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_device_with_padded_names_is_found(self):
        async def run():
            cp = ControlPoint()
            cp._ensure()
            found = []
            await cp._describe(self.loc, found.append)
            await cp._session.close() if getattr(cp, "_session", None) else None
            return cp, found

        cp, found = asyncio.run(run())
        self.assertEqual(cp.errors, [])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "VihomeMediaServer")
        self.assertEqual(found[0].kind, "server")

    def test_tidy_xml(self):
        self.assertEqual(tidy_xml("<a>\n  SortCapabilities\n</a>"), "<a>SortCapabilities</a>")
        self.assertEqual(tidy_xml("<a>\nad</a><b>x y </b>"), "<a>ad</a><b>x y</b>")
        self.assertEqual(tidy_xml("<r>\n  <a>1</a>\n</r>"), "<r><a>1</a></r>")


if __name__ == "__main__":
    unittest.main()
