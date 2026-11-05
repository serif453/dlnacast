"""Teşhis aracı: paylaşım/keşif zincirini adım adım sınar ve nerede koptuğunu gösterir."""
import asyncio
import os
import socket
import subprocess
import time
import traceback
import urllib.request
import uuid
import xml.etree.ElementTree as ET

from .config import load_config
from .localserver import guess_mime, lan_ip
from .mediaserver import MediaServer, SSDP_GROUP, SSDP_PORT

MS_TYPE = "urn:schemas-upnp-org:device:MediaServer:1"
RD_TYPE = "urn:schemas-upnp-org:device:MediaRenderer:1"


def head(t: str) -> None:
    print(f"\n== {t}")


def ok(m: str) -> None:
    print(f"  [OK]    {m}")


def bad(m: str) -> None:
    print(f"  [HATA]  {m}")


def info(m: str) -> None:
    print(f"  [..]    {m}")


def run(cmd: list) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"(çalıştırılamadı: {exc!r})"


def count_media(folder: str) -> int:
    n = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if not f.startswith(".") and guess_mime(f).split("/")[0] in ("video", "audio"):
                n += 1
    return n


def browse(ms: MediaServer, oid: str):
    args = (f"<ObjectID>{oid}</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag>"
            "<Filter>*</Filter><StartingIndex>0</StartingIndex><RequestedCount>0</RequestedCount>"
            "<SortCriteria></SortCriteria>")
    env = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
           '<s:Body><u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
           f"{args}</u:Browse></s:Body></s:Envelope>")
    req = urllib.request.Request(f"http://{ms.ip}:{ms.port}/control/cds", data=env.encode(),
                                 headers={"Content-Type": 'text/xml; charset="utf-8"'})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=5).read())
    res = next(e for e in root.iter() if e.tag.endswith("Result")).text or ""
    out = []
    for c in ET.fromstring(res):
        title = next((t.text for t in c if t.tag.endswith("title")), "?")
        out.append((c.tag.rsplit("}", 1)[-1], c.get("id"), title))
    return out


