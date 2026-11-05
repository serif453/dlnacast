#!/usr/bin/env bash
# Teşhis aracı: paylaşım ve cihaz keşfi zincirini adım adım sınar.
D="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$D" && exec "$D/.venv/bin/python" -m dlnacast.tani
