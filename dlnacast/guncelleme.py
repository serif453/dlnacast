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
    """"0.10.2" ya da "v0.10.2" -> (0, 10, 2). Geçersizse boş tuple."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def surum_metinden(init_py: str) -> Optional[str]:
    """__init__.py içeriğinden __version__ değerini çıkarır."""
    m = re.search(r'^__version__ = "([^"]+)"', init_py, re.M)
    return m.group(1) if m else None


def _getir(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "dlnacast-guncelleme"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def son_surum(timeout: float = 8.0) -> Optional[str]:
    """GitHub'daki main dalının sürümünü döndürür ("0.3.0" gibi). Ağ hatasında istisna atar."""
    sha = json.loads(_getir(f"https://api.github.com/repos/{REPO}/commits/main", timeout))["sha"]
    init = _getir(f"https://raw.githubusercontent.com/{REPO}/{sha}/dlnacast/__init__.py", timeout)
    return surum_metinden(init.decode("utf-8"))


def yeni_surum_var() -> Optional[str]:
    """Kurulu sürümden yeni bir sürüm varsa onun numarasını döndürür."""
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
