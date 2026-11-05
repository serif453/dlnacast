"""UPnP/DLNA control point: cihaz keşfi, ContentDirectory gezintisi, renderer kontrolü."""
import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.search import async_search

from .cast import CastManager
from .localplayer import LocalPlayer

MEDIA_SERVER = "urn:schemas-upnp-org:device:MediaServer:1"
MEDIA_RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
CONTENT_DIRECTORY = "urn:schemas-upnp-org:service:ContentDirectory:1"
AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
LOCAL_PLAYER_ID = "player://this-computer"  # "Bu bilgisayar (mpv)" hedefi
LOCAL_ID = "local://this-computer"  # uygulamanin kendi media server'i (SSDP gerektirmez)

NS = {
    "d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
}


@dataclass
class DeviceInfo:
    name: str
    location: str  # cihaz açıklama XML'inin URL'si, aynı zamanda kimlik
    kind: str  # "server" | "renderer" | "cast"


@dataclass
class Entry:
    id: str
    title: str
    is_container: bool
    uri: Optional[str] = None
    mime: Optional[str] = None
    upnp_class: str = ""
    thumb: Optional[str] = None  # küçük resim adresi (albüm kapağı ya da sunucunun ürettiği)


@dataclass
class PlayStatus:
    position: float = 0.0  # saniye
    duration: float = 0.0  # saniye (0 = bilinmiyor)
    state: str = ""  # PLAYING | PAUSED_PLAYBACK | STOPPED | TRANSITIONING | NO_MEDIA_PRESENT
    volume: Optional[int] = None  # 0-100, bilinmiyorsa None


def parse_hms(text) -> float:
    """"0:01:23" / "00:01:23.500" -> saniye. Geçersizse (NOT_IMPLEMENTED vb.) 0."""
    try:
        h, m, s = str(text).split(":")
        return max(0.0, int(h) * 3600 + int(m) * 60 + float(s))
    except (ValueError, TypeError):
        return 0.0


