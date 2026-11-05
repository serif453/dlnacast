"""UPnP/DLNA MediaServer: SSDP duyurusu + ContentDirectory (SOAP) + dosya akıtma.

Sadece Python standart kütüphanesini kullanır.
"""
import hashlib
import os
import random
import re
import socket
import struct
import sys
import threading
from http.server import ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr
import xml.etree.ElementTree as ET

from .localserver import _Handler, guess_mime, lan_ip

DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaServer:1"
CDS = "urn:schemas-upnp-org:service:ContentDirectory:1"
CMS = "urn:schemas-upnp-org:service:ConnectionManager:1"
SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900
SERVER_STR = "Linux/1.0 UPnP/1.0 DLNACast/0.1"


class UpnpError(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code, self.text = code, text


def _natural_key(text: str) -> list:
    """"bolum2" < "bolum10" olacak sekilde siralama anahtari."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", text)]


# ---------------- XML üretimi ----------------
def scpd_xml(actions: Dict[str, List[Tuple[str, str, str]]], variables: List[tuple]) -> str:
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<scpd xmlns="urn:schemas-upnp-org:service-1-0">',
           "<specVersion><major>1</major><minor>0</minor></specVersion><actionList>"]
    for name, args in actions.items():
        out.append(f"<action><name>{name}</name><argumentList>")
        for aname, direction, related in args:
            out.append(f"<argument><name>{aname}</name><direction>{direction}</direction>"
                       f"<relatedStateVariable>{related}</relatedStateVariable></argument>")
        out.append("</argumentList></action>")
    out.append("</actionList><serviceStateTable>")
    for name, typ, events, allowed in variables:
        out.append(f'<stateVariable sendEvents="{events}"><name>{name}</name>'
                   f"<dataType>{typ}</dataType>")
        if isinstance(allowed, tuple):  # (en az, en çok, adım)
            lo, hi, step = allowed
            out.append(f"<allowedValueRange><minimum>{lo}</minimum><maximum>{hi}</maximum>"
                       f"<step>{step}</step></allowedValueRange>")
        elif allowed:
            out.append("<allowedValueList>"
                       + "".join(f"<allowedValue>{a}</allowedValue>" for a in allowed)
                       + "</allowedValueList>")
        out.append("</stateVariable>")
    out.append("</serviceStateTable></scpd>")
    return "".join(out)


CDS_SCPD = scpd_xml(
    {
        "GetSearchCapabilities": [("SearchCaps", "out", "SearchCapabilities")],
        "GetSortCapabilities": [("SortCaps", "out", "SortCapabilities")],
        "GetSystemUpdateID": [("Id", "out", "SystemUpdateID")],
        "Browse": [
            ("ObjectID", "in", "A_ARG_TYPE_ObjectID"),
            ("BrowseFlag", "in", "A_ARG_TYPE_BrowseFlag"),
            ("Filter", "in", "A_ARG_TYPE_Filter"),
            ("StartingIndex", "in", "A_ARG_TYPE_Index"),
            ("RequestedCount", "in", "A_ARG_TYPE_Count"),
            ("SortCriteria", "in", "A_ARG_TYPE_SortCriteria"),
            ("Result", "out", "A_ARG_TYPE_Result"),
            ("NumberReturned", "out", "A_ARG_TYPE_Count"),
            ("TotalMatches", "out", "A_ARG_TYPE_Count"),
            ("UpdateID", "out", "A_ARG_TYPE_UpdateID"),
        ],
    },
    [
        ("A_ARG_TYPE_ObjectID", "string", "no", None),
        ("A_ARG_TYPE_Result", "string", "no", None),
        ("A_ARG_TYPE_BrowseFlag", "string", "no", ["BrowseMetadata", "BrowseDirectChildren"]),
        ("A_ARG_TYPE_Filter", "string", "no", None),
        ("A_ARG_TYPE_SortCriteria", "string", "no", None),
        ("A_ARG_TYPE_Index", "ui4", "no", None),
        ("A_ARG_TYPE_Count", "ui4", "no", None),
        ("A_ARG_TYPE_UpdateID", "ui4", "no", None),
        ("SearchCapabilities", "string", "no", None),
        ("SortCapabilities", "string", "no", None),
        ("SystemUpdateID", "ui4", "yes", None),
    ],
)

CMS_SCPD = scpd_xml(
    {
        "GetProtocolInfo": [("Source", "out", "SourceProtocolInfo"),
                            ("Sink", "out", "SinkProtocolInfo")],
        "GetCurrentConnectionIDs": [("ConnectionIDs", "out", "CurrentConnectionIDs")],
        "GetCurrentConnectionInfo": [
            ("ConnectionID", "in", "A_ARG_TYPE_ConnectionID"),
            ("RcsID", "out", "A_ARG_TYPE_RcsID"),
            ("AVTransportID", "out", "A_ARG_TYPE_AVTransportID"),
            ("ProtocolInfo", "out", "A_ARG_TYPE_ProtocolInfo"),
            ("PeerConnectionManager", "out", "A_ARG_TYPE_ConnectionManager"),
            ("PeerConnectionID", "out", "A_ARG_TYPE_ConnectionID"),
            ("Direction", "out", "A_ARG_TYPE_Direction"),
            ("Status", "out", "A_ARG_TYPE_ConnectionStatus"),
        ],
    },
    [
        ("SourceProtocolInfo", "string", "yes", None),
        ("SinkProtocolInfo", "string", "yes", None),
        ("CurrentConnectionIDs", "string", "yes", None),
        ("A_ARG_TYPE_ConnectionStatus", "string", "no",
         ["OK", "ContentFormatMismatch", "InsufficientBandwidth", "UnreliableChannel", "Unknown"]),
        ("A_ARG_TYPE_ConnectionManager", "string", "no", None),
        ("A_ARG_TYPE_Direction", "string", "no", ["Input", "Output"]),
        ("A_ARG_TYPE_ProtocolInfo", "string", "no", None),
        ("A_ARG_TYPE_ConnectionID", "i4", "no", None),
        ("A_ARG_TYPE_AVTransportID", "i4", "no", None),
        ("A_ARG_TYPE_RcsID", "i4", "no", None),
    ],
)

SOURCE_PROTOCOLS = ",".join(
    f"http-get:*:{m}:*" for m in (
        "video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/quicktime",
        "audio/mpeg", "audio/mp4", "audio/flac", "audio/ogg", "audio/wav",
        "image/jpeg", "image/png"))


def soap_response(action: str, ns: str, out: List[Tuple[str, object]]) -> bytes:
    inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in out)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action}Response xmlns:u="{ns}">{inner}</u:{action}Response>'
        "</s:Body></s:Envelope>"
    ).encode("utf-8")


def soap_fault(code: int, text: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><s:Fault>'
        "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{code}</errorCode><errorDescription>{escape(text)}</errorDescription>"
        "</UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    ).encode("utf-8")


# ---------------- Medya sunucusu ----------------
class MediaServer:
    def __init__(self, name: str, udn: str) -> None:
        self.name, self.udn = name, udn
        self.shares: List[str] = []
        self.update_id = 1
        self.ip = ""
        self.port = 0
        self._paths: Dict[str, str] = {}
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._ssdp: Optional["_Ssdp"] = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def location(self) -> str:
        return f"http://{self.ip}:{self.port}/desc.xml"

    def set_shares(self, shares: List[str]) -> None:
        self.shares = [os.path.realpath(s) for s in shares if os.path.isdir(s)]
        self.update_id += 1

    def start(self) -> None:
        if self._httpd is not None:
            return
        self.ip = lan_ip()
        httpd = ThreadingHTTPServer(("0.0.0.0", 0), _MediaHandler)
        httpd.daemon_threads = True
        httpd.media = self
        self._httpd = httpd
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self._ssdp = _Ssdp(self)
        self._ssdp.start()

    def stop(self) -> None:
        if self._ssdp is not None:
            self._ssdp.shutdown()
            self._ssdp = None
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd = None

    # ---- yol / kimlik ----
    def _oid(self, path: str) -> str:
        oid = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
        self._paths[oid] = path
        return oid

    def _allowed(self, path: str) -> bool:
        rp = os.path.realpath(path)
        return any(rp == s or rp.startswith(s + os.sep) for s in self.shares)

    def path_for(self, oid: str) -> Optional[str]:
        p = self._paths.get(oid)
        return p if p and self._allowed(p) else None

    # ---- cihaz açıklaması ----
    def device_xml(self) -> str:
        def svc(typ: str, sid: str, short: str) -> str:
            return (f"<service><serviceType>{typ}</serviceType>"
                    f"<serviceId>urn:upnp-org:serviceId:{sid}</serviceId>"
                    f"<SCPDURL>/{short}.xml</SCPDURL>"
                    f"<controlURL>/control/{short}</controlURL>"
                    f"<eventSubURL>/event/{short}</eventSubURL></service>")
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<root xmlns="urn:schemas-upnp-org:device-1-0" '
            'xmlns:dlna="urn:schemas-dlna-org:device-1-0">'
            "<specVersion><major>1</major><minor>0</minor></specVersion><device>"
            "<dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>"
            f"<deviceType>{DEVICE_TYPE}</deviceType>"
            f"<friendlyName>{escape(self.name)}</friendlyName>"
            "<manufacturer>DLNA Cast</manufacturer><modelName>DLNA Cast</modelName>"
            "<modelNumber>1</modelNumber>"
            f"<UDN>uuid:{self.udn}</UDN><serviceList>"
            + svc(CDS, "ContentDirectory", "cds") + svc(CMS, "ConnectionManager", "cms")
            + "</serviceList></device></root>"
        )

    # ---- ContentDirectory ----
    def _entry_xml(self, path: str, parent: str, base: str) -> str:
        oid = self._oid(path)
        title = escape(os.path.basename(path) or path)
        if os.path.isdir(path):
            return (f'<container id="{oid}" parentID="{parent}" restricted="1" searchable="0">'
                    f"<dc:title>{title}</dc:title>"
                    "<upnp:class>object.container.storageFolder</upnp:class></container>")
        mime = guess_mime(path)
        kind = mime.split("/")[0]
        cls = {"video": "object.item.videoItem", "audio": "object.item.audioItem.musicTrack",
               "image": "object.item.imageItem.photo"}.get(kind, "object.item")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        url = f"http://{base}/m/{oid}/{quote(os.path.basename(path))}"
        proto = f"http-get:*:{mime}:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
        return (f'<item id="{oid}" parentID="{parent}" restricted="1">'
                f"<dc:title>{title}</dc:title><upnp:class>{cls}</upnp:class>"
                f"<res protocolInfo={quoteattr(proto)} size=\"{size}\">{escape(url)}</res></item>")

    @staticmethod
    def _wrap(items: List[str]) -> str:
        return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                + "".join(items) + "</DIDL-Lite>")

    def browse(self, oid: str, flag: str, start: int, count: int, base: str) -> Tuple[str, int, int]:
        if flag == "BrowseMetadata":
            if oid == "0":
                xml = ('<container id="0" parentID="-1" restricted="1" searchable="0">'
                       f"<dc:title>{escape(self.name)}</dc:title>"
                       "<upnp:class>object.container</upnp:class></container>")
            else:
                p = self.path_for(oid)
                if not p:
                    raise UpnpError(701, "No such object")
                parent = "0" if p in self.shares else self._oid(os.path.dirname(p))
                xml = self._entry_xml(p, parent, base)
            return self._wrap([xml]), 1, 1
        if flag != "BrowseDirectChildren":
            raise UpnpError(402, "Invalid args")
        if oid == "0":
            entries = [(p, "0") for p in self.shares]
        else:
            p = self.path_for(oid)
            if not p or not os.path.isdir(p):
                raise UpnpError(701, "No such object")
            try:
                names = os.listdir(p)
            except OSError:
                raise UpnpError(701, "No such object")
            names = [n for n in names if not n.startswith(".")]
            full = [os.path.join(p, n) for n in names]
            full = [f for f in full if os.path.isdir(f)
                    or guess_mime(f).split("/")[0] in ("video", "audio", "image")]
            full.sort(key=lambda f: (not os.path.isdir(f), _natural_key(os.path.basename(f))))
            entries = [(f, oid) for f in full]
        total = len(entries)
        page = entries[start:] if count == 0 else entries[start:start + count]
        return self._wrap([self._entry_xml(p, par, base) for p, par in page]), len(page), total

    def handle(self, control_path: str, action: str, args: Dict[str, str], base: str):
        if control_path.endswith("/cds"):
            if action == "GetSearchCapabilities":
                return CDS, [("SearchCaps", "")]
            if action == "GetSortCapabilities":
                return CDS, [("SortCaps", "")]
            if action == "GetSystemUpdateID":
                return CDS, [("Id", self.update_id)]
            if action == "Browse":
                try:
                    start = int(args.get("StartingIndex") or 0)
                    count = int(args.get("RequestedCount") or 0)
                except ValueError:
                    raise UpnpError(402, "Invalid args")
                didl, n, total = self.browse(args.get("ObjectID", "0"),
                                             args.get("BrowseFlag", ""), start, count, base)
                return CDS, [("Result", didl), ("NumberReturned", n),
                             ("TotalMatches", total), ("UpdateID", self.update_id)]
        elif control_path.endswith("/cms"):
            if action == "GetProtocolInfo":
                return CMS, [("Source", SOURCE_PROTOCOLS), ("Sink", "")]
            if action == "GetCurrentConnectionIDs":
                return CMS, [("ConnectionIDs", "0")]
            if action == "GetCurrentConnectionInfo":
                return CMS, [("RcsID", -1), ("AVTransportID", -1), ("ProtocolInfo", ""),
                             ("PeerConnectionManager", ""), ("PeerConnectionID", -1),
                             ("Direction", "Output"), ("Status", "OK")]
        raise UpnpError(401, "Invalid Action")


class _MediaHandler(_Handler):
    def _resolve(self) -> Optional[str]:
        parts = self.path.split("?")[0].split("/")
        if len(parts) >= 3 and parts[1] == "m":
            return self.server.media.path_for(parts[2])
        return None

    def _send_xml(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("EXT", "")
        self.send_header("Server", SERVER_STR)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, head: bool) -> bool:
        m = self.server.media
        route = self.path.split("?")[0]
        docs = {"/desc.xml": lambda: m.device_xml(), "/cds.xml": lambda: CDS_SCPD,
                "/cms.xml": lambda: CMS_SCPD}
        if route in docs:
            body = docs[route]().encode("utf-8")
            if head:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
            else:
                self._send_xml(body)
            return True
        return False

    def do_GET(self) -> None:
        if not self._static(False):
            self._serve(head=False)

    def do_HEAD(self) -> None:
        if not self._static(True):
            self._serve(head=True)

    def do_SUBSCRIBE(self) -> None:  # olay aboneliği: kabul et ama olay gönderme
        self.send_response(200)
        self.send_header("SID", f"uuid:{random.getrandbits(64):016x}")
        self.send_header("TIMEOUT", "Second-1800")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_UNSUBSCRIBE(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        m = self.server.media
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            root = ET.fromstring(body)
            soap_body = next(c for c in root if c.tag.endswith("Body"))
            act = soap_body[0]
            action = act.tag.rsplit("}", 1)[-1]
            args = {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in act}
        except Exception:  # noqa: BLE001
            self.send_error(400)
            return
        base = self.headers.get("Host") or f"{m.ip}:{m.port}"
        try:
            ns, out = m.handle(self.path.split("?")[0], action, args, base)
        except UpnpError as e:
            self._send_xml(soap_fault(e.code, e.text), 500)
            return
        except Exception as e:  # noqa: BLE001
            self._send_xml(soap_fault(501, f"Action failed: {e}"), 500)
            return
        self._send_xml(soap_response(action, ns, out))


# ---------------- SSDP ----------------
class _Ssdp(threading.Thread):
    def __init__(self, media, types: Optional[List[str]] = None) -> None:
        """media: ip, udn, location özellikleri olan nesne (MediaServer veya MediaRenderer).
        types: duyurulacak aygıt/servis türleri; verilmezse MediaServer'inkiler."""
        super().__init__(daemon=True)
        self.media = media
        self._types = types
        self._halt = threading.Event()
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            self._tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(media.ip))
        except OSError:
            pass

    def targets(self) -> List[str]:
        types = self._types if self._types is not None else [DEVICE_TYPE, CDS, CMS]
        return ["upnp:rootdevice", f"uuid:{self.media.udn}", *types]

    def usn(self, t: str) -> str:
        u = f"uuid:{self.media.udn}"
        return u if t == u else f"{u}::{t}"

    def _notify(self, nts: str) -> None:
        for t in self.targets():
            msg = ("NOTIFY * HTTP/1.1\r\n"
                   f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
                   "CACHE-CONTROL: max-age=1800\r\n"
                   + (f"LOCATION: {self.media.location}\r\n" if nts == "ssdp:alive" else "")
                   + f"NT: {t}\r\nNTS: {nts}\r\nSERVER: {SERVER_STR}\r\nUSN: {self.usn(t)}\r\n\r\n")
            try:
                self._tx.sendto(msg.encode(), (SSDP_GROUP, SSDP_PORT))
            except OSError:
                pass

    def _respond(self, st: str, addr) -> None:
        msg = ("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\n"
               f"LOCATION: {self.media.location}\r\nSERVER: {SERVER_STR}\r\n"
               f"ST: {st}\r\nUSN: {self.usn(st)}\r\n\r\n")
        try:
            self._tx.sendto(msg.encode(), addr)
        except OSError:
            pass

    def handle_packet(self, data: bytes, addr) -> None:
        text = data.decode("utf-8", "ignore")
        lines = text.split("\r\n")
        if not lines or not lines[0].upper().startswith("M-SEARCH"):
            return
        hdr = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                hdr[k.strip().upper()] = v.strip()
        if "ssdp:discover" not in hdr.get("MAN", "").lower():
            return
        st = hdr.get("ST", "")
        try:
            mx = max(1, min(int(hdr.get("MX", "2")), 5))
        except ValueError:
            mx = 2
        wanted = self.targets() if st == "ssdp:all" else [t for t in self.targets() if t == st]
        for t in wanted:
            threading.Timer(random.uniform(0, min(mx, 2)), self._respond, (t, addr)).start()

    def run(self) -> None:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            rx.bind(("", SSDP_PORT))
        except OSError as exc:
            print(f"SSDP portu (1900) acilamadi: {exc}", file=sys.stderr)
            return
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(SSDP_GROUP), socket.inet_aton(self.media.ip))
            rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            try:
                mreq = struct.pack("4sl", socket.inet_aton(SSDP_GROUP), socket.INADDR_ANY)
                rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError as exc:
                print(f"Multicast gruba katilinamadi: {exc}", file=sys.stderr)
        rx.settimeout(1.0)
        self._notify("ssdp:alive")
        last = 0.0
        ticks = 0
        while not self._halt.is_set():
            try:
                data, addr = rx.recvfrom(4096)
                self.handle_packet(data, addr)
            except socket.timeout:
                ticks += 1
                if ticks - last >= 900:
                    self._notify("ssdp:alive")
                    last = ticks
            except OSError:
                break
        rx.close()

    def shutdown(self) -> None:
        self._notify("ssdp:byebye")
        self._halt.set()
