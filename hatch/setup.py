"""初期設定（画面のウィザード）の状態と保存。docs/IMPLEMENTATION.md 8.3。

- 初期設定コード：PD_DATA_DIR/setup-code。インストーラーか、初期設定が必要なときに API が作る。
  知っている人だけが初期設定画面を使える（パネルを先に開いた第三者に乗っ取られないように）。
- 画面で入れた値：PD_DATA_DIR/setup.env（systemd が hatch.env の後に読むので、こちらが優先）。
- 保存後の再起動：PD_DATA_DIR/restart-request を書くと、hatch-reload.path が hatch.target を再起動する。
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import secrets
import signal
import time
from pathlib import Path

from psycopg import AsyncConnection

from .config import get_core_settings, setup_problems
from .repo import setup as setup_repo

STARTED_AT = time.time()
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 読み間違えやすい 0・O・1・I を除く

# 画面から設定できる値（これ以外は setup.env に書かない）
PUBLIC_KEYS = (
    "PD_PUBLIC_URL",
    "PD_INTERNAL_URL",
    "PANEL_URL",
    "PANEL_PUBLIC_URL",
    "KUMA_URL",
    "KUMA_USERNAME",
    "DISCORD_CLIENT_ID",
    "DISCORD_GUILD_ID",
    "DISCORD_CHANNEL_ANNOUNCE",
    "DISCORD_CHANNEL_OPS",
)
SECRET_KEYS = (
    "PANEL_APP_KEY",
    "PANEL_CLIENT_KEY",
    "CF_API_TOKEN",
    "KUMA_PASSWORD",
    "KUMA_METRICS_KEY",
    "DISCORD_BOT_TOKEN",
    "DISCORD_CLIENT_SECRET",
)
SETUP_KEYS = PUBLIC_KEYS + SECRET_KEYS
# systemd の EnvironmentFile にそのまま書ける値だけを受け付ける
SAFE_VALUE = re.compile(r"^[^\s\"'`$\\#;]*$")


def _dir() -> Path:
    return get_core_settings().PD_DATA_DIR


def code_path() -> Path:
    return _dir() / "setup-code"


def env_path() -> Path:
    return _dir() / "setup.env"


def restart_path() -> Path:
    return _dir() / "restart-request"


def _normalize(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def ensure_code() -> str:
    p = code_path()
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(code + "\n")
    return code


def code_matches(given: str) -> bool:
    want = _normalize(ensure_code())
    return bool(given) and hmac.compare_digest(_normalize(given).encode(), want.encode())


def discard_code() -> None:
    code_path().unlink(missing_ok=True)


def current(key: str) -> str:
    return os.environ.get(key, "").strip()


def read_env_file() -> dict[str, str]:
    p = env_path()
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k in SETUP_KEYS:
            out[k] = v
    return out


def write_env_file(values: dict[str, str]) -> None:
    """setup.env を置き換える（一時ファイルに書いてから入れ替える）。"""
    merged = {**read_env_file(), **{k: v for k, v in values.items() if k in SETUP_KEYS and v}}
    body = "# 初期設定画面で保存した値（/etc/hatch/hatch.env より優先されます）\n"
    body += "".join(f"{k}={merged[k]}\n" for k in SETUP_KEYS if k in merged)
    tmp = env_path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, env_path())


def request_restart() -> None:
    restart_path().write_text(f"{time.time()}\n", encoding="utf-8")


def restarting() -> bool:
    """再起動を頼んだが、まだこのプロセスが入れ替わっていない。"""
    p = restart_path()
    return p.exists() and p.stat().st_mtime > STARTED_AT


async def idle_until_stopped() -> None:
    """設定が揃うまで何もせずに待つ（worker・scheduler 用。初期設定の保存後に systemd が再起動する）。"""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()


async def needed(conn: AsyncConnection) -> bool:
    return bool(setup_problems()) or not await setup_repo.completed(conn)
