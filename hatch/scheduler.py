"""定期処理のプロセス（hatch-scheduler.service）。現在は「ゴミ箱の期限切れの完全削除」のみ。

期限・整合性チェック・監視の取得は T17〜T20 で追加する。1分ごとに各処理を実行する。
    python -m hatch.scheduler
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from psycopg import AsyncConnection

from . import db, health
from .config import ConfigError, get_settings
from .jobs import lifecycle as _lifecycle  # noqa: F401 - purge の登録
from .jobs.engine import enqueue
from .logging import setup_logging

log = logging.getLogger("hatch.scheduler")
TICK_SECONDS = 60


async def enqueue_due_purges(conn: AsyncConnection) -> list[int]:
    """ゴミ箱の期限（purge_after）を過ぎたサーバーの完全削除を登録する。同じサーバーに二重に登録しない。"""
    cur = await conn.execute(
        """SELECT s.id::text AS id FROM servers s
           WHERE s.status = 'trashed' AND s.purge_after <= now()
             AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.server_id = s.id AND j.kind = 'purge'
                             AND j.status IN ('queued','running'))
           ORDER BY s.purge_after LIMIT 50"""
    )
    jobs = []
    for row in await cur.fetchall():
        jobs.append(
            await enqueue(conn, "purge", via="system", server_id=row["id"], idempotency_key=f"auto-purge:{row['id']}")
        )
    return jobs


async def tick() -> None:
    async with db.transaction() as conn:
        n = await enqueue_due_purges(conn)
    if n:
        log.info("期限切れのゴミ箱 %d 件の完全削除を登録しました", len(n))


async def main() -> int:
    setup_logging()
    try:
        get_settings()
    except ConfigError as e:
        log.error(str(e))
        return 2
    await db.open_pool(max_size=3)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("定期処理を開始しました")
    while not stop.is_set():
        try:
            await tick()
        except Exception:
            log.exception("定期処理でエラー")
        await asyncio.to_thread(health.beat, "scheduler")
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
        except TimeoutError:
            pass
    await db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
