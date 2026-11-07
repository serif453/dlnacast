"""UPnP/DLNA MediaRenderer: ağdaki telefon/bilgisayar bu programı "TV" gibi görür,
gönderdikleri video ve müziği bu bilgisayarda mpv ile oynatırız.

Sadece Python standart kütüphanesini kullanır. HTTP istekleri kendi thread'lerinde gelir;
mpv işleri AsyncBridge'in asyncio döngüsüne aktarılır (bkz. MediaRenderer._run).
"""
import concurrent.futures
import asyncio
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit
from xml.sax.saxutils import escape

from .localplayer import LocalPlayer
from .localserver import lan_ip
from .olay import EventManager, Snapshot, SubscribeError
from .mediaserver import CMS, CMS_SCPD, SERVER_STR, UpnpError, _Ssdp, scpd_xml, soap_fault, soap_response

RENDERER_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RCS = "urn:schemas-upnp-org:service:RenderingControl:1"
DC_TITLE = "{http://purl.org/dc/elements/1.1/}title"

MAX_BODY = 1_000_000  # SOAP istekleri küçüktür; bundan büyüğünü reddet
CALL_TIMEOUT = 12.0  # mpv'den yanıt için en fazla bekleme (sn)
PLAYING_STATES = ("PLAYING", "TRANSITIONING")
ACTIVE_STATES = ("PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK")

SINK_PROTOCOLS = ",".join(
    f"http-get:*:{m}:*" for m in (
        "video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/quicktime",
        "video/mpeg", "video/mp2t", "video/x-ms-wmv", "video/3gpp", "video/x-flv",
        "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/flac", "audio/x-flac",
        "audio/ogg", "audio/opus", "audio/wav", "audio/x-wav", "audio/L16",
        "application/ogg", "application/vnd.apple.mpegurl", "application/x-mpegURL"))


