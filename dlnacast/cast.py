"""Chromecast gönderici (pychromecast). Engelleyici çağrılar thread havuzunda çalışır.

Arama (mDNS) program açık kaldığı sürece arka planda sürer; bulunan cihaza hemen
bağlanılır ve bağlantı yenilemede koparılmaz. Bilinen IP adresleri (`known_hosts`)
mDNS duyurusu kaçsa bile cihaza doğrudan ulaşmayı sağlar.
"""
import asyncio
import mimetypes
import threading
import time
from typing import Dict, List, Optional, Tuple

CONNECT_TIMEOUT = 10.0  # tek bağlantı denemesinde bekleme (sn)
FIRST_SCAN = 5.0  # ilk aramada cihazların duyurusunu bekleme (sn)
RESCAN = 3.0  # sonraki yenilemelerde bekleme (sn)


class CastManager:
    def __init__(self) -> None:
        self._casts: Dict[str, object] = {}
        self._browser = None
        self._zc = None
        self._lock = threading.Lock()

    def has(self, key: str) -> bool:
        return key in self._casts

    def known_hosts(self) -> List[str]:
        """Bulunan Chromecast'lerin IP adresleri (bir sonraki açılışta doğrudan denenir)."""
        out = []
        for cc in self._casts.values():
            host = getattr(getattr(cc, "cast_info", None), "host", None)
            if host and host not in out:
                out.append(host)
        return out

    @staticmethod
    def _disconnect(cc) -> None:
        """Bağlantıyı kapatır (cihazdaki oynatma durmaz)."""
        try:
            cc.disconnect(blocking=False)
        except TypeError:
            try:
                cc.disconnect()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        for cc in list(self._casts.values()):
            self._disconnect(cc)
        self._casts = {}
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:  # noqa: BLE001
                pass
            self._zc = None

    # ---- keşif ----
    def _connect(self, info):
        """CastInfo'dan Chromecast nesnesi oluşturur ve bağlantıyı arka planda başlatır."""
        import pychromecast

        cc = pychromecast.get_chromecast_from_cast_info(
            info, self._zc, tries=None, retry_wait=3, timeout=CONNECT_TIMEOUT)
        cc.start()  # bağlantı şimdiden kurulsun; oynat'a basınca beklenmesin
        return cc

    def _discover_blocking(self, known_hosts: List[str]) -> List[Tuple[str, str]]:
        import zeroconf
        from pychromecast.discovery import CastBrowser, SimpleCastListener

        first = self._browser is None
        if first:  # arama bir kez başlatılır, program kapanana kadar sürer
            self._zc = zeroconf.Zeroconf()
            self._browser = CastBrowser(SimpleCastListener(), self._zc, known_hosts or None)
            self._browser.start_discovery()
        elif known_hosts:
            self._browser.host_browser.update_hosts(known_hosts)
        time.sleep(FIRST_SCAN if first else RESCAN)
        found = []
        with self._lock:
            for uuid, info in list(self._browser.devices.items()):
                key = str(uuid)
                if key not in self._casts:
                    try:
                        self._casts[key] = self._connect(info)
                    except Exception:  # noqa: BLE001  (bu cihaz atlanır, diğerleri sürer)
                        continue
                found.append((info.friendly_name or key, key))
        return found

    async def discover(self, known_hosts: Optional[List[str]] = None) -> List[Tuple[str, str]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._discover_blocking, list(known_hosts or []))

    # ---- oynatma ----
    def _reconnect(self, key: str):
        """Bozulan bağlantıyı kapatıp aynı cihaza yeniden bağlanır."""
        with self._lock:
            old = self._casts[key]
            info = old.cast_info
            if self._browser is not None:  # cihazın adresi değiştiyse güncelini kullan
                info = self._browser.devices.get(old.uuid, info)
            self._disconnect(old)
            cc = self._connect(info)
            self._casts[key] = cc
            return cc

    def _play_blocking(self, key: str, url: str, mime: Optional[str], title: str) -> None:
        from pychromecast.error import PyChromecastError

        content_type = mime or mimetypes.guess_type(url)[0] or "video/mp4"
        cc = self._casts[key]
        for attempt in range(2):
            try:
                cc.wait(timeout=CONNECT_TIMEOUT)
                mc = cc.media_controller
                mc.play_media(url, content_type, title=title or None)
                mc.block_until_active(timeout=15)
                return
            except (PyChromecastError, OSError) as exc:
                if attempt == 1:
                    raise RuntimeError("Chromecast'e bağlanılamadı. TV açık ve aynı ağda mı? "
                                       "Birkaç saniye sonra tekrar dene.") from exc
                cc = self._reconnect(key)  # bir kez yeniden bağlanıp tekrar dene

    async def play(self, key: str, url: str, mime: Optional[str] = None, title: str = "") -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._play_blocking, key, url, mime, title)

    def _control_blocking(self, key: str, cmd: str) -> None:
        mc = self._casts[key].media_controller
        {"pause": mc.pause, "resume": mc.play, "stop": mc.stop}[cmd]()

    async def control(self, key: str, cmd: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._control_blocking, key, cmd)

    # ---- durum / konum / ses ----
    def _status_blocking(self, key: str, with_volume: bool):
        cc = self._casts[key]
        st = cc.media_controller.status
        pos = getattr(st, "adjusted_current_time", None) or st.current_time or 0.0
        dur = st.duration or 0.0
        state = {"PLAYING": "PLAYING", "PAUSED": "PAUSED_PLAYBACK",
                 "BUFFERING": "TRANSITIONING"}.get(st.player_state, "STOPPED")
        vol = None
        if with_volume and cc.status is not None and cc.status.volume_level is not None:
            vol = int(round(cc.status.volume_level * 100))
        return float(pos), float(dur), state, vol

    async def status(self, key: str, with_volume: bool = False):
        """(konum_sn, süre_sn, durum, ses 0-100 | None) döndürür."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._status_blocking, key, with_volume)

    async def seek(self, key: str, seconds: float) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._casts[key].media_controller.seek, seconds)

    async def set_volume(self, key: str, level: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._casts[key].set_volume, level / 100.0)
