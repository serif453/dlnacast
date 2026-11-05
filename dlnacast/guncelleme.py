"""GitHub'daki son sürümü bulur ve güncelleyiciyi başlatır (sadece standart kütüphane)."""
import json
import os
import re
import shutil
import subprocess
import urllib.request
from typing import Optional, Tuple

from . import REPO, __version__

UYGULAMA_DIZINI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def surum_tuple(text: str) -> Tuple[int, ...]:
    """"v0.10.2" -> (0, 10, 2). Sayı olmayan etiketler için boş tuple."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def en_yeni(etiketler) -> Optional[str]:
    """Etiket adlarından en yüksek sürümü döndürür (yoksa None)."""
    gecerli = [e for e in etiketler if surum_tuple(e)]
    return max(gecerli, key=surum_tuple) if gecerli else None


def son_surum(timeout: float = 8.0) -> Optional[str]:
    """GitHub'daki en yeni sürüm etiketini döndürür ("v0.3.0" gibi). Ağ hatasında istisna atar."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/tags?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "dlnacast-guncelleme"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return en_yeni(t.get("name", "") for t in data)


def yeni_surum_var() -> Optional[str]:
    """Kurulu sürümden yeni bir sürüm varsa onun adını döndürür."""
    son = son_surum()
    if son and surum_tuple(son) > surum_tuple(__version__):
        return son
    return None


def guncelleyiciyi_baslat() -> None:
    """Güncelleyiciyi bir terminal penceresinde açar; bitince program yeniden açılır."""
    betik = os.path.join(UYGULAMA_DIZINI, "kur.sh")
    komut = ["bash", betik, "--guncelle", "--ac"]
    env = dict(os.environ, DLNACAST_MENU="1")  # pencere sonucu görebilmen için hemen kapanmaz
    for term in (["konsole", "-e"], ["gnome-terminal", "--"], ["xfce4-terminal", "-x"],
                 ["kitty"], ["alacritty", "-e"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            subprocess.Popen(term + komut, start_new_session=True, env=env)
            return
    # terminal bulunamadı: arka planda çalıştır (sudo gerekmiyorsa yine de güncellenir)
    subprocess.Popen(komut, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
