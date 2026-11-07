"""Yerel dosyaları ağa sunan küçük HTTP sunucusu (Range + CORS destekli)."""
import mimetypes
import os
import secrets
import socket
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import quote, urlsplit

from . import donustur


@dataclass
class TranscodeJob:
    args: List[str]  # ffmpeg komutu
    mime: str


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

    def _serve_transcode(self, head: bool) -> bool:
        """/t/<anahtar>/ad: ffmpeg çıktısını akıtır. Bu yol değilse False döner."""
        parts = self.path.split("?")[0].split("/")
        if len(parts) < 3 or parts[1] != "t":
            return False
        job = getattr(self.server, "transcodes", {}).get(parts[2])
        if job is None:
            self.send_error(404)
            return True
        # Uzunluk bilinmediği için ortadan başlayan istek karşılanamaz (sarma programdan yapılır).
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes=") and rng[6:].split("-")[0].strip() not in ("", "0"):
            self.send_response(416)
            self.send_header("Content-Range", "bytes */*")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", job.mime)
        self.send_header("Connection", "close")
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("contentFeatures.dlna.org", donustur.DLNA_OZELLIK)
        self._cors()
        self.end_headers()
        if head:
            return True
        try:
            proc = subprocess.Popen(job.args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL)
        except OSError:
            return True
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # cihaz bağlantıyı kapattı (durdur, sarma, sonraki parça)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            if proc.stdout:
                proc.stdout.close()
        return True

    def _serve(self, head: bool) -> None:
        if self._serve_transcode(head):
            return
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
        self._transcodes: Dict[str, TranscodeJob] = {}
        self._ip = ""

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.files = self._files
        self._httpd.transcodes = self._transcodes
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

    def add_transcode(self, source: str, plan: "donustur.Plan", start: float = 0.0) -> str:
        """Kaynağı ffmpeg'den geçirerek sunar; her GET isteği kendi ffmpeg'ini başlatır."""
        self.start()
        token = secrets.token_urlsafe(8)
        self._transcodes[token] = TranscodeJob(donustur.komut(source, plan, start), plan.mime)
        while len(self._transcodes) > 20:  # eski adresler birikmesin
            self._transcodes.pop(next(iter(self._transcodes)))
        ext = {"video/mpeg": ".ts", "video/mp4": ".mp4", "audio/mpeg": ".mp3"}.get(plan.mime, "")
        base = os.path.splitext(os.path.basename(urlsplit(source).path or source))[0] or "medya"
        return f"http://{self._ip}:{self.port}/t/{token}/{quote(base + ext)}"
