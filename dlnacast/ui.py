"""GTK4 arayüzü: dört sekme (Yayınla, Alıcı, Ağda Gez, Sunucu)."""
import os
import re
import socket
import threading
import asyncio
import concurrent.futures
import time
import traceback
from urllib.parse import unquote, urlsplit
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from . import guncelleme  # noqa: E402
from . import gomulu  # noqa: E402
from . import kucukresim  # noqa: E402
from .bridge import AsyncBridge, to_ui  # noqa: E402
from .controlpoint import LOCAL_ID, LOCAL_PLAYER_ID, ControlPoint, DeviceInfo, Entry, PlayStatus  # noqa: E402
from .config import load_config, save_config  # noqa: E402
from .localserver import LocalServer, guess_mime  # noqa: E402
from .mediaserver import MediaServer  # noqa: E402
from .renderer import MediaRenderer  # noqa: E402

SKIP_SECONDS = 10
POLL_SECONDS = 1
VOLUME_EVERY = 10  # ses değerini kaç yoklamada bir oku
SETTLE_SECONDS = 3.0  # yeni parça başlatıldıktan sonra eski durum bilgisini yok say
END_TOLERANCE = 6.0  # parça bitmeden bu kadar saniye önce durduysa "bitti" sayılmaz

# Tema renklerinden türetilir; açık ve koyu temada aynı şekilde çalışır.
CSS = b"""
.kenar { background-color: alpha(currentColor, 0.04); }
.kenar list, .icerik list { background: transparent; }
.kenar row { border-radius: 8px; margin: 1px 8px; }
.icerik row { border-radius: 8px; margin: 1px 12px; }
.bolum-baslik { font-size: 0.8em; font-weight: bold; opacity: 0.55; margin: 14px 18px 4px 18px; }
.soluk { opacity: 0.6; }
.kucuk { font-size: 0.9em; }
.buyuk-baslik { font-size: 1.25em; font-weight: bold; }
.karsilama-baslik { font-size: 1.6em; font-weight: bold; }
.oynatici { border-top: 1px solid alpha(currentColor, 0.12); background-color: alpha(currentColor, 0.03); }
.calan-baslik { font-weight: bold; font-size: 1.05em; }
.yuvarlak { border-radius: 999px; min-width: 34px; min-height: 34px; padding: 0; }
.oynat-dugmesi { border-radius: 999px; min-width: 48px; min-height: 48px; padding: 0; }
.alici-isareti { font-size: 0.85em; font-weight: bold; color: #2e9d4a; margin: 0 6px; }
.eylem { padding: 6px 10px; }
.hedef { padding: 2px 12px; border-radius: 999px; }
.hedef-ad { font-weight: bold; }
.calan-satir label.satir-baslik { font-weight: bold; }
.calan-satir { background-color: alpha(@theme_selected_bg_color, 0.18); }
.bildirim { background-color: rgba(32, 32, 36, 0.94); color: white; border-radius: 12px;
            padding: 10px 10px 10px 16px; margin: 16px; }
.bildirim.hata { background-color: rgba(176, 40, 40, 0.96); }
.bildirim button { color: white; }
.kare { border-radius: 10px; background-color: alpha(currentColor, 0.07); }
.kare picture { border-radius: 10px; }
.karo { border-radius: 12px; padding: 6px; }
.calan-karo .kare { box-shadow: 0 0 0 3px @theme_selected_bg_color; }
.rozet { background-color: rgba(0, 0, 0, 0.6); color: white; border-radius: 999px; padding: 4px; margin: 6px; }
.sekmeler button { border-radius: 0; background: none; box-shadow: none; border: none;
                   border-bottom: 3px solid transparent; padding: 4px 16px; font-weight: bold; }
.sekmeler button:checked { border-bottom-color: @theme_selected_bg_color; color: @theme_selected_bg_color; }
.sekmeler button:hover:not(:checked) { background-color: alpha(currentColor, 0.06); }
.kart { border: 1px solid alpha(currentColor, 0.13); border-radius: 12px; padding: 14px 16px;
        background-color: alpha(currentColor, 0.02); }
.kart-baslik { font-weight: bold; font-size: 1.05em; }
.ic-kutu { background-color: alpha(currentColor, 0.05); border-radius: 8px; padding: 10px 12px; }
.cihazlar { background: transparent; }
.cihazlar row { border-radius: 8px; }
.cihazlar row:not(:last-child) { border-bottom: 1px solid alpha(currentColor, 0.08); }
.secili-cihaz { background-color: alpha(@theme_selected_bg_color, 0.15); }
.rozet-yazi { border-radius: 6px; padding: 1px 8px; font-size: 0.8em; font-weight: bold; }
.rozet-cast { background-color: alpha(#2e9d4a, 0.16); color: #2e9d4a; }
.rozet-dlna { background-color: alpha(#3b6fd8, 0.16); color: #3b6fd8; }
.rozet-pc { background-color: alpha(currentColor, 0.1); }
.acik-yazi { color: #2e9d4a; }
.video-alani { background-color: black; border-radius: 10px; }
.video-alani.tam-ekran { border-radius: 0; }
.video-bos { color: alpha(white, 0.7); }
.video-cubugu { background-color: rgba(0, 0, 0, 0.62); color: white; padding: 6px 10px; }
.video-cubugu button { color: white; }
.video-cubugu label { color: white; }
.ses-dugmesi { font-size: 1.2em; font-weight: bold; }
.adim { font-weight: bold; min-width: 26px; min-height: 26px; border-radius: 999px;
        background-color: alpha(currentColor, 0.1); }
"""


def fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def natural_key(text: str) -> list:
    """"bolum2" < "bolum10" olacak şekilde sıralama anahtarı."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


@dataclass
class QueueItem:
    title: str
    uri: Optional[str] = None  # DLNA sunucusundan gelen adres
    mime: Optional[str] = None
    path: Optional[str] = None  # yerel dosya (oynatılırken ağa açılır)


def title_from_url(url: str) -> str:
    """Adresin son parçasından okunur bir ad çıkarır."""
    name = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return name or urlsplit(url).hostname or url


def is_playable(e: Entry) -> bool:
    if e.is_container or not e.uri:
        return False
    if (e.mime or "").startswith("image/") or "imageItem" in e.upnp_class:
        return False
    return True


def local_media_files(folder: str) -> List[str]:
    """Klasördeki video/ses dosyaları (alt klasörler hariç), doğal sırayla."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    files = []
    for n in names:
        p = os.path.join(folder, n)
        if n.startswith(".") or not os.path.isfile(p):
            continue
        if guess_mime(p).split("/")[0] in ("video", "audio"):
            files.append(p)
    files.sort(key=lambda p: natural_key(os.path.basename(p)))
    return files


def local_entries(folder: str) -> List[Entry]:
    """Klasörün içeriği: önce alt klasörler, sonra video/ses dosyaları (doğal sırada)."""
    try:
        names = os.listdir(folder)
    except OSError as exc:
        raise RuntimeError(f"Klasör okunamadı: {exc}") from exc
    dirs, files = [], []
    for n in names:
        if n.startswith("."):
            continue
        p = os.path.join(folder, n)
        if os.path.isdir(p):
            dirs.append(p)
        elif os.path.isfile(p) and guess_mime(p).split("/")[0] in ("video", "audio"):
            files.append(p)

    def key(p: str) -> list:
        return natural_key(os.path.basename(p))

    out = [Entry(id=p, title=os.path.basename(p), is_container=True) for p in sorted(dirs, key=key)]
    out += [Entry(id=p, title=os.path.basename(p), is_container=False,
                  uri="file://" + p, mime=guess_mime(p)) for p in sorted(files, key=key)]
    return out


class Row(Gtk.ListBoxRow):
    def __init__(self, text: str, data, icon: str = "", subtitle: str = "",
                 trailing: Optional[Gtk.Widget] = None) -> None:
        super().__init__()
        self.data = data
        self.base_icon = icon
        box = Gtk.Box(spacing=10, margin_top=7, margin_bottom=7, margin_start=10, margin_end=6)
        self.icon = Gtk.Image(icon_name=icon or "text-x-generic-symbolic", pixel_size=20, visible=bool(icon))
        box.append(self.icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        texts.append(Gtk.Label(label=text, xalign=0, ellipsize=3, css_classes=["satir-baslik"]))
        if subtitle:
            texts.append(Gtk.Label(label=subtitle, xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"]))
        box.append(texts)
        if trailing is not None:
            trailing.set_valign(Gtk.Align.CENTER)
            box.append(trailing)
        self.set_child(box)

    def set_playing(self, on: bool) -> None:
        """Listede şu an çalan öğeyi vurgular."""
        if on:
            self.add_css_class("calan-satir")
            self.icon.set_from_icon_name("audio-volume-high-symbolic")
        else:
            self.remove_css_class("calan-satir")
            self.icon.set_from_icon_name(self.base_icon)


class Tile(Gtk.FlowBoxChild):
    """Izgara görünümündeki kare: küçük resim (ya da simge) ve altında ad."""

    W, H = 168, 112

    def __init__(self, ent: Entry, icon: str) -> None:
        super().__init__(css_classes=["karo"])
        self.data = ent
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Gtk.Overlay(css_classes=["kare"], overflow=Gtk.Overflow.HIDDEN)
        frame.set_size_request(self.W, self.H)
        self.icon = Gtk.Image(icon_name=icon, pixel_size=44, css_classes=["soluk"],
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        frame.set_child(self.icon)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        frame.add_overlay(self.pic)
        self.badge = Gtk.Image(icon_name="audio-volume-high-symbolic", visible=False,
                               css_classes=["rozet"], halign=Gtk.Align.END, valign=Gtk.Align.START)
        frame.add_overlay(self.badge)
        if not ent.is_container and is_playable(ent):
            frame.add_overlay(Gtk.Image(icon_name="media-playback-start-symbolic", css_classes=["rozet"],
                                        halign=Gtk.Align.START, valign=Gtk.Align.END))
        box.append(frame)
        label = Gtk.Label(label=ent.title, wrap=True, wrap_mode=2, lines=2, ellipsize=3,
                          justify=Gtk.Justification.CENTER, max_width_chars=18,
                          width_chars=18, css_classes=["kucuk"])
        box.append(label)
        self.set_child(box)
        self.set_tooltip_text(ent.title)

    def set_thumb(self, texture: Gdk.Texture) -> None:
        self.pic.set_paintable(texture)
        self.pic.set_visible(True)
        self.icon.set_visible(False)

    def set_playing(self, on: bool) -> None:
        self.badge.set_visible(on)
        if on:
            self.add_css_class("calan-karo")
        else:
            self.remove_css_class("calan-karo")


DEVICE_LOOK = {  # tür -> (simge, açıklama)
    "player": ("computer-symbolic", "Müzik program içinde çalar, video ayrı pencerede açılır"),
    "cast": ("video-display-symbolic", "Chromecast"),
    "renderer": ("video-display-symbolic", "Televizyon / DLNA cihazı"),
}


class DevicePicker(Gtk.MenuButton):
    """Başlıktaki "Şurada oynat" seçicisi: cihazlar simge ve açıklamayla listelenir."""

    def __init__(self, on_changed) -> None:
        super().__init__(tooltip_text="Videonun oynatılacağı cihazı seç", css_classes=["hedef"])
        self._items: List[DeviceInfo] = []
        self._sel = -1
        self._on_changed = on_changed
        self.on_rebuilt = None  # liste/seçim değişince çağrılır (Yayınla sekmesi için)
        box = Gtk.Box(spacing=8)
        self._icon = Gtk.Image(icon_name="video-display-symbolic")
        box.append(self._icon)
        box.append(Gtk.Label(label="Şurada oynat:", css_classes=["soluk"]))
        self._name = Gtk.Label(label="…", ellipsize=3, max_width_chars=28, css_classes=["hedef-ad"])
        box.append(self._name)
        self.set_child(box)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.connect("row-activated", lambda _l, r: self._pick(r.get_index()))
        pop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8,
                      margin_bottom=8, margin_start=6, margin_end=6)
        pop.append(Gtk.Label(label="Nerede oynatılsın?", xalign=0, margin_start=8,
                             css_classes=["calan-baslik"]))
        pop.append(self._list)
        pop.append(Gtk.Label(label="Cihazın listede yoksa açık ve aynı ağda olduğundan emin ol,\n"
                                   "sonra yenile düğmesine bas.", xalign=0, margin_start=8,
                             css_classes=["soluk", "kucuk"]))
        self.set_popover(Gtk.Popover(child=pop))

    def clear(self) -> None:
        self._items = []
        self._sel = -1
        self._rebuild()

    def append(self, info: DeviceInfo) -> None:
        self._items.append(info)
        if self._sel < 0:
            self._sel = 0
            self._rebuild()
            self._on_changed()
        else:
            self._rebuild()

    def get_selected(self) -> int:
        return self._sel

    def set_selected(self, idx: int) -> None:
        if 0 <= idx < len(self._items) and idx != self._sel:
            self._sel = idx
            self._rebuild()
            self._on_changed()

    def _pick(self, idx: int) -> None:
        self.popdown()
        self.set_selected(idx)

    def _rebuild(self) -> None:
        clear(self._list)
        for i, d in enumerate(self._items):
            icon, desc = DEVICE_LOOK.get(d.kind, DEVICE_LOOK["renderer"])
            mark = Gtk.Image(icon_name="object-select-symbolic", visible=(i == self._sel))
            self._list.append(Row(d.name, d, icon=icon, subtitle=desc, trailing=mark))
        if 0 <= self._sel < len(self._items):
            d = self._items[self._sel]
            self._icon.set_from_icon_name(DEVICE_LOOK.get(d.kind, DEVICE_LOOK["renderer"])[0])
            self._name.set_text(d.name)
        else:
            self._name.set_text("…")
        if self.on_rebuilt is not None:
            self.on_rebuilt()


def friendly_error(exc: BaseException) -> str:
    """Hata nesnesini kullanıcının anlayacağı bir cümleye çevirir."""
    name = type(exc).__name__
    text = str(exc).strip()
    if isinstance(exc, KeyError):
        return "Bu cihaz artık listede değil. Yenile düğmesine basıp cihazı tekrar seç."
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in name:
        return "Cihaz yanıt vermedi. Açık ve aynı ağda olduğundan emin olup tekrar dene."
    if isinstance(exc, (ConnectionError, OSError)) or "Connect" in name or "Communication" in name:
        return "Cihaza ulaşılamadı. Açık ve aynı ağda olduğundan emin ol, sonra yenile düğmesine bas."
    if "Failed to execute" in text or name in ("RequestFailed", "ControllerNotRegistered"):
        return ("Cihaz bu komutu şu an kabul etmedi. Çalan bir şey olmayabilir; "
                "listeden bir video seçip tekrar dene.")
    if "Upnp" in name:
        return f"Cihaz bu isteği kabul etmedi ({text or name}). Dosya türünü desteklemiyor olabilir."
    if isinstance(exc, RuntimeError) and text:
        return text  # programın kendi (Türkçe) mesajları
    return f"Bir şeyler ters gitti: {text or name}"


def entry_icon(e: Entry) -> str:
    """Liste satırı için simge adı."""
    if e.is_container:
        return "folder-symbolic"
    kind = (e.mime or "").split("/")[0]
    if kind == "audio" or "audioItem" in e.upnp_class:
        return "audio-x-generic-symbolic"
    if kind == "image" or "imageItem" in e.upnp_class:
        return "image-x-generic-symbolic"
    return "video-x-generic-symbolic"


def placeholder(text: str) -> Gtk.Label:
    return Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER,
                     margin_top=12, margin_bottom=12, margin_start=18, margin_end=18,
                     css_classes=["soluk", "kucuk"])


def section_title(text: str) -> Gtk.Label:
    return Gtk.Label(label=text.upper(), xalign=0, css_classes=["bolum-baslik"])


def action_button(icon: str, text: str, tip: str, fn) -> Gtk.Button:
    """Simgeli ve yazılı düğme."""
    box = Gtk.Box(spacing=8)
    box.append(Gtk.Image(icon_name=icon))
    box.append(Gtk.Label(label=text, xalign=0))
    btn = Gtk.Button(child=box, tooltip_text=tip, css_classes=["eylem"])
    btn.connect("clicked", lambda _b: fn())
    return btn


def clear(listbox: Gtk.ListBox) -> None:
    while (child := listbox.get_first_child()) is not None:
        listbox.remove(child)


DEVICE_BADGE = {  # tür -> (rozet yazısı, css sınıfı)
    "player": ("Bu bilgisayar", "rozet-pc"),
    "cast": ("Chromecast", "rozet-cast"),
    "renderer": ("DLNA", "rozet-dlna"),
}


def card(title: str = "", spacing: int = 10) -> Tuple[Gtk.Box, Gtk.Box]:
    """Başlıklı çerçeve (kart). (kart, içerik kutusu) döndürür."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, css_classes=["kart"])
    if title:
        outer.append(Gtk.Label(label=title, xalign=0, css_classes=["kart-baslik"]))
    return outer, outer


def switch_row(title: str, sub: str, switch: Gtk.Switch, icon: str = "") -> Gtk.Box:
    """Simge + başlık/açıklama + anahtar satırı."""
    row = Gtk.Box(spacing=12)
    if icon:
        row.append(Gtk.Image(icon_name=icon, pixel_size=20, valign=Gtk.Align.CENTER))
    txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    txt.append(Gtk.Label(label=title, xalign=0, css_classes=["calan-baslik"]))
    if sub:
        txt.append(Gtk.Label(label=sub, xalign=0, wrap=True, css_classes=["soluk", "kucuk"]))
    row.append(txt)
    switch.set_valign(Gtk.Align.CENTER)
    row.append(switch)
    return row


def page_scroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    return Gtk.ScrolledWindow(child=child, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)


class SharesPanel(Gtk.Box):
    """Paylaşılan klasörleri gösterir; ekleme/kaldırma yapar (Sunucu sekmesinde)."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.parent_win = parent
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.listbox.set_placeholder(placeholder("Henüz paylaşılan klasör yok. "
                                                 "\"Klasör ekle\" ile bir klasör seç."))
        self.append(self.listbox)
        add = action_button("list-add-symbolic", "Klasör ekle…", "Paylaşılacak klasörü seç",
                            self.add_folder)
        add.add_css_class("suggested-action")
        add.set_halign(Gtk.Align.START)
        self.append(add)
        self.fill()

    def fill(self) -> None:
        clear(self.listbox)
        for path in self.parent_win.config["shares"]:
            row = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            row.append(Gtk.Image(icon_name="folder-publicshare-symbolic"))
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            names.append(Gtk.Label(label=os.path.basename(path.rstrip(os.sep)) or path, xalign=0,
                                   ellipsize=3, css_classes=["calan-baslik"]))
            names.append(Gtk.Label(label=path, xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"]))
            row.append(names)
            btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Paylaşımdan kaldır (klasör silinmez)",
                             css_classes=["flat"], valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda _b, p=path: self.remove(p))
            row.append(btn)
            self.listbox.append(row)

    def add_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Paylaşılacak klasörü seç")
        dialog.select_folder(self.parent_win, None, self.on_folder)

    def on_folder(self, dialog, result) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path()
        shares = self.parent_win.config["shares"]
        if path and path not in shares:
            shares.append(path)
            self.parent_win.shares_changed()
            self.fill()

    def remove(self, path: str) -> None:
        self.parent_win.config["shares"].remove(path)
        self.parent_win.shares_changed()
        self.fill()


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title=f"DLNA Cast {__version__}")
        self.set_default_size(1120, 740)
        self.bridge = AsyncBridge()
        self.cp = ControlPoint()
        self.local = LocalServer()

        self.servers: List[DeviceInfo] = []
        self.renderers: List[DeviceInfo] = []
        self.current_server: Optional[DeviceInfo] = None
        self.stack: List[Tuple[str, str]] = []  # (object_id, başlık)
        self.entries: List[Entry] = []  # görüntülenen klasörün içeriği
        self.entry_rows: List[Row] = []
        self.view_local = False  # sağ liste diskten mi geliyor (yoksa DLNA sunucusundan mı)
        self._local_rows: Dict[str, Row] = {}
        self._want_renderer: Optional[str] = None  # yenilemeden once secili hedef
        self._sel_guard = False  # True iken liste/secim degisimi kullanici secimi sayilmaz
        self._discovering = False  # arama surerken durum yoklamasi durur

        # Kuyruk
        self.queue: List[QueueItem] = []
        self.queue_idx = -1
        self.queue_rows: List[Optional[Row]] = []  # kuyruk öğesine karşılık gelen liste satırı
        self._pending = False  # Yayınla sekmesinde medya seçildi ama henüz başlatılmadı

        # Oynatma durumu
        self._polling = False
        self._poll_n = 0
        self._pos = 0.0
        self._dur = 0.0
        self._vol = 50  # son bilinen ses düzeyi
        self._unmute_vol = 0  # "Sessiz"den önceki ses düzeyi (0 = sessiz değil)
        self._seek_src = 0
        self._vol_src = 0
        self._seek_touch = 0.0  # kullanıcının son kaydırma zamanı
        self._vol_touch = 0.0
        self._was_playing = False  # bu parça şu ana kadar çalıştı/duraklatıldı mı
        self._user_stopped = False  # kullanıcı Durdur'a bastı mı
        self._last_pos = 0.0
        self._last_dur = 0.0
        self._play_t0 = 0.0

        self._state = ""  # seçili cihazın son bilinen oynatma durumu
        self._playing_row: Optional[Row] = None
        self.config = load_config()
        self._thumbs = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self._thumb_gen = 0
        self._art_gen = 0

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Aynı bilgi birden çok sekmede gösterilir (Yayınla kartı + Ağda Gez alt paneli).
        self.seek_scales: List[Gtk.Scale] = []
        self.pos_labels: List[Gtk.Label] = []
        self.dur_labels: List[Gtk.Label] = []
        self.status_labels: List[Gtk.Label] = []

        self.autonext = Gtk.Switch(active=self.config.get("autonext", True),
                                   tooltip_text="Bir video bitince listedeki sıradaki video başlar")
        self.loop_switch = Gtk.Switch(active=self.config.get("dongu", False),
                                      tooltip_text="Liste (ya da tek video) bitince baştan başlar")
        self.renderer_switch = Gtk.Switch(
            tooltip_text="Açıkken telefon ve bilgisayarlar bu bilgisayarı TV gibi görür; "
                         "gönderdikleri video/müzik burada oynar (DLNA renderer)")
        self.server_switch = Gtk.Switch(active=self.config.get("sunucu", True),
                                        tooltip_text="Açıkken paylaşılan klasörler ağdaki TV ve "
                                                     "telefonlarda görünür (DLNA server)")

        self.mserver = MediaServer(f"DLNA Cast ({socket.gethostname()})", self.config["uuid"])
        self.cp.local_media = self.mserver
        self.config.setdefault("alici_gomulu", True)
        self.rplayer: Optional["gomulu.EmbeddedPlayer"] = None  # alıcının program içi oynatıcısı
        if gomulu.available():
            try:
                self.rplayer = gomulu.EmbeddedPlayer()
            except Exception as exc:  # noqa: BLE001  (libmpv açılamazsa eski mpv penceresi)
                print(f"Gömülü oynatıcı açılamadı: {exc!r}")
        self._ext_player = None  # ayrı pencereli mpv (gerekince oluşturulur)
        self.renderer = MediaRenderer(f"DLNA Cast Alıcı ({socket.gethostname()})",
                                      self.config["renderer_uuid"], self.bridge.loop,
                                      player=self._pick_renderer_player())
        self.renderer.on_message = lambda text: to_ui(self.on_renderer_message, text)

        self._build_header()
        self.tabs = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.tabs.add_titled(self._build_cast_tab(), "yayinla", "Yayınla")
        self.tabs.add_titled(self._build_receiver_tab(), "alici", "Alıcı")
        self.tabs.add_titled(self._build_browse_tab(), "gez", "Ağda Gez")
        self.tabs.add_titled(self._build_server_tab(), "sunucu", "Sunucu")
        self.tab_switcher.set_stack(self.tabs)

        self.toast_label = Gtk.Label(wrap=True, xalign=0, max_width_chars=60)
        self.toast_icon = Gtk.Image(icon_name="dialog-information-symbolic")
        toast_close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "yuvarlak"],
                                 tooltip_text="Kapat")
        toast_close.connect("clicked", lambda *_: self.hide_toast())
        self.toast_box = Gtk.Box(spacing=10, css_classes=["bildirim"])
        self.toast_box.append(self.toast_icon)
        self.toast_box.append(self.toast_label)
        self.toast_box.append(toast_close)
        self.toast = Gtk.Revealer(child=self.toast_box, reveal_child=False,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                  halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        self._toast_src = 0
        overlay = Gtk.Overlay(child=self.tabs)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

        # Boşluk vb. her yerde çalışsın (CAPTURE); ok tuşları ise önce odaktaki
        # ses/ilerleme çubuğuna gitsin, onlar kullanmazsa sarma yapsın (BUBBLE).
        for phase, pairs in (
                (Gtk.PropagationPhase.CAPTURE, (("space", self.on_space), ("BackSpace", self.go_back),
                                                ("<Control>o", self.open_file), ("F5", self.refresh),
                                                ("F11", self.on_fullscreen_key), ("f", self.on_fullscreen_key),
                                                ("Escape", self.on_escape))),
                (Gtk.PropagationPhase.BUBBLE, (("Left", lambda: self.on_arrow(-SKIP_SECONDS)),
                                               ("Right", lambda: self.on_arrow(SKIP_SECONDS))))):
            keys = Gtk.ShortcutController(propagation_phase=phase)
            for trigger, fn in pairs:
                keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                               action=Gtk.CallbackAction.new(self._key, fn)))
            self.add_controller(keys)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

        self.autonext.connect("notify::active", self.on_autonext)
        self.loop_switch.connect("notify::active", self.on_loop)
        self.renderer_switch.connect("notify::active", self.on_renderer_switch)
        self.server_switch.connect("notify::active", self.on_server_switch)
        self.connect("close-request", self.on_close)
        self.shares_changed(save=False)
        if self.config["renderer"]:  # son seferde açıksa yine aç
            self.renderer_switch.set_active(True)
        self._update_receiver_info()
        self._update_cast_media()
        tab = self.config.get("sekme", "yayinla")
        if self.tabs.get_child_by_name(tab) is not None:
            self.tabs.set_visible_child_name(tab)
        self.tabs.connect("notify::visible-child-name", self.on_tab_changed)
        if self.rplayer is not None:  # görüntü alanı baştan hazır olsun (bkz. EmbeddedPlayer.prepare)
            self.connect("map", lambda *_: GLib.idle_add(self.rplayer.prepare))
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self.poll)
        GLib.timeout_add_seconds(2, self._update_receiver_info)
        threading.Thread(target=self._check_update, daemon=True).start()

    # ---------------- başlık çubuğu ----------------
    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        self.tab_switcher = Gtk.StackSwitcher(css_classes=["sekmeler"])
        header.set_title_widget(self.tab_switcher)

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        menu.append(Gtk.Label(label="Kısayollar:  Boşluk = oynat/duraklat,  ← → = 10 sn,\n"
                                    "Backspace = geri,  Ctrl+O = dosya aç,  F5 = yenile.\n"
                                    "Dosya ya da klasörü pencereye sürükleyip bırakabilirsin.",
                              xalign=0, css_classes=["soluk", "kucuk"]))
        menu.append(Gtk.Label(label=f"DLNA Cast {__version__}", xalign=0, css_classes=["soluk", "kucuk"]))
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Menü",
                                       popover=Gtk.Popover(child=menu))
        header.pack_end(self.menu_btn)

        self.update_btn = Gtk.Button(label="Güncelle", visible=False, css_classes=["suggested-action"])
        self.update_btn.connect("clicked", lambda *_: self.start_update())
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Cihazları yeniden ara (F5)")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_btn)
        self.search_spinner = Gtk.Spinner(visible=False, tooltip_text="Ağdaki cihazlar aranıyor…")
        header.pack_end(self.search_spinner)
        header.pack_end(self.update_btn)
        self.alici_label = Gtk.Label(label="● Alıcı açık", visible=False, css_classes=["alici-isareti"],
                                     tooltip_text="Ağdaki cihazlar bu bilgisayara video gönderebilir")
        header.pack_start(self.alici_label)

    # ---------------- 1. sekme: Yayınla (cast sender) ----------------
    def _build_cast_tab(self) -> Gtk.Widget:
        # Medya kartı
        media, mbox = card("Medya")
        top = Gtk.Box(spacing=10)
        top.append(Gtk.Label(label="Kaynak", css_classes=["soluk"]))
        self.source_dd = Gtk.DropDown.new_from_strings(["Bilgisayardaki dosya", "Ağ adresi (URL)"])
        self.source_dd.set_hexpand(True)
        self.source_dd.connect("notify::selected", lambda *_: self.source_stack.set_visible_child_name(
            "url" if self.source_dd.get_selected() == 1 else "dosya"))
        top.append(self.source_dd)
        self.playlist_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.playlist_list.set_placeholder(placeholder("Liste boş."))
        self.playlist_list.connect("row-activated", lambda _l, r: self._play_from_list(r.get_index()))
        pl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                         margin_start=6, margin_end=6)
        pl_box.append(Gtk.Label(label="Oynatma listesi", xalign=0, margin_start=8, css_classes=["calan-baslik"]))
        pl_scroll = Gtk.ScrolledWindow(child=self.playlist_list, propagate_natural_height=True,
                                       max_content_height=360, min_content_width=340,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER)
        pl_box.append(pl_scroll)
        self.playlist_label = Gtk.Label(label="Liste · 0")
        pl_child = Gtk.Box(spacing=8)
        pl_child.append(Gtk.Image(icon_name="view-list-symbolic"))
        pl_child.append(self.playlist_label)
        self.playlist_btn = Gtk.MenuButton(child=pl_child, tooltip_text="Sıradaki videolar",
                                           popover=Gtk.Popover(child=pl_box))
        top.append(self.playlist_btn)
        mbox.append(top)

        self.source_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        filebox = Gtk.Box(spacing=10, css_classes=["ic-kutu"])
        ftxt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.media_title = Gtk.Label(xalign=0, ellipsize=3, css_classes=["calan-baslik"])
        self.media_sub = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        ftxt.append(self.media_title)
        ftxt.append(self.media_sub)
        filebox.append(ftxt)
        browse = action_button("folder-open-symbolic", "Gözat…", "Bilgisayardan video ya da müzik seç (Ctrl+O)",
                               self.cast_browse)
        clear_btn = action_button("edit-clear-symbolic", "Temizle", "Seçili medyayı bırak", self.clear_media)
        for b in (browse, clear_btn):
            b.add_css_class("flat")
            b.set_valign(Gtk.Align.CENTER)
            filebox.append(b)
        self.source_stack.add_named(filebox, "dosya")
        urlbox = Gtk.Box(spacing=10, css_classes=["ic-kutu"])
        self.url_entry = Gtk.Entry(hexpand=True, placeholder_text="https://ornek.com/video.mp4",
                                   input_purpose=Gtk.InputPurpose.URL)
        self.url_entry.connect("activate", lambda *_: self.add_url())
        urlbox.append(self.url_entry)
        add_url = action_button("list-add-symbolic", "Ekle", "Bu adresi oynatma listesine koy", self.add_url)
        urlbox.append(add_url)
        self.source_stack.add_named(urlbox, "url")
        mbox.append(self.source_stack)

        # Oynatma kartı
        play, pbox = card("Oynatma")
        main = Gtk.Box(spacing=16)
        art = Gtk.Overlay(css_classes=["kare"], overflow=Gtk.Overflow.HIDDEN, valign=Gtk.Align.START)
        art.set_size_request(120, 120)
        self.art_icon = Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=48, css_classes=["soluk"],
                                  halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        art.set_child(self.art_icon)
        self.art_pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        art.add_overlay(self.art_pic)
        main.append(art)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        self.cast_title = Gtk.Label(label="Bir cihaz seç", xalign=0, ellipsize=3, css_classes=["calan-baslik"])
        self.cast_dev = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        right.append(self.cast_title)
        right.append(self.cast_dev)
        right.append(self._seek_row())
        btns = Gtk.Box(spacing=6)
        self.cast_btn = Gtk.Button(css_classes=["suggested-action", "eylem"],
                                   tooltip_text="Seçili medyayı seçili cihazda oynat / duraklat")
        cb = Gtk.Box(spacing=8)
        self.cast_btn_icon = Gtk.Image(icon_name="media-playback-start-symbolic")
        self.cast_btn_label = Gtk.Label(label="Yayınla")
        cb.append(self.cast_btn_icon)
        cb.append(self.cast_btn_label)
        self.cast_btn.set_child(cb)
        self.cast_btn.connect("clicked", lambda *_: self.cast_clicked())
        btns.append(self.cast_btn)
        stop = action_button("media-playback-stop-symbolic", "Durdur", "Oynatmayı durdur",
                             lambda: self.send_control("stop"))
        stop.add_css_class("flat")
        btns.append(stop)
        self.c_prev = Gtk.Button(icon_name="media-skip-backward-symbolic", tooltip_text="Önceki",
                                 css_classes=["flat"], sensitive=False)
        self.c_prev.connect("clicked", lambda *_: self.prev_item())
        self.c_next = Gtk.Button(icon_name="media-skip-forward-symbolic", tooltip_text="Sonraki",
                                 css_classes=["flat"], sensitive=False)
        self.c_next.connect("clicked", lambda *_: self.next_item())
        btns.append(self.c_prev)
        btns.append(self.c_next)
        btns.append(Gtk.Box(hexpand=True))
        btns.append(Gtk.Label(label="Ses", css_classes=["soluk"]))
        for text, delta, tip in (("−", -5, "Sesi kıs"), ("+", 5, "Sesi aç")):
            b = Gtk.Button(label=text, tooltip_text=tip, css_classes=["flat", "yuvarlak", "ses-dugmesi"])
            b.connect("clicked", lambda _b, d=delta: self.change_volume(d))
            btns.append(b)
        self.vol_label = Gtk.Label(label="50", width_chars=3, css_classes=["soluk", "kucuk"])
        btns.append(self.vol_label)
        self.mute_btn = Gtk.ToggleButton(css_classes=["flat"], tooltip_text="Sesi kapat / aç")
        mb = Gtk.Box(spacing=6)
        self.mute_icon = Gtk.Image(icon_name="audio-volume-high-symbolic")
        mb.append(self.mute_icon)
        mb.append(Gtk.Label(label="Sessiz"))
        self.mute_btn.set_child(mb)
        self.mute_btn.connect("toggled", self.on_mute_toggled)
        btns.append(self.mute_btn)
        right.append(btns)
        main.append(right)
        pbox.append(main)
        pbox.append(Gtk.Separator(margin_top=4))
        opts = Gtk.Box(spacing=28)
        for label, icon, sw in (("Döngü", "media-playlist-repeat-symbolic", self.loop_switch),
                                ("Sıradakine geç", "media-skip-forward-symbolic", self.autonext)):
            o = Gtk.Box(spacing=8)
            o.append(Gtk.Image(icon_name=icon))
            o.append(Gtk.Label(label=label))
            sw.set_valign(Gtk.Align.CENTER)
            o.append(sw)
            opts.append(o)
        pbox.append(opts)
        st = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        self.status_labels.append(st)
        pbox.append(st)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        left.append(media)
        left.append(play)

        # Cihazlar kartı
        devs, dbox = card("Cihazlar")
        devs.set_size_request(300, -1)
        self.dev_count = Gtk.Label(xalign=0, css_classes=["soluk"])
        dbox.append(self.dev_count)
        self.cast_devs = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["cihazlar"])
        self.cast_devs.connect("row-activated", lambda _l, r: self.picker.set_selected(r.get_index()))
        dbox.append(self.cast_devs)
        dbox.append(Gtk.Label(label="TV listede yoksa açık ve aynı ağda olduğundan emin ol, "
                                    "sonra sağ üstteki yenile düğmesine bas.", xalign=0, wrap=True,
                              vexpand=True, valign=Gtk.Align.END, css_classes=["soluk", "kucuk"]))

        page = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        page.append(page_scroll(left))
        page.append(devs)
        return page

    def _seek_row(self) -> Gtk.Box:
        """İlerleme çubuğu (konum, çubuk, süre); birden çok yerde kullanılır."""
        pos = Gtk.Label(label="0:00", width_chars=7, xalign=1, css_classes=["soluk", "kucuk"])
        dur = Gtk.Label(label="0:00", width_chars=7, xalign=0, css_classes=["soluk", "kucuk"])
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_sensitive(False)
        scale.connect("change-value", self.on_seek_changed)
        self.seek_scales.append(scale)
        self.pos_labels.append(pos)
        self.dur_labels.append(dur)
        box = Gtk.Box(spacing=8)
        box.append(pos)
        box.append(scale)
        box.append(dur)
        return box

    # ---------------- 2. sekme: Alıcı (DLNA renderer) ----------------
    def _build_receiver_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        top, tbox = card()
        tbox.append(switch_row("Alıcıyı aç",
                               "Açıkken telefon ve bilgisayarlar bu bilgisayarı TV gibi görür; "
                               "gönderdikleri video ve müzik burada oynar.",
                               self.renderer_switch, icon="video-display-symbolic"))
        self.embed_switch = Gtk.Switch(active=bool(self.rplayer) and self.config["alici_gomulu"],
                                       sensitive=self.rplayer is not None)
        tbox.append(switch_row("Program içinde oynat",
                               "Gelen video aşağıdaki oynatıcıda açılır. Kapalıysa ayrı bir mpv "
                               "penceresinde açılır." if self.rplayer else
                               "Kullanılamıyor: python-mpv kurulu değil, video ayrı pencerede açılır.",
                               self.embed_switch, icon="view-reveal-symbolic"))
        self.embed_switch.connect("notify::active", self.on_embed_switch)
        tbox.append(Gtk.Separator())
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        self.rcv_state = Gtk.Label(xalign=0, css_classes=["calan-baslik"])
        self.rcv_name = Gtk.Label(label=self.renderer.name, xalign=0, selectable=True, ellipsize=3)
        self.rcv_now = Gtk.Label(xalign=0, ellipsize=3)
        self.rcv_subs = Gtk.Label(xalign=0)
        for i, (k, w) in enumerate((("Durum", self.rcv_state), ("Ağda görünen adı", self.rcv_name),
                                    ("Şu an çalan", self.rcv_now), ("Bağlı kumanda", self.rcv_subs))):
            grid.attach(Gtk.Label(label=k, xalign=0, css_classes=["soluk"]), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        tbox.append(grid)
        page.append(top)
        self.rcv_page = page
        self.rcv_top = top
        self.rcv_video_card = self._build_video_card()
        page.append(self.rcv_video_card)

        log, lbox = card("Son olaylar", spacing=4)
        self.rcv_log = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.rcv_log.set_placeholder(placeholder("Henüz bir şey olmadı. Telefonda bu bilgisayar "
                                                 f"\"{self.renderer.name}\" adıyla görünür."))
        lbox.append(Gtk.ScrolledWindow(child=self.rcv_log, min_content_height=70, max_content_height=70,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))
        page.append(log)
        self.rcv_log_card = log
        # Kaydırma yok: oynatıcı kalan bütün alanı kaplasın.
        return page

    # ---------------- alıcının program içi oynatıcısı ----------------
    def _pick_renderer_player(self):
        """Ayara göre alıcının kullanacağı oynatıcı: program içi ya da ayrı mpv penceresi."""
        if self.rplayer is not None and self.config.get("alici_gomulu", True):
            return self.rplayer
        if self._ext_player is None:
            from .localplayer import LocalPlayer
            self._ext_player = LocalPlayer(tag="-alici", title="DLNA Cast (alıcı)")
        return self._ext_player

    def on_embed_switch(self, switch: Gtk.Switch, _pspec) -> None:
        self.config["alici_gomulu"] = switch.get_active()
        save_config(self.config)
        if self._video_fs and not switch.get_active():
            self.unfullscreen()
        old = self.renderer.player
        new = self._pick_renderer_player()
        if new is not old:
            old.close()  # çalan varsa durur; telefondan yeniden gönderince yeni yerde açılır
            self.renderer.player = new
            self.renderer._loaded_uri = ""
        self.rcv_video_card.set_visible(self.renderer.player is self.rplayer)
        self._update_video_bar()

    def _build_video_card(self) -> Gtk.Widget:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["kart"], spacing=8, vexpand=True)
        self.video_title = Gtk.Label(label="Oynatıcı", xalign=0, ellipsize=3, css_classes=["kart-baslik"])
        frame.append(self.video_title)
        self.video_area = Gtk.Overlay(css_classes=["video-alani"], overflow=Gtk.Overflow.HIDDEN,
                                      vexpand=True)
        self.video_area.set_size_request(-1, 300)
        self.video_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.CENTER, css_classes=["video-bos"])
        self.video_empty_icon = Gtk.Image(icon_name="video-display-symbolic", pixel_size=56)
        empty.append(self.video_empty_icon)
        self.video_empty_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        empty.append(self.video_empty_label)
        self.video_stack.add_named(empty, "bos")
        if self.rplayer is not None:
            self.video_stack.add_named(self.rplayer.widget, "video")
            self.rplayer.on_change = self._on_rplayer_change
        audio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.CENTER, css_classes=["video-bos"])
        audio.append(Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=64))
        self.video_audio_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                           css_classes=["calan-baslik"])
        audio.append(self.video_audio_label)
        self.video_stack.add_named(audio, "muzik")
        self.video_area.set_child(self.video_stack)

        # alt çubuk: oynat/duraklat, sarma, ilerleme, ses, tam ekran
        bar = Gtk.Box(spacing=6, css_classes=["video-cubugu"])

        def vbtn(icon: str, tip: str, fn) -> Gtk.Button:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat", "yuvarlak"])
            b.connect("clicked", lambda _b: fn())
            bar.append(b)
            return b

        self.v_play = vbtn("media-playback-pause-symbolic", "Oynat / Duraklat (Boşluk)", self._rp_toggle)
        vbtn("media-seek-backward-symbolic", "10 sn geri (←)", lambda: self._rp_seek_rel(-SKIP_SECONDS))
        vbtn("media-seek-forward-symbolic", "10 sn ileri (→)", lambda: self._rp_seek_rel(SKIP_SECONDS))
        self.v_pos = Gtk.Label(label="0:00", width_chars=7, xalign=1, css_classes=["kucuk"])
        self.v_dur = Gtk.Label(label="0:00", width_chars=7, xalign=0, css_classes=["kucuk"])
        self.v_seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.v_seek.set_draw_value(False)
        self.v_seek.set_hexpand(True)
        self.v_seek.connect("change-value", self._rp_seek_drag)
        self._v_seek_touch = 0.0
        bar.append(self.v_pos)
        bar.append(self.v_seek)
        bar.append(self.v_dur)
        bar.append(Gtk.Image(icon_name="audio-volume-high-symbolic"))
        self.v_vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.v_vol.set_draw_value(False)
        self.v_vol.set_size_request(100, -1)
        self.v_vol.connect("change-value", self._rp_volume)
        bar.append(self.v_vol)
        self.v_full = vbtn("view-fullscreen-symbolic", "Tam ekran (F)", self.toggle_video_fullscreen)
        self.video_bar = Gtk.Revealer(child=bar, reveal_child=True, valign=Gtk.Align.END,
                                      transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        self.video_area.add_overlay(self.video_bar)
        self._bar_hide_src = 0
        self._rp_new = ""

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_video_click)
        self.video_area.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._show_video_bar())
        self.video_area.add_controller(motion)
        frame.append(self.video_area)
        self._video_fs = False
        self.connect("notify::fullscreened", self._on_fullscreened)
        frame.set_visible(self.rplayer is not None and self.renderer.player is self.rplayer)
        GLib.timeout_add(500, self._update_video_bar)
        return frame

    def _on_rplayer_change(self) -> None:
        """Gömülü oynatıcıda yeni medya başladı/durdu: görünümü güncelle."""
        p = self.rplayer
        if p is None:
            return
        if p.has_media:
            self.video_stack.set_visible_child_name("muzik" if p.is_audio else "video")
            self.video_audio_label.set_text(p.title)
            self.video_title.set_text(p.title or "Oynatıcı")
            if not p.is_audio and self.renderer.running and self._rp_new != p.title:
                self.tabs.set_visible_child_name("alici")  # gelen videoyu hemen göster
            self._rp_new = p.title
        else:
            self._rp_new = ""
            self.video_stack.set_visible_child_name("bos")
            self.video_title.set_text("Oynatıcı")
            # Tam ekran bozulmaz: telefon art arda foto/video gönderirken arada kısa süre boş kalır.
        self._update_video_bar()

    def _rp_active(self) -> bool:
        return (self.rplayer is not None and self.renderer.player is self.rplayer
                and self.rplayer.has_media)

    def _update_video_bar(self) -> bool:
        if self.rplayer is None:
            return False
        if self._video_fs:
            empty = ""  # tam ekranda sıradakini beklerken sade siyah ekran
        elif self.renderer.running:
            empty = "Telefondan gönderilen video ve fotoğraf burada açılır."
        else:
            empty = "Alıcı kapalı. Yukarıdaki \"Alıcıyı aç\" anahtarını aç."
        self.video_empty_label.set_text(empty)
        self.video_empty_icon.set_visible(bool(empty))
        if not self._rp_active():
            for w in (self.v_pos, self.v_dur):
                w.set_text("0:00")
            self.v_seek.set_value(0)
            return True
        m = self.rplayer.mpv
        try:
            pos, dur, paused, vol = m.time_pos, m.duration, m.pause, m.volume
        except Exception:  # noqa: BLE001
            return True
        self.v_play.set_icon_name("media-playback-start-symbolic" if paused
                                  else "media-playback-pause-symbolic")
        if dur:
            self.v_seek.set_range(0, dur)
            self.v_dur.set_text(fmt_time(dur))
        if pos is not None and time.monotonic() - self._v_seek_touch > 1.5:
            self.v_seek.set_value(pos)
            self.v_pos.set_text(fmt_time(pos))
        if vol is not None:
            self.v_vol.set_value(vol)
        return True

    def _rp_toggle(self) -> None:
        if self._rp_active():
            self.rplayer.toggle_pause()
            self.renderer.events.changed()  # telefondaki uygulamaya hemen bildir
            GLib.timeout_add(150, lambda: self._update_video_bar() and False)

    def _rp_seek_rel(self, delta: float) -> None:
        if self._rp_active():
            self.rplayer.seek_relative(delta)

    def _rp_seek_drag(self, _scale, _scroll, value: float) -> bool:
        self._v_seek_touch = time.monotonic()
        self.v_pos.set_text(fmt_time(value))
        if self._rp_active():
            self.rplayer.mpv.seek(value, "absolute")
        return False

    def _rp_volume(self, _scale, _scroll, value: float) -> bool:
        if self.rplayer is not None:
            level = int(max(0, min(100, value)))
            self.rplayer.mpv.volume = level
            self.renderer.volume = level
            self.renderer.events.changed()
        return False

    def _on_video_click(self, _gesture: Gtk.GestureClick, n_press: int, _x, _y) -> None:
        self._show_video_bar()
        if n_press == 2:
            self.toggle_video_fullscreen()

    def toggle_video_fullscreen(self) -> None:
        if self._video_fs:
            self.unfullscreen()
        elif self.rplayer is not None and self.renderer.player is self.rplayer:
            self.tabs.set_visible_child_name("alici")
            self._video_fs = True
            self._apply_video_fs(True)
            self.fullscreen()

    def _on_fullscreened(self, *_args) -> None:
        if not self.is_fullscreen() and self._video_fs:  # tam ekrandan çıkıldı (Esc, pencere yöneticisi)
            self._video_fs = False
            self._apply_video_fs(False)

    def _apply_video_fs(self, on: bool) -> None:
        """Tam ekranda sadece video görünür; çıkınca her şey yerine döner."""
        titlebar = self.get_titlebar()
        if titlebar is not None:
            titlebar.set_visible(not on)
        for w in (self.rcv_top, self.rcv_log_card, self.video_title):
            w.set_visible(not on)
        m = 0 if on else 12
        for setter in (self.rcv_page.set_margin_top, self.rcv_page.set_margin_bottom,
                       self.rcv_page.set_margin_start, self.rcv_page.set_margin_end):
            setter(m)
        if on:
            self.rcv_video_card.remove_css_class("kart")
            self.video_area.add_css_class("tam-ekran")
            self._show_video_bar()
        else:
            self.rcv_video_card.add_css_class("kart")
            self.video_area.remove_css_class("tam-ekran")
            if self._bar_hide_src:
                GLib.source_remove(self._bar_hide_src)
                self._bar_hide_src = 0
            self.video_bar.set_reveal_child(True)
            self.video_area.set_cursor(None)
            self._update_video_bar()
        self.v_full.set_icon_name("view-restore-symbolic" if on else "view-fullscreen-symbolic")
        self.v_full.set_tooltip_text("Tam ekrandan çık (Esc)" if on else "Tam ekran (F)")

    def _show_video_bar(self) -> None:
        """Tam ekranda fare oynayınca çubuğu ve imleci göster, 3 sn sonra gizle."""
        self.video_bar.set_reveal_child(True)
        self.video_area.set_cursor(None)
        if not self._video_fs:
            return
        if self._bar_hide_src:
            GLib.source_remove(self._bar_hide_src)
        self._bar_hide_src = GLib.timeout_add_seconds(3, self._hide_video_bar)

    def _hide_video_bar(self) -> bool:
        self._bar_hide_src = 0
        if self._video_fs:
            self.video_bar.set_reveal_child(False)
            self.video_area.set_cursor_from_name("none")
        return False

    # ---- alıcı sekmesindeyken tuşlar oynatıcıya gider ----
    def _on_receiver_tab(self) -> bool:
        return self.tabs.get_visible_child_name() == "alici" and self._rp_active()

    def on_space(self):
        if self._on_receiver_tab():
            self._rp_toggle()
        else:
            self.toggle_play()

    def on_arrow(self, delta: float):
        if self._on_receiver_tab():
            self._rp_seek_rel(delta)
        else:
            self.skip(delta)

    def on_fullscreen_key(self):
        if self.tabs.get_visible_child_name() != "alici" and not self._video_fs:
            return False
        if self.rplayer is None or self.renderer.player is not self.rplayer:
            return False
        self.toggle_video_fullscreen()
        return True

    def on_escape(self):
        if not self._video_fs:
            return False  # Esc başka yerde (açılır menü vb.) kullanılsın
        self.unfullscreen()
        return True

    # ---------------- 3. sekme: Ağda Gez (DMP) ----------------
    def _build_browse_tab(self) -> Gtk.Widget:
        # ---- üst çubuk: hedef cihaz ----
        self.picker = DevicePicker(self.on_renderer_changed)
        self.picker.on_rebuilt = self._sync_cast_devices
        bar = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        bar.append(self.picker)

        # ---- sol panel: kaynaklar ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["kenar"])
        side.set_size_request(250, -1)
        side.append(section_title("Ağdaki sunucular"))
        self.dev_list = Gtk.ListBox()
        self.dev_list.set_placeholder(placeholder("Ağda medya sunucusu bulunamadı. "
                                                  "Sağ üstteki yenile düğmesiyle tekrar ara."))
        self.dev_list.connect("row-activated", self.on_server_activated)
        side.append(self.dev_list)
        side.append(section_title("Bu bilgisayar"))
        acts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=10, margin_end=10)
        for btn in (action_button("document-open-symbolic", "Dosya aç",
                                  "Bilgisayardan dosya(lar) seç ve sırayla oynat", self.open_file),
                    action_button("folder-open-symbolic", "Klasör aç",
                                  "Bilgisayardan klasör aç, içindeki videolar listelensin",
                                  self.play_local_folder)):
            btn.add_css_class("flat")
            acts.append(btn)
        side.append(acts)
        self.local_list = Gtk.ListBox()
        self.local_list.set_placeholder(placeholder("Açtığın ve paylaştığın klasörler burada görünür."))
        self.local_list.connect("row-activated", self.on_server_activated)
        side.append(self.local_list)
        side_scroll = Gtk.ScrolledWindow(child=side, hscrollbar_policy=Gtk.PolicyType.NEVER)

        # ---- sağ alan: klasör içeriği ----
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", sensitive=False,
                                   tooltip_text="Geri", css_classes=["flat"])
        self.back_btn.connect("clicked", lambda *_: self.go_back())
        self.view_title = Gtk.Label(xalign=0, ellipsize=3, css_classes=["buyuk-baslik"])
        self.view_sub = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titles.append(self.view_title)
        titles.append(self.view_sub)
        self.play_all_btn = action_button("media-playback-start-symbolic", "Tümünü oynat",
                                          "Bu klasördeki her şeyi baştan sırayla oynat", self.play_all)
        self.play_all_btn.add_css_class("suggested-action")
        topbar = Gtk.Box(spacing=8, margin_top=10, margin_bottom=6, margin_start=12, margin_end=16)
        topbar.append(self.back_btn)
        topbar.append(titles)
        self.grid_mode = self.config.get("gorunum", "izgara") == "izgara"
        self.view_btn = Gtk.Button(css_classes=["flat"])
        self.view_btn.connect("clicked", lambda *_: self.toggle_view())
        self._update_view_btn()
        topbar.append(self.view_btn)
        topbar.append(self.play_all_btn)

        self.item_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)  # çalan satır ayrıca vurgulanır
        self.item_list.set_placeholder(placeholder("Bu klasörde video ya da müzik yok."))
        self.item_list.connect("row-activated", self.on_entry_activated)
        liste = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["icerik"])
        liste.append(topbar)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                activate_on_single_click=True, valign=Gtk.Align.START,
                                max_children_per_line=30, min_children_per_line=2,
                                column_spacing=6, row_spacing=6, margin_start=10, margin_end=10,
                                margin_bottom=10)
        self.flow.connect("child-activated", lambda _f, tile: self.on_entry_activated(None, tile))
        self.items_stack = Gtk.Stack(vexpand=True)
        self.items_stack.add_named(Gtk.ScrolledWindow(child=self.item_list,
                                                      hscrollbar_policy=Gtk.PolicyType.NEVER), "liste")
        self.items_stack.add_named(Gtk.ScrolledWindow(child=self.flow,
                                                      hscrollbar_policy=Gtk.PolicyType.NEVER), "izgara")
        liste.append(self.items_stack)

        welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        welcome.append(Gtk.Image(icon_name="network-server-symbolic", pixel_size=72,
                                 css_classes=["soluk"]))
        welcome.append(Gtk.Label(label="Ağdaki medyalarda gez", css_classes=["karsilama-baslik"]))
        steps = Gtk.Grid(column_spacing=10, row_spacing=8, halign=Gtk.Align.CENTER, margin_top=4)
        for i, text in enumerate(("Soldan ağdaki bir sunucuyu (ya da bilgisayarındaki bir klasörü) seç.",
                                  "Üstteki \"Şurada oynat\" düğmesinden cihazı (TV) seç.",
                                  "Listede bir videoya tıkla; hemen oynamaya başlar.")):
            steps.attach(Gtk.Label(label=str(i + 1), css_classes=["adim"]), 0, i, 1, 1)
            steps.attach(Gtk.Label(label=text, xalign=0, css_classes=["soluk"]), 1, i, 1, 1)
        welcome.append(steps)

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner(width_request=32, height_request=32)
        loading.append(self.spinner)
        self.loading_label = Gtk.Label(css_classes=["soluk"])
        loading.append(self.loading_label)

        self.view_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
        self.view_stack.add_named(welcome, "karsilama")
        self.view_stack.add_named(liste, "liste")
        self.view_stack.add_named(loading, "yukleniyor")

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=260, vexpand=True,
                          shrink_start_child=False, resize_start_child=False)
        paned.set_start_child(side_scroll)
        paned.set_end_child(self.view_stack)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(bar)
        box.append(Gtk.Separator())
        box.append(paned)
        box.append(self._build_player_bar())
        return box

    def _build_player_bar(self) -> Gtk.Widget:
        """Ağda Gez sekmesinin altındaki oynatıcı paneli."""
        self.now_icon = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=28,
                                  css_classes=["soluk"])
        self.now_title = Gtk.Label(label="Çalan bir şey yok", xalign=0, ellipsize=3,
                                   css_classes=["calan-baslik"])
        self.now_sub = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        now_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        now_txt.append(self.now_title)
        now_txt.append(self.now_sub)
        now = Gtk.Box(spacing=10)
        now.set_size_request(280, -1)
        now.append(self.now_icon)
        now.append(now_txt)

        controls = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)

        def add_btn(icon: str, tip: str, fn, big: bool = False) -> Gtk.Button:
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER,
                             css_classes=["oynat-dugmesi", "suggested-action"] if big else ["yuvarlak", "flat"])
            btn.connect("clicked", lambda _b: fn())
            controls.append(btn)
            return btn

        self.prev_btn = add_btn("media-skip-backward-symbolic", "Önceki", self.prev_item)
        add_btn("media-seek-backward-symbolic", f"{SKIP_SECONDS} sn geri",
                lambda: self.skip(-SKIP_SECONDS))
        self.play_btn = add_btn("media-playback-start-symbolic", "Oynat / Duraklat",
                                self.toggle_play, big=True)
        add_btn("media-seek-forward-symbolic", f"{SKIP_SECONDS} sn ileri",
                lambda: self.skip(SKIP_SECONDS))
        self.next_btn = add_btn("media-skip-forward-symbolic", "Sonraki", self.next_item)
        add_btn("media-playback-stop-symbolic", "Durdur", lambda: self.send_control("stop"))
        self.prev_btn.set_sensitive(False)
        self.next_btn.set_sensitive(False)

        self.vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.vol_scale.set_draw_value(False)
        self.vol_scale.set_size_request(120, -1)
        self.vol_scale.set_value(self._vol)
        self.vol_scale.connect("change-value", self.on_volume_changed)
        right = Gtk.Box(spacing=8, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        right.set_size_request(280, -1)
        self.vol_scale.set_tooltip_text("Ses düzeyi")
        right.append(Gtk.Image(icon_name="audio-volume-high-symbolic"))
        right.append(self.vol_scale)

        row1 = Gtk.CenterBox()
        row1.set_start_widget(now)
        row1.set_center_widget(controls)
        row1.set_end_widget(right)

        seekbox = self._seek_row()
        self.status = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        self.status_labels.append(self.status)
        player = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["oynatici"])
        for w, top, bottom in ((row1, 10, 0), (seekbox, 0, 0), (self.status, 0, 8)):
            w.set_margin_start(16)
            w.set_margin_end(16)
            w.set_margin_top(top)
            w.set_margin_bottom(bottom)
            player.append(w)
        return player

    # ---------------- 4. sekme: Sunucu (DLNA server) ----------------
    def _build_server_tab(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        top, tbox = card()
        tbox.append(switch_row("Klasörleri ağda paylaş",
                               "Açıkken aşağıdaki klasörler ağdaki TV, telefon ve bilgisayarlarda "
                               "görünür; oradan açıp izleyebilirsin.",
                               self.server_switch, icon="folder-publicshare-symbolic"))
        tbox.append(Gtk.Separator())
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        self.srv_state = Gtk.Label(xalign=0, css_classes=["calan-baslik"])
        self.srv_name = Gtk.Label(label=self.mserver.name, xalign=0, selectable=True, ellipsize=3)
        self.srv_addr = Gtk.Label(xalign=0, selectable=True, ellipsize=3, css_classes=["kucuk"])
        for i, (k, w) in enumerate((("Durum", self.srv_state), ("Ağda görünen adı", self.srv_name),
                                    ("Adres", self.srv_addr))):
            grid.attach(Gtk.Label(label=k, xalign=0, css_classes=["soluk"]), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        tbox.append(grid)
        page.append(top)

        shares, sbox = card("Paylaşılan klasörler")
        self.shares_panel = SharesPanel(self)
        sbox.append(self.shares_panel)
        sbox.append(Gtk.Label(label="TV'de görmek için: TV'nin \"Medya\" ya da \"Kaynaklar\" menüsünde "
                                    f"\"{self.mserver.name}\" adını seç.", xalign=0, wrap=True,
                              css_classes=["soluk", "kucuk"]))
        page.append(shares)
        return page_scroll(page)

    # ---------------- sekmeler arası yardımcılar ----------------
    def on_tab_changed(self, *_args) -> None:
        self.config["sekme"] = self.tabs.get_visible_child_name()
        save_config(self.config)

    def _sync_cast_devices(self) -> None:
        """Yayınla sekmesindeki cihaz listesini "Şurada oynat" seçicisiyle aynı tutar."""
        clear(self.cast_devs)
        sel = self.picker.get_selected()
        for i, d in enumerate(self.renderers):
            icon = DEVICE_LOOK.get(d.kind, DEVICE_LOOK["renderer"])[0]
            badge_txt, badge_css = DEVICE_BADGE.get(d.kind, DEVICE_BADGE["renderer"])
            box = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=d.name, xalign=0, hexpand=True, ellipsize=3))
            box.append(Gtk.Label(label=badge_txt, css_classes=["rozet-yazi", badge_css]))
            box.append(Gtk.Image(icon_name="object-select-symbolic", visible=(i == sel)))
            row = Gtk.ListBoxRow(child=box)
            if i == sel:
                row.add_css_class("secili-cihaz")
            self.cast_devs.append(row)
        n = len(self.renderers) - 1
        self.dev_count.set_text(f"Ağda {n} cihaz bulundu" if n > 0 else
                                ("Aranıyor…" if self._discovering else "Ağda cihaz bulunamadı"))
        target = self.selected_renderer()
        self.cast_dev.set_text(f"Cihaz: {target.name}" if target else "Sağdan bir cihaz seç")

    def _update_cast_media(self) -> None:
        """Yayınla sekmesindeki Medya kartını ve oynatma listesini günceller."""
        n = len(self.queue)
        self.playlist_label.set_text(f"Liste · {n}")
        clear(self.playlist_list)
        for i, it in enumerate(self.queue):
            icon = "audio-volume-high-symbolic" if i == self.queue_idx else (
                "audio-x-generic-symbolic" if guess_mime(it.path or it.uri or "").startswith("audio/")
                else "video-x-generic-symbolic")
            self.playlist_list.append(Row(it.title, it, icon=icon))
        if not self.queue:
            self.media_title.set_text("Medya seçilmedi")
            self.media_sub.set_text("Yayınlamak için \"Gözat…\" ile bir video ya da müzik seç.")
            self.cast_title.set_text("Medya seçilmedi")
            self._set_art(None)
            return
        idx = max(0, self.queue_idx)
        it = self.queue[idx]
        self.media_title.set_text(it.title)
        where = it.path or it.uri or ""
        self.media_sub.set_text(where + (f"   ·   {n} öğeli listenin {idx + 1}. öğesi" if n > 1 else ""))
        self.cast_title.set_text(it.title)
        self._set_art(it)

    def _set_art(self, item: Optional[QueueItem]) -> None:
        """Oynatma kartındaki kapak/kare: yerel dosyadan küçük resim, yoksa simge."""
        self._art_gen += 1
        gen = self._art_gen
        self.art_pic.set_visible(False)
        self.art_icon.set_visible(True)
        audio = bool(item) and guess_mime(item.path or item.uri or "").startswith("audio/")
        self.art_icon.set_from_icon_name("audio-x-generic-symbolic" if audio or item is None
                                         else "video-x-generic-symbolic")
        if item is None or not item.path:
            return

        def job() -> None:
            try:
                path = kucukresim.from_local(item.path, audio=audio)
                if not path:
                    return
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 240, 240, True)
            except Exception:  # noqa: BLE001  (resim yoksa simge kalır)
                return
            to_ui(self._show_art, gen, pix)

        self._thumbs.submit(job)

    def _show_art(self, gen: int, pix) -> None:
        if gen == self._art_gen:
            self.art_pic.set_paintable(Gdk.Texture.new_for_pixbuf(pix))
            self.art_pic.set_visible(True)
            self.art_icon.set_visible(False)

    def cast_browse(self) -> None:
        dialog = Gtk.FileDialog(title="Yayınlanacak dosya(lar)ı seç")
        dialog.set_filters(self._media_filters())
        dialog.open_multiple(self, None, self.on_cast_files)

    def on_cast_files(self, dialog, result) -> None:
        try:
            model = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # iptal edildi
        paths = [model.get_item(i).get_path() for i in range(model.get_n_items())]
        self.set_cast_media([p for p in paths if p])

    def set_cast_media(self, paths: List[str]) -> None:
        """Medyayı seçer ama başlatmaz; "Yayınla" düğmesi başlatır."""
        if not paths:
            return
        paths = sorted(paths, key=lambda p: natural_key(os.path.basename(p)))
        items = [QueueItem(os.path.basename(p), path=p) for p in paths]
        self.set_queue(items, [None] * len(items))
        self._pending = True
        self._update_cast_media()
        self.update_nav_buttons()
        self.set_status(f"\"{items[0].title}\" hazır. Sağdan cihazı seçip \"Yayınla\"ya bas.")

    def add_url(self) -> None:
        url = self.url_entry.get_text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.show_toast("Adres http:// ya da https:// ile başlamalı.", error=True)
            return
        item = QueueItem(title_from_url(url), uri=url, mime=guess_mime(url.split("?")[0]))
        if self._pending or not self.queue:
            self.set_queue(self.queue + [item] if self._pending else [item],
                           (self.queue_rows + [None]) if self._pending else [None])
        else:
            self.set_queue([item], [None])
        self._pending = True
        self.url_entry.set_text("")
        self._update_cast_media()
        self.update_nav_buttons()
        self.set_status("Adres listeye eklendi. \"Yayınla\"ya basınca oynar.")

    def clear_media(self) -> None:
        self.set_queue([], [])
        self._pending = False
        self._update_cast_media()
        self.update_nav_buttons()

    def cast_clicked(self) -> None:
        if self.selected_renderer() is None:
            self.show_toast("Önce sağdaki listeden bir cihaz seç.")
            return
        if not self.queue:
            self.show_toast("Önce \"Gözat…\" ile bir video ya da müzik seç.")
            return
        if self._pending:
            self.start_item(max(0, self.queue_idx))
        else:
            self.toggle_play()

    def _play_from_list(self, idx: int) -> None:
        self.playlist_btn.popdown()
        self.start_item(idx)

    def change_volume(self, delta: int) -> None:
        self._set_volume(max(0, min(100, self._vol + delta)))

    def on_mute_toggled(self, btn: Gtk.ToggleButton) -> None:
        if btn.get_active():
            if self._vol > 0:
                self._unmute_vol = self._vol
                self._set_volume(0)
        else:
            self._set_volume(self._unmute_vol or 50)
            self._unmute_vol = 0
        self.mute_icon.set_from_icon_name("audio-volume-muted-symbolic" if btn.get_active()
                                          else "audio-volume-high-symbolic")

    def _set_volume(self, level: int) -> None:
        self._vol = level
        self.vol_scale.set_value(level)
        self.vol_label.set_text(str(level))
        self.on_volume_changed(None, None, level)

    def on_loop(self, switch: Gtk.Switch, _pspec) -> None:
        self.config["dongu"] = switch.get_active()
        save_config(self.config)

    # ---- alıcı sekmesi ----
    def on_renderer_message(self, text: str) -> None:
        self.set_status(text)
        row = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        row.append(Gtk.Label(label=time.strftime("%H:%M"), css_classes=["soluk", "kucuk"]))
        row.append(Gtk.Label(label=text.removeprefix("Alıcı: "), xalign=0, wrap=True, hexpand=True))
        self.rcv_log.prepend(row)
        while (last := self.rcv_log.get_row_at_index(30)) is not None:
            self.rcv_log.remove(last)

    def _update_receiver_info(self) -> bool:
        on = self.renderer.running
        if self.rplayer is not None and hasattr(self, "video_empty_label"):
            self._update_video_bar()
        self.rcv_state.set_text("● Açık" if on else "Kapalı")
        if on:
            self.rcv_state.add_css_class("acik-yazi")
        else:
            self.rcv_state.remove_css_class("acik-yazi")
        self.rcv_now.set_text(self.renderer.title if on and self.renderer.title else "—")
        n = self.renderer.events.count if on else 0
        self.rcv_subs.set_text(f"{n} uygulama durum bildirimlerini dinliyor" if n else "Yok")
        return True

    # ---- sunucu sekmesi ----
    def on_server_switch(self, switch: Gtk.Switch, _pspec) -> None:
        self.config["sunucu"] = switch.get_active()
        self.shares_changed()

    def _update_server_info(self) -> None:
        if self.mserver.running:
            self.srv_state.set_text(f"● Açık: {len(self.mserver.shares)} klasör paylaşılıyor")
            self.srv_state.add_css_class("acik-yazi")
            self.srv_addr.set_text(self.mserver.location)
        else:
            self.srv_state.remove_css_class("acik-yazi")
            self.srv_state.set_text("Kapalı" if not self.config.get("sunucu", True)
                                    else "Kapalı: önce aşağıdan bir klasör ekle")
            self.srv_addr.set_text("—")


    def on_renderer_switch(self, switch: Gtk.Switch, _pspec) -> None:
        """Alıcı anahtarı: açınca ağda 'TV' olarak duyurulur, kapatınca kaybolur."""
        want = switch.get_active()
        if want == self.renderer.running:
            return
        if want:
            try:
                self.renderer.start()
            except Exception as exc:  # noqa: BLE001
                switch.set_active(False)
                self.show_error(exc)
                return
            self.show_toast(f"Alıcı açık: telefonunda \"{self.renderer.name}\" adıyla görünür.")
        else:
            if self._video_fs:
                self.unfullscreen()
            self.renderer.stop()
            self.show_toast("Alıcı kapatıldı.")
        self.alici_label.set_visible(want)
        self.config["renderer"] = want
        save_config(self.config)
        self._update_receiver_info()

    def on_close(self, *_args) -> bool:
        self.renderer.stop()
        if self.rplayer is not None:
            self.rplayer.terminate()
        if self._ext_player is not None:
            self._ext_player.close()
        self.mserver.stop()
        self.cp.player.close()
        self.cp.cast.close()
        self._thumb_gen += 1
        self._thumbs.shutdown(wait=False, cancel_futures=True)
        return False

    def shares_changed(self, save: bool = True) -> None:
        if save:
            save_config(self.config)
        self.mserver.set_shares(self.config["shares"])
        if save:
            self.sync_local_rows()
        want = bool(self.mserver.shares) and self.config.get("sunucu", True)
        if want and not self.mserver.running:
            try:
                self.mserver.start()
            except Exception as exc:  # noqa: BLE001
                self.show_error(exc)
        elif not want and self.mserver.running:
            self.mserver.stop()
        self._update_server_info()
        if save:
            self.set_status(f"{len(self.mserver.shares)} klasör ağda paylaşılıyor." if self.mserver.running
                            else "Klasör paylaşımı kapalı.")

    def local_dirs(self) -> List[str]:
        out: List[str] = []
        for p in list(self.config.get("shares", [])) + list(self.config.get("recent", [])):
            if p not in out and os.path.isdir(p):
                out.append(p)
        return out

    def sync_local_rows(self) -> None:
        """Paylaşılan ve açılan klasörleri sol listede gösterir (diskten gezilir)."""
        wanted = self.local_dirs()
        shared = set(self.config.get("shares", []))
        for p in list(self._local_rows):
            if p not in wanted:
                self.local_list.remove(self._local_rows.pop(p))
        for p in wanted:
            if p not in self._local_rows:
                name = os.path.basename(p.rstrip(os.sep)) or p
                icon = "folder-publicshare-symbolic" if p in shared else "folder-symbolic"
                trailing = None
                if p not in shared:
                    trailing = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "yuvarlak"],
                                          tooltip_text="Listeden kaldır (klasör silinmez)")
                    trailing.connect("clicked", lambda _b, path=p: self.forget_dir(path))
                row = Row(name, DeviceInfo(name, p, "localdir"), icon=icon,
                          subtitle="Ağda paylaşılıyor" if p in shared else "", trailing=trailing)
                row.set_tooltip_text(p)
                self.local_list.append(row)
                self._local_rows[p] = row

    def remember_dir(self, folder: str) -> None:
        """Açılan klasörü listenin başına ekler ve kaydeder (en fazla 8)."""
        recent = [p for p in self.config.get("recent", []) if p != folder]
        self.config["recent"] = [folder] + recent[:7]
        save_config(self.config)
        self.sync_local_rows()

    def forget_dir(self, folder: str) -> None:
        self.config["recent"] = [p for p in self.config.get("recent", []) if p != folder]
        save_config(self.config)
        self.sync_local_rows()

    def selected_renderer(self) -> Optional[DeviceInfo]:
        idx = self.picker.get_selected()
        if not self.renderers or idx >= len(self.renderers):
            return None
        return self.renderers[idx]

    # ---- keşif ----
    def refresh(self) -> None:
        prev = self.selected_renderer()
        self._want_renderer = prev.location if prev else None
        self._discovering = True
        self.servers.clear()
        self.renderers.clear()
        if not self.view_local:  # sunucu listesi yenileniyor; açık sunucu klasörü geçersiz olur
            self.entries = []
            self.entry_rows = []
            self.stack = []
            clear(self.item_list)
            self.view_stack.set_visible_child_name("karsilama")
        clear(self.dev_list)
        self._sel_guard = True
        try:
            self.picker.clear()
            me = DeviceInfo("Bu bilgisayar", LOCAL_PLAYER_ID, "player")
            self.renderers.append(me)
            self.picker.append(me)
        finally:
            self._sel_guard = False
        self.sync_local_rows()  # paylaşılan ve açılan klasörler diskten listelenir
        self.refresh_btn.set_sensitive(False)
        self.search_spinner.set_visible(True)
        self.search_spinner.start()
        self.set_status("Ağdaki cihazlar aranıyor…")
        self.cp.cast_hosts = list(self.config.get("cast_hosts", []))
        self.bridge.submit(
            self.cp.discover(lambda info: to_ui(self.add_device, info)),
            on_done=self.discovery_done,
            on_error=self.discovery_failed,
        )

    def add_device(self, info: DeviceInfo) -> None:
        if (info.kind == "server" and self.mserver.running
                and info.location == self.mserver.location):
            return  # kendi paylasimimiz zaten "Bu bilgisayar" olarak listede
        if (info.kind == "renderer" and self.renderer.running
                and info.location == self.renderer.location):
            return  # kendi alicimiz: "Bu bilgisayar (mpv)" zaten var
        if info.kind == "server":
            self.servers.append(info)
            self.dev_list.append(Row(info.name, info, icon="network-server-symbolic"))
        else:
            if any(r.location == info.location for r in self.renderers):
                return
            self.renderers.append(info)
            self.picker.append(info)
            if info.location == self._want_renderer:  # yenilemeden onceki hedef geri geldi
                self._sel_guard = True
                try:
                    self.picker.set_selected(len(self.renderers) - 1)
                finally:
                    self._sel_guard = False

    def discovery_done(self, count: int) -> None:
        self._discovering = False
        self._sync_cast_devices()
        self.refresh_btn.set_sensitive(True)
        self.search_spinner.stop()
        self.search_spinner.set_visible(False)
        hosts = self.cp.cast.known_hosts()  # Chromecast IP'leri: sonraki açılışta doğrudan denenir
        if hosts and set(hosts) - set(self.config.get("cast_hosts", [])):
            self.config["cast_hosts"] = (hosts + [h for h in self.config.get("cast_hosts", [])
                                                  if h not in hosts])[:8]
            save_config(self.config)
        others = len(self.renderers) - 1  # ilki her zaman "Bu bilgisayar"
        want = self._want_renderer
        if want is None:  # ilk acilis: agda TV/Chromecast varsa varsayilan hedef o olsun
            if others > 0 and self.picker.get_selected() == 0:
                self.picker.set_selected(1)
        lost = (want not in (None, LOCAL_PLAYER_ID)
                and not any(r.location == want for r in self.renderers))
        msg = f"Arama bitti: {len(self.servers)} sunucu, {others} oynatıcı cihaz bulundu."
        if lost:
            self.show_toast("Son kullandığın cihaz bulunamadı; \"Bu bilgisayar\" seçildi. "
                            "Cihaz açıksa yenile düğmesine bas.", error=True)
        if self.cp.errors:
            msg += f"  ({len(self.cp.errors)} uyarı; ayrıntı için tani.sh)"
        self.set_status(msg)

    def discovery_failed(self, exc: Exception) -> None:
        self._discovering = False
        self.refresh_btn.set_sensitive(True)
        self._sync_cast_devices()
        self.search_spinner.stop()
        self.search_spinner.set_visible(False)
        self.show_error(exc)

    # ---- gezinme ----
    def on_server_activated(self, lb, row: Row) -> None:
        other = self.dev_list if lb is self.local_list else self.local_list
        other.unselect_all()
        if row.data.kind == "localdir":
            self.open_local(row.data.location)
            return
        self.view_local = False
        self.current_server = row.data
        self.stack = [("0", row.data.name)]
        self.load_current()

    def open_local(self, folder: str) -> None:
        """Bilgisayardaki bir klasörün içeriğini doğrudan diskten listeler."""
        self.view_local = True
        self.stack = [(folder, os.path.basename(folder.rstrip(os.sep)) or folder)]
        self.load_current()

    def load_current(self) -> None:
        object_id, title = self.stack[-1]
        self.back_btn.set_sensitive(len(self.stack) > 1)
        if self.view_local:  # object_id burada klasör yolu
            try:
                self.show_entries(local_entries(object_id))
            except Exception as exc:  # noqa: BLE001
                self.show_error(exc)
            return
        self.loading_label.set_text(f"{title} yükleniyor…")
        self.spinner.start()
        self.view_stack.set_visible_child_name("yukleniyor")
        self.bridge.submit(
            self.cp.browse(self.current_server.location, object_id),
            on_done=self.show_entries,
            on_error=self.show_error,
        )

    def show_entries(self, entries: List[Entry]) -> None:
        clear(self.item_list)
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        old_rows = self.queue_rows
        self.entries = entries
        self.entry_rows = []
        self._playing_row = None
        if self.grid_mode and entries:
            self._fill_grid(entries)
        else:
            self._fill_list(entries)
        # kuyruk aynı klasördeyse yeni çizilen kareleri/satırları ona bağla
        by_entry = {id(w.data): w for w in self.entry_rows}
        self.queue_rows = [by_entry.get(id(r.data), r) if r is not None else None for r in old_rows]
        if 0 <= self.queue_idx < len(self.queue_rows):
            cur = self.queue_rows[self.queue_idx]
            if cur is not None and cur.get_parent() is not None:
                cur.set_playing(True)
                self._playing_row = cur
        self._finish_entries(entries)

    def _fill_grid(self, entries: List[Entry]) -> None:
        self.items_stack.set_visible_child_name("izgara")
        for e in entries:
            tile = Tile(e, entry_icon(e))
            self.flow.append(tile)
            self.entry_rows.append(tile)
        self._load_thumbs(list(self.entry_rows), self.view_local)

    def _fill_list(self, entries: List[Entry]) -> None:
        self.items_stack.set_visible_child_name("liste")
        for e in entries:
            if e.is_container:
                trailing = Gtk.Image(icon_name="go-next-symbolic", css_classes=["soluk"])
            elif is_playable(e):
                trailing = Gtk.Button(icon_name="media-playback-start-symbolic",
                                      css_classes=["flat", "yuvarlak"], tooltip_text="Oynat")
            else:
                trailing = None
            row = Row(e.title, e, icon=entry_icon(e), trailing=trailing)
            if isinstance(trailing, Gtk.Button):
                trailing.connect("clicked", lambda _b, r=row: self.on_entry_activated(self.item_list, r))
            self.item_list.append(row)
            self.entry_rows.append(row)

    def _finish_entries(self, entries: List[Entry]) -> None:
        self.spinner.stop()
        folders = sum(1 for e in entries if e.is_container)
        playable = sum(1 for e in entries if is_playable(e))
        parts = []
        if folders:
            parts.append(f"{folders} klasör")
        if playable:
            parts.append(f"{playable} video/müzik")
        source = "Bu bilgisayar" if self.view_local else (self.current_server.name if self.current_server else "")
        trail = " / ".join([source] + [t for _, t in self.stack[:-1]])
        self.view_title.set_text(self.stack[-1][1] if self.stack else "")
        self.view_sub.set_text(f"{trail}  ·  {', '.join(parts) or 'boş'}")
        self.play_all_btn.set_sensitive(playable > 0)
        self.view_stack.set_visible_child_name("liste")

    def on_entry_activated(self, _lb, row: Row) -> None:
        e: Entry = row.data
        if e.is_container:
            self.stack.append((e.id, e.title))
            self.load_current()
            return
        if self.selected_renderer() is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        if not e.uri:
            self.set_status("Bu öğenin oynatılabilir adresi yok.")
            return
        # Tıklanan öğeden başlayarak klasörün tamamı kuyruğa girer.
        items: List[QueueItem] = []
        rows: List[Optional[Row]] = []
        start = 0
        if is_playable(e):
            for ent, r in zip(self.entries, self.entry_rows):
                if r is row:
                    start = len(items)
                if is_playable(ent):
                    items.append(self.make_item(ent))
                    rows.append(r)
        else:  # örn. resim: tek başına oynat
            items, rows = [self.make_item(e)], [row]
        self.set_queue(items, rows)
        self.start_item(start)

    def make_item(self, ent: Entry) -> QueueItem:
        if self.view_local:  # yerel dosya: oynatılırken ağa açılır
            return QueueItem(ent.title, path=ent.id)
        return QueueItem(ent.title, uri=ent.uri, mime=ent.mime)

    def play_all(self) -> None:
        """Açık klasördeki tüm oynatılabilir öğeleri baştan sırayla oynatır."""
        if self.selected_renderer() is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        items, rows = [], []
        for ent, r in zip(self.entries, self.entry_rows):
            if is_playable(ent):
                items.append(self.make_item(ent))
                rows.append(r)
        if not items:
            self.set_status("Bu klasörde oynatılabilir öğe yok.")
            return
        self.set_queue(items, rows)
        self.start_item(0)

    # ---- yerel dosya / klasör ----
    def _media_filters(self) -> Gio.ListStore:
        flt = Gtk.FileFilter()
        flt.set_name("Video ve ses")
        flt.add_mime_type("video/*")
        flt.add_mime_type("audio/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        return filters

    def open_file(self) -> None:
        if self.tabs.get_visible_child_name() == "yayinla":
            self.cast_browse()  # Yayınla sekmesinde: seç ama hemen başlatma
            return
        if self.selected_renderer() is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        dialog = Gtk.FileDialog(title="Oynatılacak dosya(lar)ı seç")
        dialog.set_filters(self._media_filters())
        dialog.open_multiple(self, None, self.on_files_chosen)

    def on_files_chosen(self, dialog, result) -> None:
        try:
            model = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # iptal edildi
        paths = []
        for i in range(model.get_n_items()):
            p = model.get_item(i).get_path()
            if p:
                paths.append(p)
        self.queue_local(sorted(paths, key=lambda p: natural_key(os.path.basename(p))))

    def play_local_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Videolarına göz atılacak klasörü seç")
        dialog.select_folder(self, None, self.on_local_folder)

    def on_local_folder(self, dialog, result) -> None:
        try:
            gfile = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        folder = gfile.get_path()
        if not folder:
            return
        self.remember_dir(folder)
        self.open_local(folder)

    def queue_local(self, paths: List[str]) -> None:
        if not paths:
            return
        items = [QueueItem(os.path.basename(p), path=p) for p in paths]
        self.set_queue(items, [None] * len(items))
        self.start_item(0)

    # ---- kuyruk ----
    def set_queue(self, items: List[QueueItem], rows: List[Optional[Row]]) -> None:
        self.queue = items
        self.queue_rows = rows
        self.queue_idx = -1
        self._pending = False
        self._update_cast_media()

    def update_nav_buttons(self) -> None:
        has_next = 0 <= self.queue_idx < len(self.queue) - 1 or (self._pending and len(self.queue) > 1)
        for b in (self.prev_btn, self.c_prev):
            b.set_sensitive(self.queue_idx > 0)
        for b in (self.next_btn, self.c_next):
            b.set_sensitive(has_next)

    def start_item(self, idx: int) -> None:
        if not 0 <= idx < len(self.queue):
            return
        target = self.selected_renderer()
        if target is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        item = self.queue[idx]
        if item.path and target.location == LOCAL_PLAYER_ID:
            uri, mime = item.path, guess_mime(item.path)  # bu bilgisayarda: ağa açmaya gerek yok
        elif item.path:  # yerel dosya: şimdi ağa aç
            try:
                uri = self.local.add_file(item.path)
            except Exception as exc:  # noqa: BLE001
                self.show_error(exc)
                return
            mime = guess_mime(item.path)
        else:
            uri, mime = item.uri, item.mime
        self.queue_idx = idx
        self._pending = False
        self._was_playing = False
        self._user_stopped = False
        self._last_pos = self._last_dur = 0.0
        self._play_t0 = time.monotonic()
        self.reset_playback_ui()
        self.update_nav_buttons()
        row = self.queue_rows[idx] if idx < len(self.queue_rows) else None
        if self._playing_row is not None:
            self._playing_row.set_playing(False)
            self._playing_row = None
        if row is not None and row.get_parent() is not None:  # klasör hâlâ ekranda
            row.set_playing(True)
            self._playing_row = row
        where = f"  ·  {idx + 1}/{len(self.queue)}" if len(self.queue) > 1 else ""
        self.now_title.set_text(item.title)
        self.now_sub.set_text(f"{target.name}{where}")
        mime = mime or ""
        self.now_icon.set_from_icon_name("audio-x-generic-symbolic" if mime.startswith("audio/")
                                         else "video-x-generic-symbolic")
        self._update_cast_media()
        self.cast_dev.set_text(f"Cihaz: {target.name}{where}")
        self.set_status(f"\"{item.title}\" → {target.name}")
        self.bridge.submit(self.cp.play(target.location, uri, mime, item.title),
                           on_error=self.show_error)

    def next_item(self) -> None:
        if self.queue_idx + 1 < len(self.queue):
            self.start_item(self.queue_idx + 1)
        else:
            self.set_status("Kuyruğun sonu.")

    def prev_item(self) -> None:
        if self.queue_idx > 0:
            self.start_item(self.queue_idx - 1)

    def maybe_advance(self) -> None:
        """Parça kendiliğinden bittiyse sıradakine geç (ya da Döngü açıksa baştan başla)."""
        if self._user_stopped or not self.queue:
            return
        if self._last_dur > 0 and self._last_pos < self._last_dur - END_TOLERANCE:
            return  # ortada durmuş (uzaktan kumanda vb.), bitmiş sayma
        auto = self.autonext.get_active()
        if auto and self.queue_idx + 1 < len(self.queue):
            self.start_item(self.queue_idx + 1)
        elif self.loop_switch.get_active():
            self.start_item(0 if auto else max(0, self.queue_idx))
        elif auto and len(self.queue) > 1:
            self.set_status("Liste bitti.")

    # ---- oynatma kontrolleri ----
    def send_control(self, cmd: str) -> None:
        target = self.selected_renderer()
        if target is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        if cmd == "stop":
            self._user_stopped = True
        elif cmd == "resume":
            self._user_stopped = False
        self.bridge.submit(self.cp.control(target.location, cmd), on_error=self.show_error)

    def toggle_play(self) -> None:
        """Çalıyorsa duraklatır, değilse devam ettirir; kuyruk hiç başlamadıysa ilk öğeyi başlatır."""
        if self._state in ("PLAYING", "TRANSITIONING"):
            self.send_control("pause")
        elif self._state == "PAUSED_PLAYBACK":
            self.send_control("resume")
        elif self.queue:  # durmuş ya da bitmiş: sıradaki/aynı videoyu yeniden başlat
            self.start_item(max(0, self.queue_idx))
        else:
            self.show_toast("Önce listeden oynatmak istediğin videoyu seç.")

    def _has_media(self) -> bool:
        """Seçili cihazda şu an çalan ya da duraklatılmış bir şey var mı?"""
        return self._state in ("PLAYING", "PAUSED_PLAYBACK", "TRANSITIONING")

    def _set_state(self, state: str) -> None:
        self._state = state
        playing = state in ("PLAYING", "TRANSITIONING")
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing
                                    else "media-playback-start-symbolic")
        self.play_btn.set_tooltip_text("Duraklat" if playing else "Oynat")
        if self._pending or state not in ("PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK"):
            icon, text = "media-playback-start-symbolic", "Yayınla"
        elif playing:
            icon, text = "media-playback-pause-symbolic", "Duraklat"
        else:
            icon, text = "media-playback-start-symbolic", "Devam et"
        self.cast_btn_icon.set_from_icon_name(icon)
        self.cast_btn_label.set_text(text)

    def skip(self, delta: float) -> None:
        target = self.selected_renderer()
        if target is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        if not self._has_media() or self._dur <= 0:
            self.set_status("Şu an çalan bir şey yok; önce bir video oynat.")
            return
        new = min(max(0.0, self._pos + delta), max(0.0, self._dur - 1))
        self._pos = new
        self._seek_touch = time.monotonic()
        self._show_pos(new)
        # Tuşa art arda basılırsa her basışta komut gönderme; durunca tek sarma yap.
        if self._seek_src:
            GLib.source_remove(self._seek_src)
        self._seek_src = GLib.timeout_add(500, self._do_seek, new)

    def on_seek_changed(self, _scale, _scroll, value: float) -> bool:
        """Kullanıcı çubuğu kaydırırken çalışır; son değeri 0.4 sn bekleyip gönderir."""
        self._seek_touch = time.monotonic()
        self._show_pos(value)
        if self._seek_src:
            GLib.source_remove(self._seek_src)
        self._seek_src = GLib.timeout_add(400, self._do_seek, value)
        return False

    def _do_seek(self, value: float) -> bool:
        self._seek_src = 0
        target = self.selected_renderer()
        if target is not None:
            value = max(0.0, min(value, self._dur)) if self._dur > 0 else max(0.0, value)
            self._pos = value
            self.bridge.submit(self.cp.seek(target.location, value), on_error=self.show_error)
        return False

    def _show_pos(self, value: float) -> None:
        for sc in self.seek_scales:
            sc.set_value(value)
        for lb in self.pos_labels:
            lb.set_text(fmt_time(value))

    def on_volume_changed(self, _scale, _scroll, value: float) -> bool:
        self._vol_touch = time.monotonic()
        self._vol = int(max(0, min(100, value)))
        self.vol_label.set_text(str(self._vol))
        if self._vol_src:
            GLib.source_remove(self._vol_src)
        self._vol_src = GLib.timeout_add(250, self._do_volume, value)
        return False

    def _do_volume(self, value: float) -> bool:
        self._vol_src = 0
        target = self.selected_renderer()
        if target is not None:
            self.bridge.submit(self.cp.set_volume(target.location, int(value)),
                               on_error=self.show_error)
        return False

    def on_renderer_changed(self) -> None:
        if self._sel_guard:  # yenileme sirasinda program degistiriyor, kullanici degil
            return
        self._was_playing = False  # başka cihazın durumu bu parçayı bitirmiş sayılmasın
        self._set_state("")
        self.reset_playback_ui()

    def reset_playback_ui(self) -> None:
        self._pos = self._dur = 0.0
        self._poll_n = 0  # ses değeri bir sonraki yoklamada okunsun
        for sc in self.seek_scales:
            sc.set_sensitive(False)
            sc.set_range(0, 1)
            sc.set_value(0)
        for lb in self.pos_labels + self.dur_labels:
            lb.set_text("0:00")

    def poll(self) -> bool:
        """Saniyede bir seçili renderer'ın konumunu sorar."""
        if self._discovering:
            return True  # yenileme sürerken cihaz listesi değişiyor
        target = self.selected_renderer()
        if target is not None and not self._polling:
            self._polling = True
            with_volume = self._poll_n % VOLUME_EVERY == 0
            self._poll_n += 1
            self.bridge.submit(self.cp.status(target.location, with_volume),
                               on_done=self.on_status, on_error=self.on_status_error)
        return True  # zamanlayıcı çalışmaya devam etsin

    def on_status_error(self, _exc: Exception) -> None:
        self._polling = False  # sessizce geç; cihaz o an yanıt vermemiş olabilir

    def on_status(self, st: PlayStatus) -> None:
        self._polling = False
        self._set_state(st.state)
        settling = time.monotonic() - self._play_t0 < SETTLE_SECONDS
        stopped = st.state in ("STOPPED", "NO_MEDIA_PRESENT", "")
        if settling:
            pass  # yeni parça başlıyor: cihazdan gelen eski durum bilgisini yok say
        elif stopped:
            if self._was_playing:
                self._was_playing = False
                self.maybe_advance()
            if self._dur and not self.queue_just_started():
                self.reset_playback_ui()
        else:
            if st.state in ("PLAYING", "PAUSED_PLAYBACK"):
                self._was_playing = True
                if st.position > 0:
                    self._last_pos = st.position
                if st.duration > 0:
                    self._last_dur = st.duration
            if st.duration != self._dur:
                self._dur = st.duration
                for sc in self.seek_scales:
                    sc.set_range(0, st.duration if st.duration > 0 else 1)
                    sc.set_sensitive(st.duration > 0)
                for lb in self.dur_labels:
                    lb.set_text(fmt_time(st.duration) if st.duration > 0 else "--:--")
            user_busy = self._seek_src or time.monotonic() - self._seek_touch < 2.0
            if not user_busy:
                self._pos = st.position
                self._show_pos(st.position)
        vol_busy = self._vol_src or time.monotonic() - self._vol_touch < 2.0
        if st.volume is not None and not vol_busy:
            self._vol = int(st.volume)
            self.vol_scale.set_value(st.volume)
            self.vol_label.set_text(str(self._vol))

    def queue_just_started(self) -> bool:
        """maybe_advance yeni parça başlattıysa çubuğu tekrar sıfırlamaya gerek yok."""
        return time.monotonic() - self._play_t0 < SETTLE_SECONDS

    def go_back(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.load_current()

    # ---- güncelleme ----
    def _check_update(self) -> None:
        """GitHub'da yeni sürüm var mı bakar (arka planda; ağ yoksa sessizce geçer)."""
        try:
            yeni = guncelleme.yeni_surum_var()
        except Exception:  # noqa: BLE001  (çevrimdışı olmak hata değil)
            return
        if yeni:
            to_ui(self._show_update, yeni)

    def _show_update(self, yeni: str) -> None:
        self.update_btn.set_label(f"Güncelle ({yeni})")
        self.update_btn.set_tooltip_text(f"Kurulu sürüm {__version__}. Tıklayınca {yeni} indirilir, "
                                         "program kapanıp güncellendikten sonra yeniden açılır.")
        self.update_btn.set_visible(True)

    def start_update(self) -> None:
        try:
            guncelleme.guncelleyiciyi_baslat()
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.close()

    # ---- ızgara ve küçük resimler ----
    def _update_view_btn(self) -> None:
        self.view_btn.set_icon_name("view-list-symbolic" if self.grid_mode else "view-grid-symbolic")
        self.view_btn.set_tooltip_text("Liste görünümüne geç" if self.grid_mode
                                       else "Küçük resimli ızgara görünümüne geç")

    def toggle_view(self) -> None:
        self.grid_mode = not self.grid_mode
        self.config["gorunum"] = "izgara" if self.grid_mode else "liste"
        save_config(self.config)
        self._update_view_btn()
        if self.view_stack.get_visible_child_name() == "liste":
            self.show_entries(self.entries)

    def _load_thumbs(self, tiles: List[Tile], local: bool) -> None:
        """Küçük resimleri arka planda sırayla yükler; klasör değişirse eski işler bırakılır."""
        self._thumb_gen += 1
        gen = self._thumb_gen
        for tile in tiles:
            e = tile.data
            if e.is_container or (not local and not e.thumb):
                continue
            self._thumbs.submit(self._thumb_job, gen, tile, e, local)

    def _thumb_job(self, gen: int, tile: Tile, e: Entry, local: bool) -> None:
        if gen != self._thumb_gen:
            return  # başka klasöre geçildi
        try:
            if local:
                path = kucukresim.from_local(e.id, audio=entry_icon(e) == "audio-x-generic-symbolic")
            else:
                path = kucukresim.from_url(e.thumb)
            if not path:
                return
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, Tile.W * 2, Tile.H * 2, True)
        except Exception:  # noqa: BLE001  (resim yoksa simge kalır)
            return
        to_ui(self._set_thumb, gen, tile, pix)

    def _set_thumb(self, gen: int, tile: Tile, pix) -> None:
        if gen == self._thumb_gen and tile.get_parent() is not None:
            tile.set_thumb(Gdk.Texture.new_for_pixbuf(pix))

    # ---- kısayol, sürükle-bırak, ayar ----
    def _key(self, _widget, _args, fn) -> bool:
        if isinstance(self.get_focus(), (Gtk.Text, Gtk.Editable)):
            return False  # yazı kutusunda boşluk/silme tuşu yazıya gitsin
        return fn() is not False  # False dönen işlev tuşu kullanmadı demektir

    def on_drop(self, _target, value, _x, _y) -> bool:
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        dirs = [p for p in paths if os.path.isdir(p)]
        on_cast = self.tabs.get_visible_child_name() == "yayinla"
        if dirs and on_cast and not [p for p in paths if p not in dirs]:
            self.set_cast_media(local_media_files(dirs[0]))  # klasördeki videolar listeye
            return True
        if dirs:
            self.tabs.set_visible_child_name("gez")
            self.remember_dir(dirs[0])
            self.open_local(dirs[0])
            return True
        media = [p for p in paths if guess_mime(p).split("/")[0] in ("video", "audio")]
        if not media:
            self.show_toast("Bu dosya türü oynatılamıyor. Video ya da müzik dosyası bırak.", error=True)
            return False
        if on_cast:
            self.set_cast_media(media)
            return True
        self.queue_local(sorted(media, key=lambda p: natural_key(os.path.basename(p))))
        return True

    def on_autonext(self, switch: Gtk.Switch, _pspec) -> None:
        self.config["autonext"] = switch.get_active()
        save_config(self.config)

    # ---- yardımcılar ----
    def set_status(self, text: str) -> None:
        for lb in self.status_labels:
            lb.set_text(text)

    def show_toast(self, text: str, error: bool = False) -> None:
        """Ekranın altında kısa süre görünen bildirim; hatalar daha uzun kalır."""
        self.toast_label.set_text(text)
        self.toast_icon.set_from_icon_name("dialog-warning-symbolic" if error
                                           else "dialog-information-symbolic")
        if error:
            self.toast_box.add_css_class("hata")
        else:
            self.toast_box.remove_css_class("hata")
        self.toast.set_reveal_child(True)
        if self._toast_src:
            GLib.source_remove(self._toast_src)
        self._toast_src = GLib.timeout_add_seconds(9 if error else 4, self.hide_toast)

    def hide_toast(self) -> bool:
        if self._toast_src:
            GLib.source_remove(self._toast_src)
            self._toast_src = 0
        self.toast.set_reveal_child(False)
        return False

    def show_error(self, exc: Exception) -> None:
        self.refresh_btn.set_sensitive(True)
        self.spinner.stop()
        if self.view_stack.get_visible_child_name() == "yukleniyor":
            self.view_stack.set_visible_child_name("liste" if self.entries else "karsilama")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        msg = friendly_error(exc)
        self.set_status(msg)
        self.show_toast(msg, error=True)


class App(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.dlnacast")

    def do_activate(self) -> None:
        win = self.props.active_window or MainWindow(self)
        win.present()