def ssdp_probe(ip: str, st: str, wait: float = 4.0):
    """M-SEARCH gönderir, gelen yanıtları (ip, server, usn, location) olarak döndürür."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
    s.bind((ip, 0))
    s.settimeout(0.5)
    msg = ("M-SEARCH * HTTP/1.1\r\n" f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
           'MAN: "ssdp:discover"\r\nMX: 2\r\n' f"ST: {st}\r\n\r\n")
    s.sendto(msg.encode(), (SSDP_GROUP, SSDP_PORT))
    found, end = [], time.time() + wait
    while time.time() < end:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        h = {}
        for ln in data.decode("utf-8", "ignore").split("\r\n")[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                h[k.strip().upper()] = v.strip()
        found.append((addr[0], h.get("SERVER", ""), h.get("USN", ""), h.get("LOCATION", "")))
    s.close()
    return found


async def upnp_check() -> None:
    try:
        from async_upnp_client.aiohttp import AiohttpRequester
        from async_upnp_client.client_factory import UpnpFactory
        from async_upnp_client.search import async_search
    except ImportError as exc:
        bad(f"async_upnp_client yüklenemedi: {exc!r}")
        return
    factory = UpnpFactory(AiohttpRequester())
    locations = {}
    for st in (MS_TYPE, RD_TYPE):
        found = []

        async def cb(headers, _f=found) -> None:
            _f.append(headers.get("location"))

        try:
            await async_search(async_callback=cb, timeout=4, search_target=st)
            ok(f"async_upnp_client araması ({st.split(':')[-2]}): {len(found)} yanıt")
            for loc in found:
                locations[loc] = st
        except Exception as exc:  # noqa: BLE001
            bad(f"async_upnp_client araması ({st.split(':')[-2]}) çöktü: {exc!r}")
            traceback.print_exc()
    for loc in list(locations)[:12]:
        try:
            dev = await factory.async_create_device(loc)
            ok(f"açıklama alındı: {dev.friendly_name}  ({loc})")
        except Exception as exc:  # noqa: BLE001
            bad(f"açıklama alınamadı: {loc} -> {exc!r}")


def main() -> int:
    cfg = load_config()
    head("1) Ayar dosyası")
    shares = cfg.get("shares", [])
    if not shares:
        bad("Hiç paylaşılan klasör yok. Uygulamada 'Klasör paylaş' ile bir klasör ekle.")
    for p in shares:
        if os.path.isdir(p):
            ok(f"{p}  (diskte {count_media(p)} video/ses dosyası, alt klasörler dahil)")
        else:
            bad(f"{p} bulunamadı veya klasör değil")

    head("2) Ağ")
    ip = lan_ip()
    info(f"Yerel IP: {ip}")
    if ip.startswith("127."):
        bad("Yerel ağ IP'si bulunamadı (127.x). Ağa bağlı mısın?")

    head("3) Paylaşım sunucusu (HTTP + ContentDirectory)")
    ms = MediaServer("tani", str(uuid.uuid4()))
    ms.set_shares(shares)
    try:
        ms.start()
        ok(f"başlatıldı: {ms.location}")
    except Exception as exc:  # noqa: BLE001
        bad(f"başlatılamadı: {exc!r}")
        return 1
    try:
        urllib.request.urlopen(ms.location, timeout=5).read()
        ok("desc.xml HTTP ile alındı")
        top = browse(ms, "0")
        ok(f"ContentDirectory kök: {[t for _k, _i, t in top]}")
        for kind, oid, title in top[:3]:
            kids = browse(ms, oid)
            folders = sum(1 for k, _i, _t in kids if k == "container")
            items = len(kids) - folders
            ok(f"'{title}' içinde {folders} klasör, {items} medya dosyası (ilk 5: "
               f"{[t for _k, _i, t in kids[:5]]})")
    except Exception as exc:  # noqa: BLE001
        bad(f"sunucuya HTTP/SOAP ile erişilemedi: {exc!r}")
        traceback.print_exc()

    head("4) SSDP (çoklu yayın) - kendi sunucumuz görülebiliyor mu?")
    try:
        mine = ssdp_probe(ip, MS_TYPE)
        own = [m for m in mine if ms.udn in m[2]]
        others = [m for m in mine if ms.udn not in m[2]]
        if own:
            ok("kendi sunucumuz M-SEARCH'e yanıt verdi (SSDP ve çoklu yayın çalışıyor)")
        else:
            bad("kendi sunucumuz yanıt vermedi: UDP 1900 / çoklu yayın engelli olabilir")
        for ipx, srv, _usn, loc in others:
            info(f"başka MediaServer: {ipx}  {srv}  {loc}")
        tv = ssdp_probe(ip, RD_TYPE)
        for ipx, srv, _usn, loc in tv:
            info(f"MediaRenderer: {ipx}  {srv}  {loc}")
        if not tv:
            bad("hiç MediaRenderer (TV) yanıt vermedi")
    except Exception as exc:  # noqa: BLE001
        bad(f"SSDP sınaması çöktü: {exc!r}")
        traceback.print_exc()

    head("5) Uygulamanın kullandığı arama (async_upnp_client)")
    try:
        asyncio.run(upnp_check())
    except Exception as exc:  # noqa: BLE001
        bad(f"çöktü: {exc!r}")
        traceback.print_exc()

    head("6) Güvenlik duvarı ve port 1900")
    for svc in ("ufw", "firewalld", "nftables", "iptables"):
        state = (run(["systemctl", "is-active", svc]).splitlines() or ["?"])[0]
        info(f"{svc}: {state}")
    for line in run(["ss", "-ulpn"]).splitlines():
        if ":1900" in line:
            info(f"UDP 1900 dinleyen: {line.strip()}")
    info("Ayrıntı için: sudo ufw status verbose   |   sudo firewall-cmd --list-all")

    ms.stop()
    print("\nBitti. Yukarıdaki çıktının tamamını bana yapıştırabilirsin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
