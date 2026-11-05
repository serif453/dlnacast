"""Chromecast gönderici (pychromecast). Engelleyici çağrılar thread havuzunda çalışır."""
import asyncio
import mimetypes
from typing import Dict, List, Optional, Tuple


class CastManager:
    def __init__(self) -> None:
        self._casts: Dict[str, object] = {}
        self._browser = None

    def has(self, key: str) -> bool:
        return key in self._casts

    def _disconnect_all(self) -> None:
        """Bilinen tum Chromecast baglantilarini kapatir (cihazdaki oynatma durmaz)."""
        for cc in list(self._casts.values()):
            try:
                cc.disconnect(blocking=False)
            except TypeError:
                try:
                    cc.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        self._casts = {}

    def close(self) -> None:
        if self._browser is not None:
            try:
                import pychromecast

                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        self._disconnect_all()

    # ---- keşif ----
    def _discover_blocking(self, timeout: int) -> List[Tuple[str, str]]:
        import pychromecast

        if self._browser is not None:  # önceki aramayı kapat
            try:
                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:  # noqa: BLE001
                pass
        casts, browser = pychromecast.get_chromecasts(timeout=timeout)
        self._browser = browser
        self._disconnect_all()  # onceki aramanin baglantilari sizmasin
        found = []
        for cc in casts:
            key = str(cc.uuid)
            self._casts[key] = cc
            name = getattr(cc, "name", None) or cc.cast_info.friendly_name
            found.append((name, key))
        return found

    async def discover(self, timeout: int = 5) -> List[Tuple[str, str]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._discover_blocking, timeout)

    # ---- oynatma ----
    def _play_blocking(self, key: str, url: str, mime: Optional[str], title: str) -> None:
        cc = self._casts[key]
        cc.wait(timeout=15)
        content_type = mime or mimetypes.guess_type(url)[0] or "video/mp4"
        mc = cc.media_controller
        mc.play_media(url, content_type, title=title or None)
        mc.block_until_active(timeout=15)

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
