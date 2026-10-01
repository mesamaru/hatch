"""edge と edge 設定の版。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def exposed_targets(conn: AsyncConnection) -> list[dict[str, Any]]:
    """edge で公開するサーバー（ポート順）。"""
    cur = await conn.execute(
        """
        SELECT s.name, s.game, sl.port, host(n.tailscale_ip) AS backend_ip
        FROM servers s
        JOIN slots sl ON sl.server_id = s.id AND sl.status = 'assigned'
        JOIN nodes n ON n.id = s.node_id
        WHERE s.exposed AND s.status IN ('provisioning', 'running', 'stopped', 'maintenance')
        ORDER BY sl.port
        """
    )
    return list(await cur.fetchall())


async def latest_version(conn: AsyncConnection) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT version, haproxy_cfg, nft_rules, content_hash FROM edge_config_versions ORDER BY version DESC LIMIT 1"
    )
    return await cur.fetchone()


async def insert_version(conn: AsyncConnection, haproxy_cfg: str, nft_rules: str, content_hash: str) -> int:
    cur = await conn.execute(
        "INSERT INTO edge_config_versions (haproxy_cfg, nft_rules, content_hash) VALUES (%s, %s, %s) RETURNING version",
        (haproxy_cfg, nft_rules, content_hash),
    )
    row = await cur.fetchone()
    return row["version"]


async def get_edge(conn: AsyncConnection, edge_id: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM edges WHERE id = %s", (edge_id,))
    return await cur.fetchone()


async def touch(conn: AsyncConnection, edge_id: str) -> None:
    await conn.execute("UPDATE edges SET last_seen_at = now() WHERE id = %s", (edge_id,))


async def report(
    conn: AsyncConnection, edge_id: str, applied_version: int | None, error: str | None, tailscale_ip: str | None = None
) -> None:
    await conn.execute(
        """UPDATE edges SET last_seen_at = now(),
                  applied_version = COALESCE(%s, applied_version),
                  last_error = %s,
                  tailscale_ip = COALESCE(%s::inet, tailscale_ip)
           WHERE id = %s""",
        (applied_version, error, tailscale_ip, edge_id),
    )


async def applied_by_live(conn: AsyncConnection, version: int) -> bool:
    """使用中の edge と、直近2分以内に応答のあった edge がすべて version 以上を適用したか。

    止まっている待機側の edge（メンテナンス中など）のせいでデプロイが失敗しないように、応答の無い待機側は数えない。
    edge が1台も登録されていなければ True（開発環境）。
    """
    cur = await conn.execute(
        """SELECT COALESCE(bool_and(COALESCE(applied_version, 0) >= %s), true) AS ok
           FROM edges WHERE is_active OR last_seen_at > now() - interval '2 minutes'""",
        (version,),
    )
    row = await cur.fetchone()
    return bool(row["ok"])
