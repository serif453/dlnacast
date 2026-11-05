"""Basit ayar dosyası: ~/.config/dlnacast/config.json"""
import json
import os
import uuid

_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "dlnacast")
_PATH = os.path.join(_DIR, "config.json")


def load_config() -> dict:
    cfg = {}
    try:
        with open(_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    cfg.setdefault("uuid", str(uuid.uuid4()))
    cfg.setdefault("shares", [])
    cfg.setdefault("renderer_uuid", str(uuid.uuid4()))  # alıcı (renderer) kimliği
    cfg.setdefault("renderer", False)  # alıcı açık mı (bir sonraki açılışta hatırlanır)
    cfg.setdefault("recent", [])  # son açılan klasörler (sol listede görünür)
    cfg.setdefault("autonext", True)  # video bitince sıradakine geç
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(_DIR, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
