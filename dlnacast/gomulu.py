"""Program içi (gömülü) oynatıcı: libmpv görüntüyü pencere içindeki Gtk.GLArea'ya çizer.

Alıcı (renderer) bunu `LocalPlayer` yerine kullanır; arayüzü aynıdır (play/control/status/
seek/set_volume/set_mute/close). libmpv thread-safe olduğu için komutlar doğrudan asyncio
thread'inden verilir; sadece çizim GTK ana thread'inde yapılır.
python-mpv (pip: mpv) ya da libmpv yoksa `available()` False döner, eski mpv penceresi kullanılır.
"""
import asyncio
import ctypes
import ctypes.util
from typing import Callable, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    import mpv  # python-mpv
except (ImportError, OSError):  # paket ya da libmpv yok
    mpv = None

GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
_GetIntegerv = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))


def available() -> bool:
    return mpv is not None


def _proc_address_loader() -> Callable[[bytes], int]:
    """OpenGL fonksiyon adresini bulan işlev (önce EGL, olmazsa GLX)."""
    funcs = []
    for lib, fn in (("EGL", "eglGetProcAddress"), ("GL", "glXGetProcAddress")):
        path = ctypes.util.find_library(lib)
        if not path:
            continue
        try:
            f = getattr(ctypes.CDLL(path), fn)
        except (OSError, AttributeError):
            continue
        f.restype = ctypes.c_void_p
        f.argtypes = [ctypes.c_char_p]
        funcs.append(f)

    def get(name: bytes) -> int:
        for f in funcs:
            addr = f(name)
            if addr:
                return addr
        return 0
    return get


