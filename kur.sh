#!/usr/bin/env bash
# DLNA Cast kurulum ve güncelleme betiği.
#
#   İlk kurulum (terminale yapıştır):
#     bash <(curl -fsSL https://raw.githubusercontent.com/serif453/dlnacast/main/kur.sh)
#
#   Güncelleme: uygulama menüsünden "DLNA Cast Güncelle" ya da programdaki "Güncelle" düğmesi.
#
# Seçenekler:
#   --guncelle   sadece daha yeni sürüm varsa kur
#   --ac         bitince programı aç
#   --yerel DIR  GitHub yerine DIR klasöründeki kodu kur (geliştirme/deneme için)
#   --kaldir     programı kaldır (ayarlar ~/.config/dlnacast kalır)
set -eEuo pipefail

REPO="serif453/dlnacast"
HEDEF="${DLNACAST_HEDEF:-$HOME/.local/share/dlnacast}"
UYG_DIZ="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SIMGE_DIZ="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DOSYALAR="dlnacast tests requirements.txt README.md DEGISIKLIKLER.md kur.sh baslat.sh tani.sh simge.svg"

MOD=kur; AC=0; YEREL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --guncelle) MOD=guncelle ;;
        --ac) AC=1 ;;
        --yerel) YEREL="$(cd "$2" && pwd)"; shift ;;
        --kaldir) MOD=kaldir ;;
        *) echo "Bilinmeyen seçenek: $1"; exit 2 ;;
    esac
    shift
done

TMP="$(mktemp -d)"
YEDEK=""
bekle() {  # menüden açılan terminal penceresi hemen kapanmasın; $1=hata ise Enter bekler
    [ -n "${DLNACAST_MENU:-}" ] || return 0
    if [ "${1:-}" = hata ] && [ -t 0 ]; then
        read -r -p "Kapatmak için Enter'a bas..." _ || true
    else
        sleep 5
    fi
}
temizle() { rm -rf "$TMP"; }
trap temizle EXIT

hata() {
    trap - ERR
    echo
    echo "HATA: işlem tamamlanamadı."
    if [ -n "$YEDEK" ] && [ -d "$YEDEK/dlnacast" ]; then
        echo "Önceki sürüme geri dönülüyor..."
        for f in $DOSYALAR; do rm -rf "${HEDEF:?}/$f"; done
        cp -a "$YEDEK"/. "$HEDEF"/
        echo "Geri dönüldü. Program eskisi gibi çalışır."
    fi
    bekle hata
    exit 1
}
trap hata ERR

surum_oku() {  # $1 = klasör; dlnacast/__init__.py içindeki sürümü yazar
    [ -f "$1/dlnacast/__init__.py" ] || return 0
    sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1/dlnacast/__init__.py" | head -n1
}

# ---------------- kaldırma ----------------
if [ "$MOD" = kaldir ]; then
    rm -rf "$HEDEF"
    rm -f "$UYG_DIZ/dlnacast.desktop" "$UYG_DIZ/dlnacast-guncelle.desktop" "$SIMGE_DIZ/dlnacast.svg"
    echo "DLNA Cast kaldırıldı. (Ayarlar duruyor: ~/.config/dlnacast — istersen onu da sil.)"
    bekle
    exit 0
fi

# ---------------- sistem paketleri ----------------
EKSIK=""
python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null || EKSIK="$EKSIK python-gobject gtk4"
command -v mpv >/dev/null 2>&1 || EKSIK="$EKSIK mpv"
command -v curl >/dev/null 2>&1 || EKSIK="$EKSIK curl"
if [ -n "$EKSIK" ]; then
    echo "Gerekli sistem paketleri eksik:$EKSIK"
    if command -v pacman >/dev/null 2>&1 && [ -t 0 ]; then
        echo "Şimdi kuruluyor; bilgisayar şifren istenecek (vazgeçmek için Ctrl+C)."
        S=""; [ "$(id -u)" -ne 0 ] && S="sudo"
        # shellcheck disable=SC2086
        $S pacman -S --needed $EKSIK
    else
        echo "Terminalde şunu çalıştır, sonra bu betiği tekrar başlat:"
        echo "  sudo pacman -S --needed$EKSIK"
        false
    fi
fi

# ---------------- yeni kodu al ----------------
KURULU="$(surum_oku "$HEDEF")"
if [ -n "$YEREL" ]; then
    KAYNAK="$YEREL"
    YENI="$(surum_oku "$KAYNAK")"
else
    echo "GitHub'da son sürüm aranıyor..."
    if ! JSON="$(curl -fsSL --retry 3 "https://api.github.com/repos/$REPO/tags?per_page=100")"; then
        echo "GitHub'a ulaşılamadı. İnternet bağlantını kontrol edip tekrar dene."
        false
    fi
    ETIKET="$(printf '%s' "$JSON" | python3 -c '
import json, re, sys
ad = [t["name"] for t in json.load(sys.stdin) if re.fullmatch(r"v?\d+(\.\d+)*", t["name"])]
print(max(ad, key=lambda e: tuple(int(p) for p in e.lstrip("v").split("."))) if ad else "")')"
    if [ -z "$ETIKET" ]; then echo "GitHub'da sürüm bulunamadı."; false; fi
    YENI="${ETIKET#v}"
