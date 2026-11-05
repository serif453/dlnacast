"""Bu bilgisayarda oynatma: mpv'yi IPC soketiyle yönetir.

DLNA sunucusundan gelen adresleri ve yerel dosyaları doğrudan oynatır.
mpv bir kez açılır (--idle), sonraki parçalar `loadfile` ile yüklenir.
Müzik penceresiz çalar (program içinden yönetilir); video için mpv penceresi açılır.
"""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlsplit

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".alac", ".ape"}


def is_audio(uri: str, mime: Optional[str] = None) -> bool:
    """Adres/MIME türüne göre müzik mi? (bilinmiyorsa video sayılır, pencere açılır)"""
    if mime:
        return mime.lower().startswith("audio/")
    path = unquote(urlsplit(uri).path) if "://" in uri else uri
    return os.path.splitext(path)[1].lower() in AUDIO_EXT


class LocalPlayer:
    def __init__(self, tag: str = "", title: str = "DLNA Cast") -> None:
        """tag: aynı süreçte ikinci bir mpv gerekirse soket adını ayırır; title: pencere başlığı."""
        self._proc: Optional[subprocess.Popen] = None
        self._title = title
        self._sock = os.path.join(tempfile.gettempdir(), f"dlnacast-mpv{tag}-{os.getpid()}.sock")
        self._req = 0

    # ---- süreç ----
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def alive(self) -> bool:
        return self._alive()

    async def _ensure(self) -> None:
        if self._alive():
            return
        exe = shutil.which("mpv")
        if not exe:
            raise RuntimeError("mpv kurulu değil (sudo pacman -S mpv)")
        try:
            os.unlink(self._sock)
        except FileNotFoundError:
            pass
        self._proc = subprocess.Popen(
            [exe, "--idle=yes", "--force-window=no", "--keep-open=no", "--ytdl=no",
             f"--title={self._title}", f"--input-ipc-server={self._sock}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):  # soket hazır olana kadar bekle (en fazla ~6 sn)
            if os.path.exists(self._sock):
                return
            if self._proc.poll() is not None:
                break
            await asyncio.sleep(0.1)
        raise RuntimeError("mpv başlatılamadı")

    def close(self) -> None:
        if self._alive():
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        try:
            os.unlink(self._sock)
        except OSError:
            pass

    # ---- IPC ----
    async def _call(self, cmds: List[list], timeout: float = 3.0) -> List[Optional[dict]]:
        """Tek bağlantıda birden fazla komut gönderir; her biri için yanıt sözlüğü döndürür."""
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self._sock), timeout)
        try:
            ids = {}
            for c in cmds:
                self._req += 1
                ids[self._req] = len(ids)
                writer.write(json.dumps({"command": c, "request_id": self._req}).encode() + b"\n")
            await writer.drain()
            out: List[Optional[dict]] = [None] * len(cmds)
            pending = len(cmds)
            while pending:
                line = await asyncio.wait_for(reader.readline(), timeout)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                i = ids.get(msg.get("request_id"))
                if i is not None and out[i] is None:
                    out[i] = msg
                    pending -= 1
            return out
        finally:
            writer.close()

    async def _do(self, *cmd) -> None:
        res = (await self._call([list(cmd)]))[0]
        if not res or res.get("error") != "success":
            raise RuntimeError(f"mpv komutu başarısız: {cmd[0]} ({(res or {}).get('error')})")

    # ---- oynatma ----
    async def play(self, uri: str, mime: Optional[str] = None, title: str = "") -> None:
        await self._ensure()
        audio = is_audio(uri, mime)
        # Müzik: pencere açılmaz, görüntü (kapak resmi) kapalı. Video: mpv penceresi.
        await self._do("set_property", "force-window", "no" if audio else "yes")
        await self._do("set_property", "vid", "no" if audio else "auto")
        await self._do("set_property", "force-media-title", title or "")
        await self._do("loadfile", uri, "replace")
        await self._do("set_property", "pause", False)

    async def control(self, cmd: str) -> None:
        if not self._alive():
            return
        if cmd == "pause":
            await self._do("set_property", "pause", True)
        elif cmd == "resume":
            await self._do("set_property", "pause", False)
        elif cmd == "stop":
            await self._do("stop")

    async def status(self, with_volume: bool = False) -> Tuple[float, float, str, Optional[int]]:
        """(konum_sn, süre_sn, durum, ses 0-100 | None)."""
        if not self._alive():
            return 0.0, 0.0, "STOPPED", None
        names = ["idle-active", "pause", "time-pos", "duration"] + (["volume"] if with_volume else [])
        try:
            res = await self._call([["get_property", n] for n in names])
        except (OSError, asyncio.TimeoutError):
            return 0.0, 0.0, "STOPPED", None

        def val(i: int):
            r = res[i]
            return r.get("data") if r and r.get("error") == "success" else None

        idle, paused, pos, dur = val(0), val(1), val(2), val(3)
        vol = val(4) if with_volume else None
        if idle is not False:  # True veya bilinmiyor: çalan bir şey yok
            state = "STOPPED"
        elif pos is None:
            state = "TRANSITIONING"  # dosya yükleniyor
        elif paused:
            state = "PAUSED_PLAYBACK"
        else:
            state = "PLAYING"
        return (float(pos or 0.0), float(dur or 0.0), state,
                int(round(vol)) if isinstance(vol, (int, float)) else None)

    async def seek(self, seconds: float) -> None:
        await self._do("seek", float(seconds), "absolute")

    async def set_volume(self, level: int) -> None:
        await self._do("set_property", "volume", int(level))

    async def set_mute(self, on: bool) -> None:
        await self._do("set_property", "mute", bool(on))