class EmbeddedPlayer:
    """libmpv tabanlı oynatıcı; `widget` (Gtk.GLArea) arayüze yerleştirilir."""

    def __init__(self) -> None:
        if mpv is None:
            raise RuntimeError("python-mpv kurulu değil")
        # Güvenlik: ağdan gelen adreslerde youtube-dl, betik ve güvensiz çalma listesi yok.
        self.mpv = mpv.MPV(vo="libmpv", idle=True, keep_open="no", ytdl=False,
                           osc=False, input_default_bindings=False, input_vo_keyboard=False,
                           load_scripts=False, config=False, hwdec="auto-safe",
                           audio_display="no", image_display_duration="inf")  # foto açık kalsın
        self.widget = Gtk.GLArea(hexpand=True, vexpand=True, auto_render=True)
        self.widget.connect("realize", self._on_realize)
        self.widget.connect("unrealize", self._on_unrealize)
        self.widget.connect("render", self._on_render)
        self._ctx = None
        self._gl_get = _proc_address_loader()
        self._proc_fn = mpv.MpvGlGetProcAddressFn(lambda _ctx, name: self._gl_get(name))
        self._get_integerv = None
        self.on_change: Optional[Callable[[], None]] = None  # durum değişince (ana thread'de)
        self.title = ""
        self.is_audio = False
        self._dead = False
        for prop in ("pause", "idle-active", "vid"):
            self.mpv.observe_property(prop, lambda *_: GLib.idle_add(self._changed))

    # ---- çizim (GTK ana thread'i) ----
    def _on_realize(self, area: Gtk.GLArea) -> None:
        area.make_current()
        if area.get_error() is not None:
            return
        try:
            self._ctx = mpv.MpvRenderContext(
                self.mpv, "opengl", opengl_init_params={"get_proc_address": self._proc_fn})
            self._ctx.update_cb = lambda: GLib.idle_add(self._queue_render)
            addr = self._gl_get(b"glGetIntegerv")
            self._get_integerv = _GetIntegerv(addr) if addr else None
        except Exception:  # noqa: BLE001  (GL yoksa görüntü olmaz, ses yine çalar)
            self._ctx = None

    def _on_unrealize(self, area: Gtk.GLArea) -> None:
        if self._ctx is not None:
            area.make_current()
            self._ctx.free()
            self._ctx = None

    def _queue_render(self) -> bool:
        self.widget.queue_render()
        return False

    def _on_render(self, area: Gtk.GLArea, _glctx) -> bool:
        if self._ctx is None:
            return False
        fbo = ctypes.c_int(0)
        if self._get_integerv is not None:
            self._get_integerv(GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(fbo))
        scale = area.get_scale_factor()
        w, h = area.get_width() * scale, area.get_height() * scale
        self._ctx.render(flip_y=True, opengl_fbo={"w": w, "h": h, "fbo": fbo.value})
        return True

    def _changed(self) -> bool:
        if self.on_change is not None:
            self.on_change()
        return False

    # ---- durum (her thread'den okunabilir) ----
    @property
    def alive(self) -> bool:
        return True

    @property
    def has_media(self) -> bool:
        if self._dead:  # kapanırken zamanlayıcılar hâlâ soruyor olabilir
            return False
        return self.mpv.idle_active is False

    def _get(self, name: str):
        if self._dead:
            return None
        try:
            return getattr(self.mpv, name)
        except Exception:  # noqa: BLE001  (henüz yüklenmemiş özellik)
            return None

    # ---- LocalPlayer ile aynı arayüz (asyncio thread'inden çağrılır) ----
    def prepare(self) -> bool:
        """Çizim alanını hazırla (ana thread). Görüntü yeri yoksa mpv videoyu kapatır."""
        if self._ctx is None and self.widget.get_root() is not None:
            self.widget.realize()
        return False

    async def _ensure_ctx(self) -> None:
        for _ in range(20):  # en fazla ~2 sn
            if self._ctx is not None:
                return
            GLib.idle_add(self.prepare)
            await asyncio.sleep(0.1)

    async def play(self, uri: str, mime: Optional[str] = None, title: str = "") -> None:
        from .localplayer import is_audio
        await self._ensure_ctx()
        self.title = title
        self.is_audio = is_audio(uri, mime)
        self.mpv.force_media_title = title or ""
        self.mpv.vid = "no" if self.is_audio else "auto"
        self.mpv.loadfile(uri, "replace")
        self.mpv.pause = False
        GLib.idle_add(self._changed)

    async def control(self, cmd: str) -> None:
        if cmd == "pause":
            self.mpv.pause = True
        elif cmd == "resume":
            self.mpv.pause = False
        elif cmd == "stop":
            self.mpv.command("stop")
            self.title = ""
            GLib.idle_add(self._changed)

    async def status(self, with_volume: bool = False) -> Tuple[float, float, str, Optional[int]]:
        idle, paused = self._get("idle_active"), self._get("pause")
        pos, dur = self._get("time_pos"), self._get("duration")
        vol = self._get("volume") if with_volume else None
        if idle is not False:
            state = "STOPPED"
        elif pos is None:
            state = "TRANSITIONING"
        elif paused:
            state = "PAUSED_PLAYBACK"
        else:
            state = "PLAYING"
        return (float(pos or 0.0), float(dur or 0.0), state,
                int(round(vol)) if isinstance(vol, (int, float)) else None)

    async def seek(self, seconds: float) -> None:
        self.mpv.seek(float(seconds), "absolute", "exact")  # tam konum (anahtar kareye yuvarlanmaz)

    async def set_volume(self, level: int) -> None:
        self.mpv.volume = int(level)

    async def set_mute(self, on: bool) -> None:
        self.mpv.mute = bool(on)

    # ---- arayüzdeki düğmeler için (ana thread) ----
    def toggle_pause(self) -> None:
        if self.has_media:
            self.mpv.pause = not self.mpv.pause

    def seek_relative(self, delta: float) -> None:
        if self.has_media:
            self.mpv.seek(delta, "relative")

    def close(self) -> None:
        """Oynatmayı durdurur (alıcı kapatılınca). mpv örneği program kapanana kadar kalır."""
        if self._dead:  # terminate'ten sonra mpv'ye komut göndermek programı çökertir
            return
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass
        self.title = ""

    def terminate(self) -> None:
        if self._dead:
            return
        self._dead = True
        if self._ctx is not None:
            self._ctx.free()
            self._ctx = None
        self.mpv.terminate()
