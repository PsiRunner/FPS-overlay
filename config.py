"""Tiny persistent settings for the overlay: text color, font family,
font size, position.

Stored as config.json next to the app (script or built .exe) so it's
easy to find, back up, or hand-edit.
"""
import json
import sys
from pathlib import Path


def _config_dir() -> Path:
    # When frozen by PyInstaller, __file__ lives in a temp extraction dir
    # that is wiped on exit - keep config.json beside the real .exe instead.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = _config_dir() / "config.json"

DEFAULTS = {
    "color": "#39FF14",   # neon green, readable over most game scenes
    "font_family": "Consolas",
    "font_size": 20,
    "pos_x": 30,
    "pos_y": 30,
    "auto_update": False, # check GitHub for a newer release at every launch
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULTS.copy()


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass
