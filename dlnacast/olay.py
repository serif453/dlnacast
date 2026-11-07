"""UPnP olay bildirimi (GENA): abonelere durum değişikliklerini NOTIFY ile gönderir.

Renderer için: AVTransport ve RenderingControl `LastChange` olayı, ConnectionManager düz
değişkenler. Sadece standart kütüphane. Kendi thread'lerinde çalışır.
"""
import http.client
import queue
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr

MAX_SUBS = 32  # aynı anda en fazla abonelik
MAX_CALLBACKS = 4  # bir abonelikte en fazla geri çağrı adresi
MIN_TIMEOUT, MAX_TIMEOUT = 60, 1800  # saniye
NOTIFY_TIMEOUT = 3.0
POLL_EVERY = 1.0  # abone varken oynatıcı durumunu bu sıklıkla yokla
FIRST_DELAY = 0.2  # ilk olay, abonelik yanıtı (SID) karşıya ulaştıktan sonra gitsin

AVT_EVENT_NS = "urn:schemas-upnp-org:metadata-1-0/AVT/"
RCS_EVENT_NS = "urn:schemas-upnp-org:metadata-1-0/RCS/"

# Durum anlık görüntüsü: servis -> {değişken: değer}
Snapshot = Dict[str, Dict[str, str]]


class SubscribeError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def parse_callbacks(header: str) -> List[str]:
    """'<http://a/b><http://c/d>' -> ['http://a/b', 'http://c/d'] (sadece http)."""
    out = []
    for part in header.split("<")[1:]:
        url = part.split(">", 1)[0].strip()
        if url.lower().startswith("http://"):
            out.append(url)
    return out


def parse_timeout(header: str) -> int:
    t = (header or "").strip().lower()
    if t.startswith("second-"):
        try:
            return max(MIN_TIMEOUT, min(MAX_TIMEOUT, int(t[7:])))
        except ValueError:
            pass
    return MAX_TIMEOUT  # yoksa ya da "infinite" ise


def last_change_xml(ns: str, values: Dict[str, str]) -> str:
    """LastChange içeriği: <Event><InstanceID val="0"><Değişken val="..."/>...</InstanceID></Event>"""
    inner = []
    for name, val in values.items():
        if name in ("Volume", "Mute"):
            inner.append(f'<{name} channel="Master" val={quoteattr(str(val))}/>')
        else:
            inner.append(f"<{name} val={quoteattr(str(val))}/>")
    return f'<Event xmlns="{ns}"><InstanceID val="0">{"".join(inner)}</InstanceID></Event>'


def propertyset(props: Dict[str, str]) -> bytes:
    inner = "".join(f"<e:property><{k}>{escape(str(v))}</{k}></e:property>" for k, v in props.items())
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">{inner}</e:propertyset>'
            ).encode("utf-8")


def event_body(service: str, values: Dict[str, str]) -> bytes:
    if service == "avt":
        return propertyset({"LastChange": last_change_xml(AVT_EVENT_NS, values)})
    if service == "rcs":
        return propertyset({"LastChange": last_change_xml(RCS_EVENT_NS, values)})
    return propertyset(values)  # cms: değişkenler doğrudan


class _Sub:
    def __init__(self, sid: str, service: str, callbacks: List[str], timeout: int) -> None:
        self.sid, self.service, self.callbacks = sid, service, callbacks
        self.expires = time.monotonic() + timeout
        self.seq = 0
        self.ready = False  # ilk (tam) olay gönderilmeden değişiklik olayı gönderme
        self.not_before = time.monotonic() + FIRST_DELAY

    def next_seq(self) -> int:
        s = self.seq
        self.seq = 1 if self.seq >= 0xFFFFFFFF else self.seq + 1
        return s


