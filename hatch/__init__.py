"""Hatch - Pterodactyl 自動デプロイ基盤"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    __version__ = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    __version__ = "0.0.0"
