"""GTK4 arayüzü: solda kaynaklar, sağda klasör içeriği, altta oynatıcı paneli."""
import os
import re
import socket
import threading
import asyncio
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from . import guncelleme  # noqa: E402
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


DEVICE_LOOK = {  # tür -> (simge, açıklama)
    "player": ("computer-symbolic", "Bu bilgisayarda mpv ile oynatır"),
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


class SharesWindow(Gtk.Window):
    """Paylaşılan klasörleri gösterir; ekleme/kaldırma yapar."""

    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(title="Paylaşılan klasörler", transient_for=parent, modal=True)
        self.parent_win = parent
        self.set_default_size(520, 360)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        box.append(Gtk.Label(label="Paylaşılan klasörler", xalign=0, css_classes=["buyuk-baslik"]))
        info = Gtk.Label(xalign=0, wrap=True, css_classes=["soluk"],
                         label="Buraya eklediğin klasörler ağdaki TV, telefon ve diğer cihazlarda görünür.")
        box.append(info)
        self.listbox = Gtk.ListBox(vexpand=True, selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.set_placeholder(placeholder("Henüz paylaşılan klasör yok."))
        box.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True))
        add = action_button("list-add-symbolic", "Klasör ekle…", "Paylaşılacak klasörü seç",
                            self.add_folder)
        add.add_css_class("suggested-action")
        add.set_halign(Gtk.Align.START)
        box.append(add)
        self.set_child(box)
        self.fill()

    def fill(self) -> None:
        clear(self.listbox)
        for path in self.parent_win.config["shares"]:
            row = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            row.append(Gtk.Image(icon_name="folder-symbolic"))
            row.append(Gtk.Label(label=path, xalign=0, hexpand=True, ellipsize=3))
            btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Paylaşımdan kaldır",
                             css_classes=["flat"])
            btn.connect("clicked", lambda _b, p=path: self.remove(p))
            row.append(btn)
            self.listbox.append(row)

    def add_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Paylaşılacak klasörü seç")
        dialog.select_folder(self, None, self.on_folder)

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
        self.set_default_size(1120, 720)
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

        # Oynatma durumu
        self._polling = False
        self._poll_n = 0
        self._pos = 0.0
        self._dur = 0.0
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

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # ---- başlık çubuğu: hedef cihaz, yenile, menü ----
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        self.renderer_switch = Gtk.Switch(
            valign=Gtk.Align.CENTER,
            tooltip_text="Açıkken telefon ve bilgisayarlar bu bilgisayarı TV gibi görür; "
                         "gönderdikleri video/müzik burada oynar (DLNA renderer)")
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        alici = Gtk.Box(spacing=12)
        alici_txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        alici_txt.append(Gtk.Label(label="Alıcı modu", xalign=0, css_classes=["calan-baslik"]))
        alici_txt.append(Gtk.Label(label="Telefondan bu bilgisayara video gönderilebilir.",
                                   xalign=0, wrap=True, max_width_chars=34,
                                   css_classes=["soluk", "kucuk"]))
        alici.append(alici_txt)
        alici.append(self.renderer_switch)
        menu.append(alici)
        menu.append(Gtk.Separator())
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Menü")
        self.autonext = Gtk.Switch(valign=Gtk.Align.CENTER, active=True,
                                   tooltip_text="Bir video bitince klasördeki sıradaki video başlar")
        sira = Gtk.Box(spacing=12)
        sira.append(Gtk.Label(label="Bitince sıradakine geç", xalign=0, hexpand=True,
                              css_classes=["calan-baslik"]))
        sira.append(self.autonext)
        menu.append(sira)
        menu.append(Gtk.Separator())
        menu.append(action_button("folder-publicshare-symbolic", "Klasörleri ağda paylaş…",
                                  "Klasörleri ağdaki TV ve telefonlara aç",
                                  self.open_shares))
        menu.append(Gtk.Separator())
        menu.append(Gtk.Label(label="Kısayollar:  Boşluk = oynat/duraklat,  ← → = 10 sn,\n"
                                    "Backspace = geri,  Ctrl+O = dosya aç,  F5 = yenile.\n"
                                    "Dosya ya da klasörü pencereye sürükleyip bırakabilirsin.",
                              xalign=0, css_classes=["soluk", "kucuk"]))
        menu.append(Gtk.Label(label=f"DLNA Cast {__version__}", xalign=0, css_classes=["soluk", "kucuk"]))
        self.menu_btn.set_popover(Gtk.Popover(child=menu))
        header.pack_end(self.menu_btn)

        self.update_btn = Gtk.Button(label="Güncelle", visible=False, css_classes=["suggested-action"])
        self.update_btn.connect("clicked", lambda *_: self.start_update())
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Cihazları yeniden ara (F5)")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_btn)
        self.search_spinner = Gtk.Spinner(visible=False, tooltip_text="Ağdaki cihazlar aranıyor…")
        header.pack_end(self.search_spinner)
        self.picker = DevicePicker(self.on_renderer_changed)
        header.set_title_widget(self.picker)
        header.pack_end(self.update_btn)
        self.alici_label = Gtk.Label(label="● Alıcı açık", visible=False, css_classes=["alici-isareti"],
                                     tooltip_text="Ağdaki cihazlar bu bilgisayara video gönderebilir")
        header.pack_start(self.alici_label)

        # ---- sol panel: kaynaklar ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["kenar"])
        side.set_size_request(250, -1)
        acts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=12, margin_start=10, margin_end=10)
        for btn in (action_button("document-open-symbolic", "Dosya aç",
                                  "Bilgisayardan dosya(lar) seç ve sırayla oynat", self.open_file),
                    action_button("folder-open-symbolic", "Klasör aç",
                                  "Bilgisayardan klasör aç, içindeki videolar listelensin",
                                  self.play_local_folder)):
            btn.add_css_class("flat")
            acts.append(btn)
        side.append(acts)
        side.append(section_title("Bu bilgisayar"))
        self.local_list = Gtk.ListBox()
        self.local_list.set_placeholder(placeholder("Açtığın ve paylaştığın klasörler burada görünür."))
        self.local_list.connect("row-activated", self.on_server_activated)
        side.append(self.local_list)
        side.append(section_title("Ağdaki sunucular"))
        self.dev_list = Gtk.ListBox()
        self.dev_list.set_placeholder(placeholder("Ağda medya sunucusu bulunamadı."))
        self.dev_list.connect("row-activated", self.on_server_activated)
        side.append(self.dev_list)
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
        topbar.append(self.play_all_btn)

        self.item_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)  # çalan satır ayrıca vurgulanır
        self.item_list.set_placeholder(placeholder("Bu klasörde video ya da müzik yok."))
        self.item_list.connect("row-activated", self.on_entry_activated)
        liste = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["icerik"])
        liste.append(topbar)
        liste.append(Gtk.ScrolledWindow(child=self.item_list, vexpand=True,
                                        hscrollbar_policy=Gtk.PolicyType.NEVER))

        welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        welcome.append(Gtk.Image(icon_name="video-display-symbolic", pixel_size=72,
                                 css_classes=["soluk"]))
        welcome.append(Gtk.Label(label="Ne izlemek istersin?", css_classes=["karsilama-baslik"]))
        steps = Gtk.Grid(column_spacing=10, row_spacing=8, halign=Gtk.Align.CENTER, margin_top=4)
        for i, text in enumerate(("Aşağıdan bir dosya ya da klasör aç, veya soldan bir sunucu seç.",
                                  "Üstteki \"Şurada oynat\" düğmesinden cihazı (TV) seç.",
                                  "Listede bir videoya tıkla; hemen oynamaya başlar.")):
            steps.attach(Gtk.Label(label=str(i + 1), css_classes=["adim"]), 0, i, 1, 1)
            steps.attach(Gtk.Label(label=text, xalign=0, css_classes=["soluk"]), 1, i, 1, 1)
        welcome.append(steps)
        wbtns = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=8)
        wfile = action_button("document-open-symbolic", "Dosya aç", "Bilgisayardan dosya seç",
                              self.open_file)
        wfile.add_css_class("suggested-action")
        wbtns.append(wfile)
        wbtns.append(action_button("folder-open-symbolic", "Klasör aç", "Bilgisayardan klasör aç",
                                   self.play_local_folder))
        welcome.append(wbtns)
        welcome.append(Gtk.Label(label="İpucu: dosyaları bu pencereye sürükleyip bırakabilirsin.",
                                 margin_top=6, css_classes=["soluk", "kucuk"]))

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
        overlay = Gtk.Overlay(child=self.view_stack)
        overlay.add_overlay(self.toast)
        paned.set_end_child(overlay)

        # ---- alt panel: oynatıcı ----
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
        self.vol_scale.set_value(50)
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

        self.pos_label = Gtk.Label(label="0:00", width_chars=7, xalign=1, css_classes=["soluk", "kucuk"])
        self.dur_label = Gtk.Label(label="0:00", width_chars=7, xalign=0, css_classes=["soluk", "kucuk"])
        self.seek_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.seek_scale.set_draw_value(False)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.set_sensitive(False)
        self.seek_scale.connect("change-value", self.on_seek_changed)
        seekbox = Gtk.Box(spacing=8)
        seekbox.append(self.pos_label)
        seekbox.append(self.seek_scale)
        seekbox.append(self.dur_label)

        self.status = Gtk.Label(xalign=0, ellipsize=3, css_classes=["soluk", "kucuk"])
        player = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["oynatici"])
        for w, top, bottom in ((row1, 10, 0), (seekbox, 0, 0), (self.status, 0, 8)):
            w.set_margin_start(16)
            w.set_margin_end(16)
            w.set_margin_top(top)
            w.set_margin_bottom(bottom)
            player.append(w)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(paned)
        box.append(player)
        self.set_child(box)

        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for trigger, fn in (("space", self.toggle_play), ("Left", lambda: self.skip(-SKIP_SECONDS)),
                            ("Right", lambda: self.skip(SKIP_SECONDS)), ("BackSpace", self.go_back),
                            ("<Control>o", self.open_file), ("F5", self.refresh)):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                           action=Gtk.CallbackAction.new(self._key, fn)))
        self.add_controller(keys)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

        self.autonext.set_active(self.config.get("autonext", True))
        self.autonext.connect("notify::active", self.on_autonext)
        self.mserver = MediaServer(f"DLNA Cast ({socket.gethostname()})", self.config["uuid"])
        self.cp.local_media = self.mserver
        self.renderer = MediaRenderer(f"DLNA Cast Alıcı ({socket.gethostname()})",
                                      self.config["renderer_uuid"], self.bridge.loop)
        self.renderer.on_message = lambda text: to_ui(self.set_status, text)
        self.renderer_switch.connect("notify::active", self.on_renderer_switch)
        self.connect("close-request", self.on_close)
        self.shares_changed(save=False)
        if self.config["renderer"]:  # son seferde açıksa yine aç
            self.renderer_switch.set_active(True)
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self.poll)
        threading.Thread(target=self._check_update, daemon=True).start()

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
            self.renderer.stop()
            self.show_toast("Alıcı kapatıldı.")
        self.alici_label.set_visible(want)
        self.config["renderer"] = want
        save_config(self.config)

    def on_close(self, *_args) -> bool:
        self.renderer.stop()
        self.mserver.stop()
        self.cp.player.close()
        self.cp.cast.close()
        return False

    def open_shares(self) -> None:
        self.menu_btn.popdown()
        SharesWindow(self).present()

    def shares_changed(self, save: bool = True) -> None:
        if save:
            save_config(self.config)
        self.mserver.set_shares(self.config["shares"])
        if save:
            self.sync_local_rows()
        if self.mserver.shares and not self.mserver.running:
            try:
                self.mserver.start()
            except Exception as exc:  # noqa: BLE001
                self.show_error(exc)
                return
        if save:
            self.set_status(f"{len(self.mserver.shares)} klasör ağda paylaşılıyor.")

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
        self.refresh_btn.set_sensitive(True)
        self.search_spinner.stop()
        self.search_spinner.set_visible(False)
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
        self.entries = entries
        self.entry_rows = []
        self._playing_row = None
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

    def update_nav_buttons(self) -> None:
        self.prev_btn.set_sensitive(self.queue_idx > 0)
        self.next_btn.set_sensitive(0 <= self.queue_idx < len(self.queue) - 1)

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
        """Parça kendiliğinden bittiyse sıradakine geç."""
        if self._user_stopped or not self.autonext.get_active():
            return
        if self._last_dur > 0 and self._last_pos < self._last_dur - END_TOLERANCE:
            return  # ortada durmuş (uzaktan kumanda vb.), bitmiş sayma
        if self.queue_idx + 1 >= len(self.queue):
            if len(self.queue) > 1:
                self.set_status("Kuyruk bitti.")
            return
        self.start_item(self.queue_idx + 1)

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
        elif self._state in ("STOPPED", "NO_MEDIA_PRESENT", "") and self.queue and self.queue_idx < 0:
            self.start_item(0)
        elif self._state in ("STOPPED", "NO_MEDIA_PRESENT", "") and self.queue_idx >= 0 and self._user_stopped:
            self.start_item(self.queue_idx)
        else:
            self.send_control("resume")

    def _set_state(self, state: str) -> None:
        self._state = state
        playing = state in ("PLAYING", "TRANSITIONING")
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing
                                    else "media-playback-start-symbolic")
        self.play_btn.set_tooltip_text("Duraklat" if playing else "Oynat")

    def skip(self, delta: float) -> None:
        target = self.selected_renderer()
        if target is None:
            self.set_status("Önce üstteki \"Şurada oynat\" düğmesinden bir cihaz seç.")
            return
        new = max(0.0, self._pos + delta)
        if self._dur > 0:
            new = min(new, max(0.0, self._dur - 1))
        self._pos = new
        self._seek_touch = time.monotonic()
        self.seek_scale.set_value(new)
        self.pos_label.set_text(fmt_time(new))
        self.bridge.submit(self.cp.seek(target.location, new), on_error=self.show_error)

    def on_seek_changed(self, _scale, _scroll, value: float) -> bool:
        """Kullanıcı çubuğu kaydırırken çalışır; son değeri 0.4 sn bekleyip gönderir."""
        self._seek_touch = time.monotonic()
        self.pos_label.set_text(fmt_time(value))
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

    def on_volume_changed(self, _scale, _scroll, value: float) -> bool:
        self._vol_touch = time.monotonic()
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
        self.seek_scale.set_sensitive(False)
        self.seek_scale.set_range(0, 1)
        self.seek_scale.set_value(0)
        self.pos_label.set_text("0:00")
        self.dur_label.set_text("0:00")

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
                self.seek_scale.set_range(0, st.duration if st.duration > 0 else 1)
                self.dur_label.set_text(fmt_time(st.duration) if st.duration > 0 else "--:--")
                self.seek_scale.set_sensitive(st.duration > 0)
            user_busy = self._seek_src or time.monotonic() - self._seek_touch < 2.0
            if not user_busy:
                self._pos = st.position
                self.seek_scale.set_value(st.position)
                self.pos_label.set_text(fmt_time(st.position))
        vol_busy = self._vol_src or time.monotonic() - self._vol_touch < 2.0
        if st.volume is not None and not vol_busy:
            self.vol_scale.set_value(st.volume)

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

    # ---- kısayol, sürükle-bırak, ayar ----
    def _key(self, _widget, _args, fn) -> bool:
        fn()
        return True

    def on_drop(self, _target, value, _x, _y) -> bool:
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        dirs = [p for p in paths if os.path.isdir(p)]
        if dirs:
            self.remember_dir(dirs[0])
            self.open_local(dirs[0])
            return True
        media = [p for p in paths if guess_mime(p).split("/")[0] in ("video", "audio")]
        if not media:
            self.show_toast("Bu dosya türü oynatılamıyor. Video ya da müzik dosyası bırak.", error=True)
            return False
        self.queue_local(sorted(media, key=lambda p: natural_key(os.path.basename(p))))
        return True

    def on_autonext(self, switch: Gtk.Switch, _pspec) -> None:
        self.config["autonext"] = switch.get_active()
        save_config(self.config)

    # ---- yardımcılar ----
    def set_status(self, text: str) -> None:
        self.status.set_text(text)

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