def fmt_hms(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_didl(xml_text: str) -> List[Entry]:
    entries: List[Entry] = []
    if not xml_text:
        return entries
    root = ET.fromstring(xml_text)
    for el in root:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("container", "item"):
            continue
        uri = mime = None
        for res in el.findall("d:res", NS):
            if res.text and res.text.strip():
                uri = res.text.strip()
                parts = res.get("protocolInfo", "").split(":")
                mime = parts[2] if len(parts) > 2 else None
                break
        thumb = (el.findtext("upnp:albumArtURI", default="", namespaces=NS) or "").strip() or None
        if thumb is None:  # bazı sunucular küçük resmi ayrı bir image/* <res> olarak verir
            for res in el.findall("d:res", NS):
                parts = res.get("protocolInfo", "").split(":")
                if len(parts) > 2 and parts[2].startswith("image/") and (res.text or "").strip():
                    thumb = res.text.strip()
                    break
        entries.append(
            Entry(
                id=el.get("id", ""),
                title=el.findtext("dc:title", default="?", namespaces=NS),
                is_container=(tag == "container"),
                uri=uri,
                mime=mime,
                upnp_class=el.findtext("upnp:class", default="", namespaces=NS),
                thumb=thumb,
            )
        )
    return entries


def check_local_uri(uri: str) -> None:
    """Bu bilgisayarda oynatılacak adresi denetler: sadece http(s) ya da var olan yerel dosya.

    Ağdaki bir sunucu kötü niyetle mpv'nin özel adreslerini (av://, edl:// ...) gönderemesin.
    """
    if uri.lower().startswith(("http://", "https://")):
        return
    if os.path.isabs(uri) and os.path.isfile(uri):
        return
    raise RuntimeError("Bu adres bu bilgisayarda oynatılamaz (sadece ağ adresleri ve yerel dosyalar).")


class ControlPoint:
    """Tüm metotlar asyncio döngüsü içinde çalışır (AsyncBridge ile çağır)."""

    def __init__(self) -> None:
        self._factory: Optional[UpnpFactory] = None
        self._devices: Dict[str, object] = {}
        self.cast = CastManager()
        self.local_media = None  # ayni surecteki MediaServer
        self.player = LocalPlayer()  # bu bilgisayarda oynatma (mpv)
        self.errors: List[str] = []  # son aramadaki uyarilar
        self.cast_hosts: List[str] = []  # bilinen Chromecast IP'leri (arayüz config'ten verir)

    def _ensure(self) -> None:
        if self._factory is None:  # aiohttp oturumu çalışan döngüde oluşmalı
            self._factory = UpnpFactory(AiohttpRequester())

    async def discover(self, on_found: Callable[[DeviceInfo], None], timeout: int = 5) -> int:
        self._ensure()
        seen = set()
        tasks: List[asyncio.Task] = []

        async def on_response(headers) -> None:
            loc = headers.get("location")
            if loc and loc not in seen:
                seen.add(loc)
                tasks.append(asyncio.create_task(self._describe(loc, on_found)))

        self.errors = []
        targets = (MEDIA_SERVER, MEDIA_RENDERER)
        results = await asyncio.gather(
            self._discover_cast(on_found, timeout),
            *(
                async_search(async_callback=on_response, timeout=timeout, search_target=st)
                for st in targets
            ),
            return_exceptions=True,  # biri patlasa da digerlerinin sonucu kaybolmasin
        )
        labels = ["Chromecast"] + [f"SSDP {t.split(':')[-2]}" for t in targets]
        for label, res in zip(labels, results):
            if isinstance(res, BaseException):
                self.errors.append(f"{label}: {res!r}")
                print(f"{label} aramasi basarisiz: {res!r}", file=sys.stderr)
        await asyncio.gather(*tasks, return_exceptions=True)
        return len(seen)

    async def _discover_cast(self, on_found: Callable[[DeviceInfo], None], timeout: int) -> None:
        try:
            for name, key in await self.cast.discover(self.cast_hosts):
                on_found(DeviceInfo(name, key, "cast"))
        except ImportError:
            print("pychromecast kurulu degil, Chromecast atlandi.", file=sys.stderr)
            self.errors.append("Chromecast: pychromecast kurulu degil")
        except Exception as exc:  # noqa: BLE001
            print(f"Chromecast aramasi basarisiz: {exc!r}", file=sys.stderr)
            self.errors.append(f"Chromecast: {exc!r}")

    async def _describe(self, location: str, on_found: Callable[[DeviceInfo], None]) -> None:
        try:
            dev = await self._factory.async_create_device(location)
        except Exception as exc:  # noqa: BLE001  (bozuk/erişilemeyen cihazları atla)
            print(f"Cihaz aciklamasi alinamadi ({location}): {exc!r}", file=sys.stderr)
            self.errors.append(f"{location}: {exc!r}")
            return
        if "MediaServer" in dev.device_type:
            kind = "server"
        elif "MediaRenderer" in dev.device_type:
            kind = "renderer"
        else:
            return
        self._devices[location] = dev
        on_found(DeviceInfo(dev.friendly_name, location, kind))

    async def browse(self, location: str, object_id: str = "0") -> List[Entry]:
        if location == LOCAL_ID:  # kendi paylasimimiz: aga cikmadan dogrudan oku
            ms = self.local_media
            if ms is None or not ms.running:
                raise RuntimeError("Paylasim sunucusu calismiyor (once bir klasor paylas).")
            didl, _n, _total = ms.browse(object_id, "BrowseDirectChildren", 0, 0,
                                         f"{ms.ip}:{ms.port}")
            return parse_didl(didl)
        dev = self._devices[location]
        action = dev.service(CONTENT_DIRECTORY).action("Browse")
        entries: List[Entry] = []
        start = 0
        while True:
            resp = await action.async_call(
                ObjectID=object_id,
                BrowseFlag="BrowseDirectChildren",
                Filter="*",
                StartingIndex=start,
                RequestedCount=200,
                SortCriteria="",
            )
            page = parse_didl(resp.get("Result", ""))
            entries.extend(page)
            returned = int(resp.get("NumberReturned", 0) or 0)
            total = int(resp.get("TotalMatches", 0) or 0)
            start += returned
            if returned == 0 or (total and start >= total):
                return entries

    async def play(self, renderer_location: str, uri: str,
                   mime: Optional[str] = None, title: str = "") -> None:
        if renderer_location == LOCAL_PLAYER_ID:
            check_local_uri(uri)
            await self.player.play(uri, mime, title)
            return
        if self.cast.has(renderer_location):
            await self.cast.play(renderer_location, uri, mime, title)
            return
        dev = self._devices[renderer_location]
        svc = dev.service(AV_TRANSPORT)
        # Bazı TV'ler oynatırken yeni adres kabul etmez: önce durdur, durmasını bekle.
        await self._stop_and_wait(svc)
        for attempt in range(3):
            try:
                await svc.action("SetAVTransportURI").async_call(
                    InstanceID=0, CurrentURI=uri, CurrentURIMetaData=""
                )
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                await asyncio.sleep(1.0)
        await svc.action("Play").async_call(InstanceID=0, Speed="1")

    async def _stop_and_wait(self, svc) -> None:
        try:
            await svc.action("Stop").async_call(InstanceID=0)
        except Exception:  # noqa: BLE001  (zaten durmuş olabilir)
            return
        for _ in range(10):
            try:
                info = await svc.action("GetTransportInfo").async_call(InstanceID=0)
            except Exception:  # noqa: BLE001
                return
            if info.get("CurrentTransportState") in ("STOPPED", "NO_MEDIA_PRESENT"):
                return
            await asyncio.sleep(0.3)

    async def control(self, renderer_location: str, cmd: str) -> None:
        """cmd: "pause" | "resume" | "stop"."""
        if renderer_location == LOCAL_PLAYER_ID:
            await self.player.control(cmd)
            return
        if self.cast.has(renderer_location):
            await self.cast.control(renderer_location, cmd)
            return
        svc = self._devices[renderer_location].service(AV_TRANSPORT)
        if cmd == "pause":
            await svc.action("Pause").async_call(InstanceID=0)
        elif cmd == "resume":
            await svc.action("Play").async_call(InstanceID=0, Speed="1")
        elif cmd == "stop":
            await svc.action("Stop").async_call(InstanceID=0)

    # ---- konum / ses ----
    async def status(self, renderer_location: str, with_volume: bool = False) -> PlayStatus:
        if renderer_location == LOCAL_PLAYER_ID:
            pos, dur, state, vol = await self.player.status(with_volume)
            return PlayStatus(pos, dur, state, vol)
        if self.cast.has(renderer_location):
            pos, dur, state, vol = await self.cast.status(renderer_location, with_volume)
            return PlayStatus(pos, dur, state, vol)
        dev = self._devices[renderer_location]
        svc = dev.service(AV_TRANSPORT)
        pos = await svc.action("GetPositionInfo").async_call(InstanceID=0)
        info = await svc.action("GetTransportInfo").async_call(InstanceID=0)
        st = PlayStatus(
            position=parse_hms(pos.get("RelTime")),
            duration=parse_hms(pos.get("TrackDuration")),
            state=info.get("CurrentTransportState", "") or "",
        )
        if with_volume:
            try:
                rc = dev.service(RENDERING_CONTROL)
                r = await rc.action("GetVolume").async_call(InstanceID=0, Channel="Master")
                st.volume = int(r.get("CurrentVolume"))
            except Exception:  # noqa: BLE001  (bazı cihazlarda ses servisi yok)
                st.volume = None
        return st

    async def seek(self, renderer_location: str, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        if renderer_location == LOCAL_PLAYER_ID:
            await self.player.seek(seconds)
            return
        if self.cast.has(renderer_location):
            await self.cast.seek(renderer_location, seconds)
            return
        svc = self._devices[renderer_location].service(AV_TRANSPORT)
        await svc.action("Seek").async_call(
            InstanceID=0, Unit="REL_TIME", Target=fmt_hms(seconds)
        )

    async def set_volume(self, renderer_location: str, level: int) -> None:
        level = max(0, min(100, int(level)))
        if renderer_location == LOCAL_PLAYER_ID:
            await self.player.set_volume(level)
            return
        if self.cast.has(renderer_location):
            await self.cast.set_volume(renderer_location, level)
            return
        rc = self._devices[renderer_location].service(RENDERING_CONTROL)
        await rc.action("SetVolume").async_call(
            InstanceID=0, Channel="Master", DesiredVolume=level
        )
