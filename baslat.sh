#!/usr/bin/env bash
# DLNA Cast'i açar. Hata olursa kayıt: ~/.cache/dlnacast/son-calisma.log
D="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LOG="${XDG_CACHE_HOME:-$HOME/.cache}/dlnacast/son-calisma.log"
mkdir -p "$(dirname "$LOG")"

uyar() {
    echo "$1" >&2
    if command -v notify-send >/dev/null 2>&1; then notify-send -i dlnacast "DLNA Cast" "$1"
    elif command -v kdialog >/dev/null 2>&1; then kdialog --title "DLNA Cast" --error "$1"
    fi
}

if ! "$D/.venv/bin/python" -c pass 2>/dev/null; then
    uyar "Kurulum eksik ya da bozuk. Uygulama menüsünden 'DLNA Cast Güncelle'yi aç."
    exit 1
fi
cd "$D" || exit 1
"$D/.venv/bin/python" -m dlnacast "$@" 2> >(tee "$LOG" >&2)
KOD=$?
if [ "$KOD" -ne 0 ]; then
    uyar "Program bir hatayla kapandı. Ayrıntı: $LOG"
fi
exit "$KOD"
