"""Yerel dosyaları ağa sunan küçük HTTP sunucusu (Range + CORS destekli)."""
import mimetypes
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import quote


_EXTRA_MIME = {
    ".mkv": "video/x-matroska", ".webm": "video/webm", ".mp4": "video/mp4",
    ".m4v": "video/mp4", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".ts": "video/mp2t", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".opus": "audio/ogg", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
}


def guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _EXTRA_MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def lan_ip() -> str:
    """Yerel ağdaki IP adresini bulur (paket göndermez)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # sessiz
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def do_GET(self) -> None:
        self._serve(head=False)

    def _resolve(self) -> Optional[str]:
        parts = self.path.split("?")[0].split("/")
        if len(parts) >= 3 and parts[1] == "f":
            return self.server.files.get(parts[2])
        return None

    def _serve(self, head: bool) -> None:
        path = self._resolve()
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        ctype = guess_mime(path)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = int(b) if b else size - 1
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("transferMode.dlna.org", "Streaming")
        self._cors()
        self.end_headers()
        if head:
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LocalServer:
    def __init__(self) -> None:
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._files: Dict[str, str] = {}
        self._ip = ""

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.files = self._files
        self._ip = lan_ip()
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def add_file(self, path: str) -> str:
        """Dosyayı sunuma ekler ve ağdan erişilecek adresi döndürür."""
        self.start()
        token = secrets.token_urlsafe(8)
        self._files[token] = path
        name = quote(os.path.basename(path))
        return f"http://{self._ip}:{self.port}/f/{token}/{name}"