class EventManager:
    """Abonelikleri tutar, durum değişimini fark edip abonelere bildirir."""

    def __init__(self, snapshot: Callable[[], Snapshot]) -> None:
        self._snapshot = snapshot  # anlık durumu döndürür (kendi thread'imizde çağrılır)
        self._subs: Dict[str, _Sub] = {}
        self._lock = threading.Lock()
        self._last: Optional[Snapshot] = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._out: "queue.Queue[Optional[Tuple[str, str, int, bytes]]]" = queue.Queue()
        self._threads: List[threading.Thread] = []

    # ---- yaşam döngüsü ----
    def start(self) -> None:
        self._stop.clear()
        self._threads = [threading.Thread(target=self._watch, daemon=True),
                         threading.Thread(target=self._send_loop, daemon=True)]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._out.put(None)
        with self._lock:
            self._subs.clear()
        self._last = None

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    def changed(self) -> None:
        """Durum değişmiş olabilir: hemen kontrol et (HTTP thread'inden çağrılabilir)."""
        self._wake.set()

    # ---- SUBSCRIBE / UNSUBSCRIBE ----
    def subscribe(self, service: str, client_ip: str, headers) -> Tuple[str, int]:
        """Yeni abonelik ya da yenileme. (SID, süre) döndürür; hatada SubscribeError."""
        sid = (headers.get("SID") or "").strip()
        callback = headers.get("CALLBACK")
        nt = (headers.get("NT") or "").strip()
        timeout = parse_timeout(headers.get("TIMEOUT", ""))
        if sid:  # yenileme
            if callback or nt:
                raise SubscribeError(400)
            with self._lock:
                sub = self._subs.get(sid)
                if sub is None or sub.service != service:
                    raise SubscribeError(412)
                sub.expires = time.monotonic() + timeout
            return sid, timeout
        if nt != "upnp:event" or not callback:
            raise SubscribeError(412)
        # Güvenlik: olaylar sadece abone olan cihazın kendisine gönderilir (başka adrese değil).
        urls = [u for u in parse_callbacks(callback) if urlsplit(u).hostname == client_ip]
        if not urls:
            raise SubscribeError(412)
        sid = f"uuid:{uuid.uuid4()}"
        with self._lock:
            self._expire_locked()
            if len(self._subs) >= MAX_SUBS:
                raise SubscribeError(503)
            self._subs[sid] = _Sub(sid, service, urls[:MAX_CALLBACKS], timeout)
        t = threading.Timer(FIRST_DELAY + 0.05, self._wake.set)  # ilk olayı izleyici gönderir
        t.daemon = True
        t.start()
        return sid, timeout

    def unsubscribe(self, headers) -> None:
        sid = (headers.get("SID") or "").strip()
        if not sid:
            raise SubscribeError(412)
        if headers.get("CALLBACK") or headers.get("NT"):
            raise SubscribeError(400)
        with self._lock:
            if self._subs.pop(sid, None) is None:
                raise SubscribeError(412)

    # ---- iç işler ----
    def _expire_locked(self) -> None:
        now = time.monotonic()
        for sid in [s for s, sub in self._subs.items() if sub.expires < now]:
            del self._subs[sid]

    def _watch(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(POLL_EVERY)
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                self._expire_locked()
                if not self._subs:
                    self._last = None
                    continue
            try:
                snap = self._snapshot()
            except Exception:  # noqa: BLE001  (oynatıcı yanıt vermezse sonra yine dene)
                continue
            self._dispatch(snap)

    def _dispatch(self, snap: Snapshot) -> None:
        last = self._last or {}
        diff = {svc: {k: v for k, v in vals.items() if last.get(svc, {}).get(k) != v}
                for svc, vals in snap.items()}
        self._last = snap
        now = time.monotonic()
        with self._lock:
            for sub in list(self._subs.values()):
                if not sub.ready:  # yeni abone: tam durum
                    if now < sub.not_before:
                        continue
                    values = snap.get(sub.service, {})
                    sub.ready = True
                else:
                    values = diff.get(sub.service, {})
                if not values:
                    continue
                body = event_body(sub.service, values)
                seq = sub.next_seq()
                for url in sub.callbacks:
                    self._out.put((url, sub.sid, seq, body))

    def _send_loop(self) -> None:
        while True:
            item = self._out.get()
            if item is None or self._stop.is_set():
                return
            url, sid, seq, body = item
            try:
                send_notify(url, sid, seq, body)
            except Exception:  # noqa: BLE001  (abone kapanmış olabilir; önemli değil)
                pass


def send_notify(url: str, sid: str, seq: int, body: bytes) -> int:
    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=NOTIFY_TIMEOUT)
    try:
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        conn.putrequest("NOTIFY", path, skip_accept_encoding=True)
        conn.putheader("Content-Type", 'text/xml; charset="utf-8"')
        conn.putheader("NT", "upnp:event")
        conn.putheader("NTS", "upnp:propchange")
        conn.putheader("SID", sid)
        conn.putheader("SEQ", str(seq))
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()