fi

if [ "$MOD" = guncelle ] && [ -n "$KURULU" ] && [ "$KURULU" = "$YENI" ]; then
    echo "Zaten en son sürüm kurulu ($KURULU)."
    if [ "$AC" = 1 ]; then setsid -f "$HEDEF/baslat.sh" >/dev/null 2>&1 || true; fi
    bekle
    exit 0
fi

if [ -z "$YEREL" ]; then
    echo "İndiriliyor: $ETIKET"
    curl -fsSL --retry 3 -o "$TMP/kod.tar.gz" "https://github.com/$REPO/archive/refs/tags/$ETIKET.tar.gz"
    mkdir "$TMP/kod"
    tar -xzf "$TMP/kod.tar.gz" -C "$TMP/kod" --strip-components=1
    KAYNAK="$TMP/kod"
fi
[ -f "$KAYNAK/dlnacast/__main__.py" ] || { echo "İndirilen dosya bozuk görünüyor."; false; }

# ---------------- programı kapat, yedek al, kopyala ----------------
if pkill -f "$HEDEF/.venv/bin/python -m dlnacast" 2>/dev/null; then sleep 1; fi
mkdir -p "$HEDEF"
if [ -n "$KURULU" ]; then
    YEDEK="$HEDEF/yedekler/$KURULU-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$YEDEK"
    for f in $DOSYALAR; do
        if [ -e "$HEDEF/$f" ]; then cp -a "$HEDEF/$f" "$YEDEK/"; fi
    done
    # son 3 yedeği tut
    ls -dt "$HEDEF"/yedekler/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf
fi
for f in $DOSYALAR; do rm -rf "${HEDEF:?}/$f"; done
for f in $DOSYALAR; do
    if [ -e "$KAYNAK/$f" ]; then cp -a "$KAYNAK/$f" "$HEDEF/"; fi
done
chmod +x "$HEDEF/kur.sh" "$HEDEF/baslat.sh" "$HEDEF/tani.sh"

# ---------------- Python ortamı (.venv) ----------------
if ! "$HEDEF/.venv/bin/python" -c "pass" 2>/dev/null; then  # yok ya da Python sürümü değişmiş
    echo "Python ortamı hazırlanıyor (ilk seferde birkaç dakika sürebilir)..."
    rm -rf "$HEDEF/.venv"
    python3 -m venv --system-site-packages "$HEDEF/.venv"
fi
REQ_OZET="$(sha256sum "$HEDEF/requirements.txt" | cut -d' ' -f1)"
if [ "$(cat "$HEDEF/.venv/.req-ozet" 2>/dev/null)" != "$REQ_OZET" ]; then
    echo "Python paketleri kuruluyor..."
    "$HEDEF/.venv/bin/pip" install -q --disable-pip-version-check -r "$HEDEF/requirements.txt"
    echo "$REQ_OZET" > "$HEDEF/.venv/.req-ozet"
fi
# kurulum çalışıyor mu? (arayüz açılmadan modülleri yükler)
(cd "$HEDEF" && .venv/bin/python -c "import dlnacast.controlpoint, dlnacast.renderer, dlnacast.guncelleme")

# ---------------- menü kısayolları ----------------
mkdir -p "$UYG_DIZ" "$SIMGE_DIZ"
cp "$HEDEF/simge.svg" "$SIMGE_DIZ/dlnacast.svg"
cat > "$UYG_DIZ/dlnacast.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DLNA Cast
Comment=Videoları TV'ye, Chromecast'e ve bu bilgisayara gönder
Exec=$HEDEF/baslat.sh
Icon=dlnacast
Terminal=false
Categories=AudioVideo;Video;Player;
StartupWMClass=io.github.dlnacast
EOF
cat > "$UYG_DIZ/dlnacast-guncelle.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DLNA Cast Güncelle
Comment=DLNA Cast'in en son sürümünü GitHub'dan indirip kurar
Exec=env DLNACAST_MENU=1 bash $HEDEF/kur.sh --guncelle --ac
Icon=dlnacast
Terminal=true
Categories=AudioVideo;
EOF
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q "$UYG_DIZ" || true; fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then kbuildsycoca6 >/dev/null 2>&1 || true; fi

trap - ERR
echo
if [ -n "$KURULU" ] && [ "$KURULU" != "$YENI" ]; then
    echo "Güncellendi: $KURULU -> $YENI"
    if [ -f "$HEDEF/DEGISIKLIKLER.md" ]; then
        echo; echo "Bu sürümde neler var:"
        awk '/^## /{n++} n==1' "$HEDEF/DEGISIKLIKLER.md" | sed '1d'
    fi
else
    echo "DLNA Cast $YENI kuruldu."
    echo "Uygulama menüsünde 'DLNA Cast' olarak bulabilirsin."
fi
if [ "$AC" = 1 ]; then setsid -f "$HEDEF/baslat.sh" >/dev/null 2>&1 || true; fi
bekle
