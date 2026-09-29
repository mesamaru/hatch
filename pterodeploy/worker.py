"""ワーカー（ジョブの実行）のプロセスの入口。systemd の pterodeploy-worker.service から起動する。

python -m pterodeploy.worker
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import db, health
from .adapters.dns import CloudflareDns
from .adapters.panel import PterodactylPanel
from .config import ConfigError, get_settings
from .edge.publish import limits_from
from .games import load_games
from .jobs import bindings as _bindings  # noqa: F401
from .jobs import deploy as _deploy  # noqa: F401 - ジョブの種類を登録する
from .jobs import lifecycle as _lifecycle  # noqa: F401
from .jobs import publish_slots as _publish_slots  # noqa: F401
from .jobs.deps import Deps
from .jobs.engine import Engine
from .logging import setup_logging

log = logging.getLogger("pterodeploy.worker")
BEAT_SECONDS = 30


async def _beat(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(health.beat, "worker")
        try:
            await asyncio.wait_for(stop.wait(), BEAT_SECONDS)
        except TimeoutError:
            pass


async def main() -> int:
    setup_logging()
    try:
        s = get_settings()
        games = load_games()
    except ConfigError as e:
        log.error(str(e))
        return 2
    await db.open_pool()
    panel = PterodactylPanel(s.PANEL_URL, s.PANEL_APP_KEY.get_secret_value(), s.PANEL_CLIENT_KEY.get_secret_value())
    dns = CloudflareDns(s.CF_API_TOKEN.get_secret_value(), s.PD_INSTANCE)
    engine = Engine(Deps(panel=panel, dns=dns, games=games, limits=limits_from(s)))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("ワーカーを開始しました")
    try:
        await asyncio.gather(engine.run_forever(stop), _beat(stop))
    finally:
        await panel.aclose()
        await dns.aclose()
        await db.close_pool()
        log.info("ワーカーを停止しました")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
