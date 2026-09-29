"""スロット（ポート ↔ ホスト名）。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def get_with_rule(conn: AsyncConnection, port: int, *, lock: bool = False) -> dict[str, Any] | None:
    cur = await conn.execute(
        """SELECT sl.port, sl.host, sl.status, sl.server_id::text AS server_id, sl.dns_state,
                  r.id AS rule_id, r.name AS rule_name, r.host_template, r.record_mode, r.create_srv, r.prepublish,
                  r.assign_to, r.user_id::text AS rule_user_id, host(ip.address) AS rule_ip,
                  d.id AS domain_id, d.name AS domain, d.cf_zone_id, d.edge_host
           FROM slots sl
           JOIN slot_rules r ON r.id = sl.rule_id
           JOIN domains d ON d.id = sl.domain_id
           LEFT JOIN ip_addresses ip ON ip.id = r.ip_id
           WHERE sl.port = %s"""
        + (" FOR UPDATE OF sl" if lock else ""),
        (port,),
    )
    return await cur.fetchone()


async def assign(conn: AsyncConnection, port: int, server_id: str) -> bool:
    """空きなら自分に割り当てる（既に自分のものなら成功扱い）。取られていれば False。"""
    cur = await conn.execute(
        """UPDATE slots SET status = 'assigned', server_id = %s
           WHERE port = %s AND (status = 'free' OR (status = 'assigned' AND server_id = %s))
           RETURNING port""",
        (server_id, port, server_id),
    )
    return await cur.fetchone() is not None


async def release(conn: AsyncConnection, server_id: str) -> None:
    await conn.execute("UPDATE slots SET status = 'free', server_id = NULL WHERE server_id = %s", (server_id,))


async def of_server(conn: AsyncConnection, server_id: str) -> int | None:
    cur = await conn.execute("SELECT port FROM slots WHERE server_id = %s", (server_id,))
    row = await cur.fetchone()
    return row["port"] if row else None