# ---------------- yardımcılar ----------------
def fmt_hms(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_hms(text) -> Optional[float]:
    """"0:01:23" / "00:01:23.500" -> saniye. Geçersiz ya da negatifse None."""
    t = str(text).strip()
    if t.startswith("-"):
        return None
    try:
        h, m, s = t.lstrip("+").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None


def didl_title(meta: str) -> str:
    """DIDL-Lite üstverisinden dc:title değerini çıkarır (yoksa boş)."""
    if not meta or meta == "NOT_IMPLEMENTED":
        return ""
    try:
        root = ET.fromstring(meta)
    except ET.ParseError:
        return ""
    for el in root.iter(DC_TITLE):
        if el.text:
            return el.text.strip()
    return ""


def title_from_uri(uri: str) -> str:
    name = unquote(urlsplit(uri).path.rsplit("/", 1)[-1])
    return name or uri


def truthy(text: str) -> bool:
    return str(text).strip().lower() in ("1", "true", "yes")


# ---------------- servis tanımları (SCPD) ----------------
_IID = "A_ARG_TYPE_InstanceID"

AVT_SCPD = scpd_xml(
    {
        "SetAVTransportURI": [(
            "InstanceID", "in", _IID), ("CurrentURI", "in", "AVTransportURI"),
            ("CurrentURIMetaData", "in", "AVTransportURIMetaData")],
        "SetNextAVTransportURI": [(
            "InstanceID", "in", _IID), ("NextURI", "in", "NextAVTransportURI"),
            ("NextURIMetaData", "in", "NextAVTransportURIMetaData")],
        "GetMediaInfo": [
            ("InstanceID", "in", _IID), ("NrTracks", "out", "NumberOfTracks"),
            ("MediaDuration", "out", "CurrentMediaDuration"),
            ("CurrentURI", "out", "AVTransportURI"),
            ("CurrentURIMetaData", "out", "AVTransportURIMetaData"),
            ("NextURI", "out", "NextAVTransportURI"),
            ("NextURIMetaData", "out", "NextAVTransportURIMetaData"),
            ("PlayMedium", "out", "PlaybackStorageMedium"),
            ("RecordMedium", "out", "RecordStorageMedium"),
            ("WriteStatus", "out", "RecordMediumWriteStatus")],
        "GetTransportInfo": [
            ("InstanceID", "in", _IID), ("CurrentTransportState", "out", "TransportState"),
            ("CurrentTransportStatus", "out", "TransportStatus"),
            ("CurrentSpeed", "out", "TransportPlaySpeed")],
        "GetPositionInfo": [
            ("InstanceID", "in", _IID), ("Track", "out", "CurrentTrack"),
            ("TrackDuration", "out", "CurrentTrackDuration"),
            ("TrackMetaData", "out", "CurrentTrackMetaData"),
            ("TrackURI", "out", "CurrentTrackURI"),
            ("RelTime", "out", "RelativeTimePosition"),
            ("AbsTime", "out", "AbsoluteTimePosition"),
            ("RelCount", "out", "RelativeCounterPosition"),
            ("AbsCount", "out", "AbsoluteCounterPosition")],
        "GetDeviceCapabilities": [
            ("InstanceID", "in", _IID), ("PlayMedia", "out", "PossiblePlaybackStorageMedia"),
            ("RecMedia", "out", "PossibleRecordStorageMedia"),
            ("RecQualityModes", "out", "PossibleRecordQualityModes")],
        "GetTransportSettings": [
            ("InstanceID", "in", _IID), ("PlayMode", "out", "CurrentPlayMode"),
            ("RecQualityMode", "out", "CurrentRecordQualityMode")],
        "Stop": [("InstanceID", "in", _IID)],
        "Play": [("InstanceID", "in", _IID), ("Speed", "in", "TransportPlaySpeed")],
        "Pause": [("InstanceID", "in", _IID)],
        "Seek": [("InstanceID", "in", _IID), ("Unit", "in", "A_ARG_TYPE_SeekMode"),
                 ("Target", "in", "A_ARG_TYPE_SeekTarget")],
        "Next": [("InstanceID", "in", _IID)],
        "Previous": [("InstanceID", "in", _IID)],
        "SetPlayMode": [("InstanceID", "in", _IID), ("NewPlayMode", "in", "CurrentPlayMode")],
        "GetCurrentTransportActions": [("InstanceID", "in", _IID),
                                       ("Actions", "out", "CurrentTransportActions")],
    },
    [
        ("TransportState", "string", "no", ["STOPPED", "PLAYING", "PAUSED_PLAYBACK",
                                            "TRANSITIONING", "NO_MEDIA_PRESENT"]),
        ("TransportStatus", "string", "no", ["OK", "ERROR_OCCURRED"]),
        ("PlaybackStorageMedium", "string", "no", None),
        ("RecordStorageMedium", "string", "no", None),
        ("PossiblePlaybackStorageMedia", "string", "no", None),
        ("PossibleRecordStorageMedia", "string", "no", None),
        ("CurrentPlayMode", "string", "no", ["NORMAL"]),
        ("TransportPlaySpeed", "string", "no", ["1"]),
        ("RecordMediumWriteStatus", "string", "no", None),
        ("CurrentRecordQualityMode", "string", "no", None),
        ("PossibleRecordQualityModes", "string", "no", None),
        ("NumberOfTracks", "ui4", "no", None),
        ("CurrentTrack", "ui4", "no", None),
        ("CurrentTrackDuration", "string", "no", None),
        ("CurrentMediaDuration", "string", "no", None),
        ("CurrentTrackMetaData", "string", "no", None),
        ("CurrentTrackURI", "string", "no", None),
        ("AVTransportURI", "string", "no", None),
        ("AVTransportURIMetaData", "string", "no", None),
        ("NextAVTransportURI", "string", "no", None),
        ("NextAVTransportURIMetaData", "string", "no", None),
        ("RelativeTimePosition", "string", "no", None),
        ("AbsoluteTimePosition", "string", "no", None),
        ("RelativeCounterPosition", "i4", "no", None),
        ("AbsoluteCounterPosition", "ui4", "no", None),
        ("CurrentTransportActions", "string", "no", None),
        ("LastChange", "string", "yes", None),
        ("A_ARG_TYPE_SeekMode", "string", "no", ["REL_TIME", "ABS_TIME", "TRACK_NR"]),
        ("A_ARG_TYPE_SeekTarget", "string", "no", None),
        (_IID, "ui4", "no", None),
    ],
)

RCS_SCPD = scpd_xml(
    {
        "ListPresets": [("InstanceID", "in", _IID),
                        ("CurrentPresetNameList", "out", "PresetNameList")],
        "SelectPreset": [("InstanceID", "in", _IID), ("PresetName", "in", "A_ARG_TYPE_PresetName")],
        "GetMute": [("InstanceID", "in", _IID), ("Channel", "in", "A_ARG_TYPE_Channel"),
                    ("CurrentMute", "out", "Mute")],
        "SetMute": [("InstanceID", "in", _IID), ("Channel", "in", "A_ARG_TYPE_Channel"),
                    ("DesiredMute", "in", "Mute")],
        "GetVolume": [("InstanceID", "in", _IID), ("Channel", "in", "A_ARG_TYPE_Channel"),
                      ("CurrentVolume", "out", "Volume")],
        "SetVolume": [("InstanceID", "in", _IID), ("Channel", "in", "A_ARG_TYPE_Channel"),
                      ("DesiredVolume", "in", "Volume")],
    },
    [
        ("PresetNameList", "string", "no", None),
        ("LastChange", "string", "yes", None),
        ("Mute", "boolean", "no", None),
        ("Volume", "ui2", "no", (0, 100, 1)),
        ("A_ARG_TYPE_Channel", "string", "no", ["Master"]),
        ("A_ARG_TYPE_InstanceID", "ui4", "no", None),
        ("A_ARG_TYPE_PresetName", "string", "no", ["FactoryDefaults"]),
    ],
)


# ---------------- Renderer ----------------
class MediaRenderer:
    def __init__(self, name: str, udn: str, loop: asyncio.AbstractEventLoop,
                 player: Optional[LocalPlayer] = None, ip: str = "") -> None:
        self.name, self.udn = name, udn
        self.loop = loop  # mpv işlerinin çalışacağı asyncio döngüsü (AsyncBridge.loop)
        # Uygulamanın "Bu bilgisayar (mpv)" hedefinden ayrı bir mpv penceresi kullanır.
        self.player = player or LocalPlayer(tag="-alici", title="DLNA Cast (alıcı)")
        self.ip = ip
        self.port = 0
        self.on_message: Optional[Callable[[str], None]] = None  # arayüze kısa bilgi
        self._lock = threading.Lock()  # durumu değiştiren işlemler sırayla çalışsın
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._ssdp: Optional[_Ssdp] = None
        self.uri = ""
        self.meta = ""
        self.title = ""
        self.next_uri = ""
        self.next_meta = ""
        self._loaded_uri = ""  # mpv'ye en son yüklenen adres
        self.volume = 100
        self.mute = False
        self.events = EventManager(self._snapshot)  # abonelere durum değişikliği bildirimi

    # ---- yaşam döngüsü ----
    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def location(self) -> str:
        return f"http://{self.ip}:{self.port}/desc.xml"

    def start(self, advertise: bool = True) -> None:
        if self._httpd is not None:
            return
        if not self.ip:
            self.ip = lan_ip()
        httpd = ThreadingHTTPServer(("0.0.0.0", 0), _RendererHandler)
        httpd.daemon_threads = True
        httpd.renderer = self
        self._httpd = httpd
        self.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.events = EventManager(self._snapshot)
        self.events.start()
        if advertise:
            self._ssdp = _Ssdp(self, types=[RENDERER_TYPE, AVT, RCS, CMS])
            self._ssdp.start()

    def stop(self) -> None:
        self.events.stop()
        if self._ssdp is not None:
            self._ssdp.shutdown()
            self._ssdp = None
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self.player.close()
        self.uri = self.meta = self.title = self._loaded_uri = ""

    # ---- yardımcılar ----
    def _say(self, text: str) -> None:
        if self.on_message is not None:
            try:
                self.on_message(text)
            except Exception:  # noqa: BLE001
                pass

    def _run(self, coro):
        """Coroutine'i asyncio döngüsünde çalıştırıp sonucu bekler (HTTP thread'inden çağrılır)."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(CALL_TIMEOUT)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise UpnpError(501, "Oynatıcı yanıt vermedi")

    def _status(self, with_volume: bool = False) -> Tuple[float, float, str, Optional[int]]:
        return self._run(self.player.status(with_volume))

    def _state(self) -> str:
        state = self._status()[2]
        if state == "STOPPED" and not self.uri:
            return "NO_MEDIA_PRESENT"
        return state

    def _wait_ready(self, timeout: float = 5.0) -> str:
        """Yükleniyor (TRANSITIONING) durumundan çıkana kadar bekler; son durumu döndürür."""
        end = time.monotonic() + timeout
        state = self._status()[2]
        while state == "TRANSITIONING" and time.monotonic() < end:
            time.sleep(0.2)
            state = self._status()[2]
        return state

    @staticmethod
    def _actions(state: str, has_uri: bool) -> str:
        return {"PLAYING": "Pause,Stop,Seek", "TRANSITIONING": "Stop",
                "PAUSED_PLAYBACK": "Play,Stop,Seek"}.get(state, "Play" if has_uri else "")

    def _snapshot(self) -> Snapshot:
        """Olaylarla bildirilen değişkenlerin anlık değerleri (servis -> değişken -> değer)."""
        alive = getattr(self.player, "alive", True)
        _pos, dur, state, vol = self._status(with_volume=alive)
        uri, meta = self.uri, self.meta
        if state == "STOPPED" and not uri:
            state = "NO_MEDIA_PRESENT"
        if vol is None:
            vol = self.volume
        d = fmt_hms(dur)
        n = "1" if uri else "0"
        return {
            "avt": {
                "TransportState": state, "TransportStatus": "OK",
                "CurrentPlayMode": "NORMAL", "TransportPlaySpeed": "1",
                "PlaybackStorageMedium": "NETWORK" if uri else "NONE",
                "NumberOfTracks": n, "CurrentTrack": n,
                "CurrentTrackDuration": d, "CurrentMediaDuration": d,
                "CurrentTrackURI": uri, "AVTransportURI": uri,
                "CurrentTrackMetaData": meta, "AVTransportURIMetaData": meta,
                "NextAVTransportURI": self.next_uri, "NextAVTransportURIMetaData": self.next_meta,
                "CurrentTransportActions": self._actions(state, bool(uri)),
            },
            "rcs": {"Volume": str(max(0, min(100, int(vol)))), "Mute": "1" if self.mute else "0",
                    "PresetNameList": "FactoryDefaults"},
            "cms": {"SourceProtocolInfo": "", "SinkProtocolInfo": SINK_PROTOCOLS,
                    "CurrentConnectionIDs": "0"},
        }

    def _apply_audio(self) -> None:
        try:
            self._run(self.player.set_volume(self.volume))
            self._run(self.player.set_mute(self.mute))
        except Exception:  # noqa: BLE001  (ses ayarı oynatmayı bozmasın)
            pass

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
            "<dlna:X_DLNADOC>DMR-1.50</dlna:X_DLNADOC>"
            f"<deviceType>{RENDERER_TYPE}</deviceType>"
            f"<friendlyName>{escape(self.name)}</friendlyName>"
            "<manufacturer>DLNA Cast</manufacturer><modelName>DLNA Cast Renderer</modelName>"
            "<modelNumber>1</modelNumber>"
            f"<UDN>uuid:{self.udn}</UDN><serviceList>"
            + svc(AVT, "AVTransport", "avt") + svc(RCS, "RenderingControl", "rcs")
            + svc(CMS, "ConnectionManager", "cms")
            + "</serviceList></device></root>"
        )

    # ---- SOAP yönlendirme ----
    def handle(self, control_path: str, action: str, args: Dict[str, str]):
        try:
            return self._route(control_path, action, args)
        finally:
            if not action.startswith("Get"):  # durumu değiştiren komut: abonelere hemen bildir
                self.events.changed()

    def _route(self, control_path: str, action: str, args: Dict[str, str]):
        if control_path.endswith("/avt"):
            self._check_instance(args)
            return AVT, self._avt(action, args)
        if control_path.endswith("/rcs"):
            self._check_instance(args)
            return RCS, self._rcs(action, args)
        if control_path.endswith("/cms"):
            return CMS, self._cms(action, args)
        raise UpnpError(401, "Invalid Action")

    @staticmethod
    def _check_instance(args: Dict[str, str]) -> None:
        if args.get("InstanceID", "0").strip() not in ("0", ""):
            raise UpnpError(718, "Invalid InstanceID")

    # ---- AVTransport ----
    def _avt(self, action: str, args: Dict[str, str]) -> List[Tuple[str, object]]:
        if action == "GetTransportInfo":
            return [("CurrentTransportState", self._state()),
                    ("CurrentTransportStatus", "OK"), ("CurrentSpeed", "1")]
        if action == "GetPositionInfo":
            pos, dur, _state, _vol = self._status()
            t = fmt_hms(pos)
            return [("Track", 1 if self.uri else 0), ("TrackDuration", fmt_hms(dur)),
                    ("TrackMetaData", self.meta), ("TrackURI", self.uri),
                    ("RelTime", t), ("AbsTime", t),
                    ("RelCount", 2147483647), ("AbsCount", 2147483647)]
        if action == "GetMediaInfo":
            dur = self._status()[1]
            return [("NrTracks", 1 if self.uri else 0), ("MediaDuration", fmt_hms(dur)),
                    ("CurrentURI", self.uri), ("CurrentURIMetaData", self.meta),
                    ("NextURI", self.next_uri), ("NextURIMetaData", self.next_meta),
                    ("PlayMedium", "NETWORK"), ("RecordMedium", "NOT_IMPLEMENTED"),
                    ("WriteStatus", "NOT_IMPLEMENTED")]
        if action == "GetDeviceCapabilities":
            return [("PlayMedia", "NETWORK"), ("RecMedia", "NOT_IMPLEMENTED"),
                    ("RecQualityModes", "NOT_IMPLEMENTED")]
        if action == "GetTransportSettings":
            return [("PlayMode", "NORMAL"), ("RecQualityMode", "NOT_IMPLEMENTED")]
        if action == "GetCurrentTransportActions":
            return [("Actions", self._actions(self._state(), bool(self.uri)))]

        with self._lock:  # buradan sonrakiler durumu değiştirir
            if action == "SetAVTransportURI":
                uri = args.get("CurrentURI", "").strip()
                # Güvenlik: sadece ağ adresleri; yerel dosya yolu veya mpv'nin özel şemaları olmaz.
                if uri and not uri.lower().startswith(("http://", "https://")):
                    raise UpnpError(716, "Sadece http/https adresleri desteklenir")
                self.uri = uri
                self.meta = args.get("CurrentURIMetaData", "")
                self.title = didl_title(self.meta) or (title_from_uri(uri) if uri else "")
                self.next_uri = self.next_meta = ""
                return []
            if action == "SetNextAVTransportURI":  # sıradaki parça henüz kullanılmıyor, saklanır
                self.next_uri = args.get("NextURI", "").strip()
                self.next_meta = args.get("NextURIMetaData", "")
                return []
            if action == "Play":
                return self._play()
            if action == "Pause":
                state = self._status()[2]
                if state in PLAYING_STATES:
                    self._run(self.player.control("pause"))
                elif state != "PAUSED_PLAYBACK":
                    raise UpnpError(701, "Transition not available")
                return []
            if action == "Stop":
                self._run(self.player.control("stop"))
                self._loaded_uri = ""
                return []
            if action == "Seek":
                return self._seek(args)
            if action == "SetPlayMode":
                if args.get("NewPlayMode", "NORMAL") != "NORMAL":
                    raise UpnpError(712, "Play mode not supported")
                return []
            if action in ("Next", "Previous"):
                raise UpnpError(701, "Transition not available")
        raise UpnpError(401, "Invalid Action")

    def _play(self) -> List[Tuple[str, object]]:
        if not self.uri:
            raise UpnpError(701, "Oynatılacak adres yok")
        state = self._status()[2]
        if self._loaded_uri == self.uri:
            if state == "PAUSED_PLAYBACK":
                self._run(self.player.control("resume"))
                return []
            if state in PLAYING_STATES:
                return []
        try:
            self._run(self.player.play(self.uri, None, self.title))
        except UpnpError:
            raise
        except Exception as exc:  # noqa: BLE001  (örn. mpv kurulu değil)
            self._say(f"Alıcı: oynatılamadı: {exc}")
            raise UpnpError(501, f"Oynatılamadı: {exc}")
        self._loaded_uri = self.uri
        self._apply_audio()
        self._say(f"Alıcı: {self.title or self.uri} oynatılıyor")
        return []

    def _seek(self, args: Dict[str, str]) -> List[Tuple[str, object]]:
        unit = args.get("Unit", "REL_TIME")
        target = args.get("Target", "")
        if unit in ("REL_TIME", "ABS_TIME"):
            secs = parse_hms(target)
            if secs is None:
                raise UpnpError(711, "Illegal seek target")
        elif unit == "TRACK_NR":
            if target.strip() not in ("0", "1"):
                raise UpnpError(711, "Illegal seek target")
            secs = 0.0
        else:
            raise UpnpError(710, "Seek mode not supported")
        state = self._wait_ready()
        if state not in ACTIVE_STATES:
            raise UpnpError(701, "Transition not available")
        self._run(self.player.seek(secs))
        return []

    # ---- RenderingControl ----
    def _rcs(self, action: str, args: Dict[str, str]) -> List[Tuple[str, object]]:
        if action == "GetVolume":
            vol = self.volume
            if getattr(self.player, "alive", True):
                got = self._status(with_volume=True)[3]
                if got is not None:
                    vol = got
            return [("CurrentVolume", max(0, min(100, int(vol))))]
        if action == "SetVolume":
            try:
                level = max(0, min(100, int(args.get("DesiredVolume", ""))))
            except ValueError:
                raise UpnpError(402, "Invalid args")
            self.volume = level
            if getattr(self.player, "alive", True):
                self._run(self.player.set_volume(level))
            return []
        if action == "GetMute":
            return [("CurrentMute", 1 if self.mute else 0)]
        if action == "SetMute":
            self.mute = truthy(args.get("DesiredMute", "0"))
            if getattr(self.player, "alive", True):
                self._run(self.player.set_mute(self.mute))
            return []
        if action == "ListPresets":
            return [("CurrentPresetNameList", "FactoryDefaults")]
        if action == "SelectPreset":
            return []
        raise UpnpError(401, "Invalid Action")

    # ---- ConnectionManager ----
    def _cms(self, action: str, args: Dict[str, str]) -> List[Tuple[str, object]]:
        if action == "GetProtocolInfo":
            return [("Source", ""), ("Sink", SINK_PROTOCOLS)]
        if action == "GetCurrentConnectionIDs":
            return [("ConnectionIDs", "0")]
        if action == "GetCurrentConnectionInfo":
            return [("RcsID", 0), ("AVTransportID", 0), ("ProtocolInfo", ""),
                    ("PeerConnectionManager", ""), ("PeerConnectionID", -1),
                    ("Direction", "Input"), ("Status", "OK")]
        raise UpnpError(401, "Invalid Action")


# ---------------- HTTP ----------------
class _RendererHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # sessiz
        pass

    def _send_xml(self, body: bytes, status: int = 200, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("EXT", "")
        self.send_header("Server", SERVER_STR)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _static(self, head: bool) -> None:
        r = self.server.renderer
        docs = {"/desc.xml": r.device_xml, "/avt.xml": lambda: AVT_SCPD,
                "/rcs.xml": lambda: RCS_SCPD, "/cms.xml": lambda: CMS_SCPD}
        fn = docs.get(self.path.split("?")[0])
        if fn is None:
            self.send_error(404)
            return
        self._send_xml(fn().encode("utf-8"), head=head)

    def do_GET(self) -> None:
        self._static(False)

    def do_HEAD(self) -> None:
        self._static(True)

    def _event_service(self) -> str:
        path = self.path.split("?")[0]
        svc = path[len("/event/"):] if path.startswith("/event/") else ""
        return svc if svc in ("avt", "rcs", "cms") else ""

    def _empty(self, status: int, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Server", SERVER_STR)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_SUBSCRIBE(self) -> None:  # olay aboneliği (yeni ya da yenileme)
        svc = self._event_service()
        if not svc:
            self._empty(404)
            return
        try:
            sid, timeout = self.server.renderer.events.subscribe(
                svc, self.client_address[0], self.headers)
        except SubscribeError as e:
            self._empty(e.status)
            return
        self._empty(200, {"SID": sid, "TIMEOUT": f"Second-{timeout}"})

    def do_UNSUBSCRIBE(self) -> None:
        if not self._event_service():
            self._empty(404)
            return
        try:
            self.server.renderer.events.unsubscribe(self.headers)
        except SubscribeError as e:
            self._empty(e.status)
            return
        self._empty(200)

    def do_POST(self) -> None:
        r = self.server.renderer
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.close_connection = True
            self.send_error(413 if length > MAX_BODY else 400)
            return
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
        try:
            ns, out = r.handle(self.path.split("?")[0], action, args)
        except UpnpError as e:
            self._send_xml(soap_fault(e.code, e.text), 500)
            return
        except Exception as e:  # noqa: BLE001
            self._send_xml(soap_fault(501, f"Action failed: {e}"), 500)
            return
        self._send_xml(soap_response(action, ns, out))
