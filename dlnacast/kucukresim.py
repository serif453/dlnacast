"""Küçük resimler: ağdan indirilir (albüm kapağı) ya da yerel dosyadan ffmpeg ile kare alınır.

Sonuçlar ~/.cache/dlnacast/kucukresim klasöründe saklanır; KDE/Dolphin'in daha önce
ürettiği küçük resimler (~/.cache/thumbnails) varsa doğrudan onlar kullanılır.
Bu modüldeki fonksiyonlar engelleyicidir; arayüz bunları arka plan thread'inde çağırır.
"""
import hashlib
import os
import shutil
import subprocess
import urllib.request
from typing import Optional
from urllib.parse import quote

MAX_BYTES = 8 * 1024 * 1024  # ağdan en fazla bu kadar indir
CACHE_ROOT = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
CACHE = os.path.join(CACHE_ROOT, "dlnacast", "kucukresim")


def _cache_file(key: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".jpg")


def _missing_marker(out: str) -> str:
    return out + ".yok"  # küçük resim çıkarılamadı; her açılışta yeniden denenmesin


def from_url(url: str, timeout: float = 8.0) -> Optional[str]:
    """Ağdaki resmi indirip önbelleğe yazar; dosya yolunu döndürür."""
    if not url.lower().startswith(("http://", "https://")):
        return None
    out = _cache_file(url)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    req = urllib.request.Request(url, headers={"User-Agent": "DLNACast"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        return None
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, out)
    return out


def freedesktop(path: str) -> Optional[str]:
    """KDE/GNOME'un daha önce ürettiği küçük resim (güncelse)."""
    uri = "file://" + quote(os.path.abspath(path))
    name = hashlib.md5(uri.encode("utf-8")).hexdigest() + ".png"
    base = os.path.join(CACHE_ROOT, "thumbnails")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    for size in ("large", "x-large", "normal", "xx-large"):
        p = os.path.join(base, size, name)
        if os.path.isfile(p) and os.path.getmtime(p) >= mtime:
            return p
    return None


def from_local(path: str, audio: bool = False) -> Optional[str]:
    """Yerel video için bir kare, müzik için gömülü kapak resmi; yoksa None."""
    found = freedesktop(path)
    if found:
        return found
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return None
    out = _cache_file(f"file:{path}:{mtime}")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    if os.path.exists(_missing_marker(out)):
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    attempts = [["-i", path, "-an", "-map", "0:v:0"]] if audio else \
        [["-ss", "30", "-i", path, "-an"], ["-ss", "0", "-i", path, "-an"]]  # kısa videolarda baştan
    for args in attempts:
        try:
            subprocess.run([ffmpeg, "-v", "error", "-y", *args, "-frames:v", "1",
                            "-vf", "scale=320:-2", out],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=25, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    open(_missing_marker(out), "w").close()
    return None
