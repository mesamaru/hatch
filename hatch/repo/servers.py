"""サーバー。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

ACTIVE = ("pending", "provisioning", "running", "stopped", "suspended", "maintenance", "trashed", "purging")


async def get(conn: AsyncConnection, server_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM servers WHERE id = %s" + (" FOR UPDATE" if lock else ""), (server_id,))
    return await cur.fetchone()


async def name_in_use(conn: AsyncConnection, name: str) -> bool:
    cur = await conn.execute("SELECT 1 FROM servers WHERE name = %s AND status NOT IN ('purged','failed')", (name,))
    return await cur.fetchone() is not None


async def count_owned(conn: AsyncConnection, owner_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) AS n FROM servers "
        "WHERE owner_id = %s AND NOT is_dev_copy AND status NOT IN ('purged','failed')",
        (owner_id,),
    )
    return (await cur.fetchone())["n"]


async def insert_pending(
    conn: AsyncConnection, *, name: str, owner_id: str, plan_id: str, game: str, fqdn: str, period_days: int
) -> str:
    cur = await conn.execute(
        """INSERT INTO servers (name, owner_id, plan_id, game, fqdn, status, expires_at)
           VALUES (%s, %s, %s, %s, %s, 'pending', now() + make_interval(days => %s)) RETURNING id::text""",
        (name, owner_id, plan_id, game, fqdn, period_days),
    )
    return (await cur.fetchone())["id"]


async def set_fields(conn: AsyncConnection, server_id: str, **fields: Any) -> None:
    allowed = {"status", "node_id", "panel_server_id", "panel_uuid", "panel_allocation_id", "exposed", "fqdn"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"変更できない列: {bad}")
    cols = ", ".join(f"{k} = %s" for k in fields)
    await conn.execute(f"UPDATE servers SET {cols}, updated_at = now() WHERE id = %s", (*fields.values(), server_id))


async def node_loads(conn: AsyncConnection) -> list[dict[str, Any]]:
    """ノードごとの割り当て済みメモリ・ディスク（ゴミ箱のサーバーもディスクを使うので数える）。"""
    cur = await conn.execute(
        """SELECT n.id, n.panel_node_id, host(n.tailscale_ip) AS ip, n.memory_mb, n.disk_mb, n.overcommit_percent,
                  n.accepting,
                  COALESCE(sum(p.memory_mb) FILTER (WHERE s.id IS NOT NULL), 0)::int AS used_memory_mb,
                  COALESCE(sum(p.disk_mb) FILTER (WHERE s.id IS NOT NULL), 0)::int AS used_disk_mb
           FROM nodes n
           LEFT JOIN servers s ON s.node_id = n.id AND s.status NOT IN ('purged','failed')
           LEFT JOIN plans p ON p.id = s.plan_id
           GROUP BY n.id ORDER BY n.id"""
    )
    return list(await cur.fetchall())


VIEW_SQL = """
SELECT s.id::text AS id, s.name, s.game, s.plan_id AS plan, s.status, s.fqdn, s.node_id AS node, s.is_dev_copy,
       s.auto_restart, s.public_status, s.expires_at, s.maintenance_until, s.suspend_reason,
       s.panel_uuid::text AS panel_uuid,
       s.owner_id::text AS owner_id, u.username AS owner_name,
       sl.port, sl.host AS slot_host, d.name AS slot_domain, d.edge_host, sl.rule_id,
       cd.hostname AS custom_domain,
       px.id::text AS proxy_id, px.name AS proxy_name,
       sh.permission AS share_permission,
       (SELECT jsonb_object_agg(x.user_id::text, x.permission) FROM server_shares x WHERE x.server_id = s.id) AS shares,
       (SELECT array_agg(y.user_id::text) FROM server_supporters y WHERE y.server_id = s.id) AS supporters,
       (SELECT jsonb_build_object('id', j.id, 'kind', j.kind, 'status', j.status, 'current_step', j.current_step)
          FROM jobs j WHERE j.server_id = s.id AND j.status IN ('queued','running') ORDER BY j.id DESC LIMIT 1) AS job
FROM servers s
JOIN users u ON u.id = s.owner_id
LEFT JOIN slots sl ON sl.server_id = s.id
LEFT JOIN domains d ON d.id = sl.domain_id
LEFT JOIN custom_domains cd ON cd.server_id = s.id AND cd.status = 'active'
LEFT JOIN servers px ON px.id = s.proxy_server_id
LEFT JOIN server_shares sh ON sh.server_id = s.id AND sh.user_id = %(me)s
LEFT JOIN server_supporters sp ON sp.server_id = s.id AND sp.user_id = %(me)s
"""


async def visible(
    conn: AsyncConnection,
    me: str,
    is_admin: bool,
    *,
    is_supporter: bool = False,
    owner: str | None = None,
    statuses: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """見えるサーバー（admin は全員分、利用者は自分のものと共有されたもの、サポーターは割り当て分も）。"""
    where = ["s.status = ANY(%(st)s)"]
    if not is_admin:
        mine = "s.owner_id = %(me)s OR sh.user_id IS NOT NULL"
        if is_supporter:
            mine += " OR sp.user_id IS NOT NULL"
        where.append(f"({mine})")
    if owner:
        where.append("s.owner_id = %(owner)s")
    st = list(statuses or ("pending", "provisioning", "running", "stopped", "suspended", "maintenance"))
    cur = await conn.execute(
        VIEW_SQL + " WHERE " + " AND ".join(where) + " ORDER BY s.name", {"me": me, "st": st, "owner": owner}
    )
    return list(await cur.fetchall())


async def find_view(conn: AsyncConnection, me: str, id_or_name: str) -> dict[str, Any] | None:
    key = "s.id::text = %(k)s" if len(id_or_name) == 36 and id_or_name.count("-") == 4 else "s.name = %(k)s"
    cur = await conn.execute(
        VIEW_SQL + f" WHERE {key} AND s.status NOT IN ('purged','failed')", {"me": me, "k": id_or_name}
    )
    return await cur.fetchone()
