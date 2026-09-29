"""DB の状態から edge 設定の新しい版を作る。

ジョブの手順（デプロイ・ゴミ箱など）はサーバーの状態を変えた後に publish() を呼び、
返ってきた版を両方の edge が適用するまで待つ（wait_applied）。
"""

from __future__ import annotations

import asyncio
import time

from psycopg import AsyncConnection

from .. import db
from ..config import Settings
from ..games import Game
from ..repo import edges as repo
from .render import Limits, Target, content_hash, render_haproxy, render_nft

PUBLISH_LOCK = 7_420_119


def limits_from(settings: Settings) -> Limits:
    return Limits(
        per_ip_conn=settings.HAPROXY_PER_IP_CONN,
        per_ip_rate=settings.HAPROXY_PER_IP_RATE,
        max_conn=settings.HAPROXY_MAX_CONN,
        udp_per_ip_pps=settings.UDP_PER_IP_PPS,
    )


async def publish(conn: AsyncConnection, games: dict[str, Game], limits: Limits) -> int:
    """新しい版を作って版番号を返す。内容が前回と同じなら前回の版番号を返す。

    同時に2つ作られないように、トランザクション単位のアドバイザリーロックを取る。
    """
    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (PUBLISH_LOCK,))
    targets: list[Target] = []
    for r in await repo.exposed_targets(conn):
        g = games.get(r["game"])
        if g is None:  # 定義の消えたゲームは公開しない（整合性チェックで検出する）
            continue
        targets.append(Target(r["port"], r["backend_ip"], g.protocol, g.proxy_protocol, r["name"]))
    cfg = render_haproxy(targets, limits)
    nft = render_nft(targets, limits)
    h = content_hash(cfg, nft)
    latest = await repo.latest_version(conn)
    if latest and latest["content_hash"] == h:
        return latest["version"]
    return await repo.insert_version(conn, cfg, nft, h)


async def wait_applied(version: int, within: float = 60, interval: float = 2) -> bool:
    """使用中の edge と応答のある edge が version 以上を適用するまで待つ。時間切れなら False。"""
    deadline = time.monotonic() + within
    while True:
        async with db.transaction() as conn:
            if await repo.applied_by_live(conn, version):
                return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)
