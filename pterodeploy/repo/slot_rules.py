"""アドレス枠とスロットの管理（管理画面用）。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from ..domain.slots import Binding, SlotRule, expand

RULE_COLS = """r.id, r.name, r.domain_id, d.name AS domain, d.edge_host, d.cf_zone_id, r.port_start, r.port_end,
  r.host_template, r.number_start, r.excluded_ports, r.record_mode, r.ip_id, host(ip.address) AS ip, r.create_srv,
  r.prepublish, r.assign_to, r.user_id::text AS user_id"""
RULE_FROM = "slot_rules r JOIN domains d ON d.id = r.domain_id LEFT JOIN ip_addresses ip ON ip.id = r.ip_id"


def to_rule(row: dict[str, Any]) -> SlotRule:
    return SlotRule(
        id=row["id"],
        name=row["name"],
        domain_id=row["domain_id"],
        domain=row["domain"],
        port_start=row["port_start"],
        port_end=row["port_end"],
        host_template=row["host_template"],
        number_start=row["number_start"],
        excluded_ports=tuple(row["excluded_ports"] or ()),
        record_mode=row["record_mode"],
        ip_id=row["ip_id"],
        create_srv=row["create_srv"],
        prepublish=row["prepublish"],
        assign_to=row["assign_to"],
        user_id=row["user_id"],
    )


async def list_rules(conn: AsyncConnection) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"""SELECT {RULE_COLS},
              count(sl.port) AS total,
              count(sl.port) FILTER (WHERE sl.status = 'free') AS free,
              count(sl.port) FILTER (WHERE sl.status = 'assigned') AS assigned,
              count(sl.port) FILTER (WHERE sl.status = 'held') AS held,
              count(sl.port) FILTER (WHERE sl.status = 'disabled') AS disabled,
              COALESCE(bool_and(sl.dns_state = 'published'), false) AS dns_published
            FROM {RULE_FROM} LEFT JOIN slots sl ON sl.rule_id = r.id
            GROUP BY r.id, d.id, ip.id ORDER BY d.name, r.port_start"""
    )
    return list(await cur.fetchall())


async def get_rule(conn: AsyncConnection, rule_id: int, *, lock: bool = False) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"SELECT {RULE_COLS} FROM {RULE_FROM} WHERE r.id = %s" + (" FOR UPDATE OF r" if lock else ""), (rule_id,)
    )
    return await cur.fetchone()


async def all_rules(conn: AsyncConnection) -> list[SlotRule]:
    cur = await conn.execute(f"SELECT {RULE_COLS} FROM {RULE_FROM}")
    return [to_rule(r) for r in await cur.fetchall()]


async def bindings(conn: AsyncConnection) -> list[Binding]:
    cur = await conn.execute("SELECT domain_id, host FROM ip_bindings")
    return [Binding(r["domain_id"], r["host"]) for r in await cur.fetchall()]


async def reserved(conn: AsyncConnection) -> set[str]:
    cur = await conn.execute("SELECT name FROM reserved_names")
    return {r["name"] for r in await cur.fetchall()}


async def in_use_ports(conn: AsyncConnection, rule_id: int) -> set[int]:
    cur = await conn.execute("SELECT port FROM slots WHERE rule_id = %s AND status IN ('assigned','held')", (rule_id,))
    return {r["port"] for r in await cur.fetchall()}


async def slots_of(conn: AsyncConnection, rule_id: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """SELECT sl.port, sl.host, sl.status, sl.dns_state, s.name AS server_name, s.id::text AS server_id
           FROM slots sl LEFT JOIN servers s ON s.id = sl.server_id WHERE sl.rule_id = %s ORDER BY sl.port""",
        (rule_id,),
    )
    return list(await cur.fetchall())


async def insert_rule(conn: AsyncConnection, r: SlotRule) -> int:
    cur = await conn.execute(
        """INSERT INTO slot_rules (name, domain_id, port_start, port_end, host_template, number_start, excluded_ports,
                                   record_mode, ip_id, create_srv, prepublish, assign_to, user_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            r.name,
            r.domain_id,
            r.port_start,
            r.port_end,
            r.host_template,
            r.number_start,
            list(r.excluded_ports),
            r.record_mode,
            r.ip_id,
            r.create_srv,
            r.prepublish,
            r.assign_to,
            r.user_id,
        ),
    )
    return (await cur.fetchone())["id"]


async def update_rule(conn: AsyncConnection, r: SlotRule) -> None:
    await conn.execute(
        """UPDATE slot_rules SET name=%s, domain_id=%s, port_start=%s, port_end=%s, host_template=%s, number_start=%s,
             excluded_ports=%s, record_mode=%s, ip_id=%s, create_srv=%s, prepublish=%s, assign_to=%s, user_id=%s
           WHERE id = %s""",
        (
            r.name,
            r.domain_id,
            r.port_start,
            r.port_end,
            r.host_template,
            r.number_start,
            list(r.excluded_ports),
            r.record_mode,
            r.ip_id,
            r.create_srv,
            r.prepublish,
            r.assign_to,
            r.user_id,
            r.id,
        ),
    )


async def sync_slots(conn: AsyncConnection, r: SlotRule) -> dict[str, int]:
    """枠の定義に合わせてスロットの行を増減する。使用中・予約中の行は変えない（検証済みの前提）。"""
    want = {s.port: s.host for s in expand(r) if not s.excluded}
    cur = await conn.execute("SELECT port, host, status FROM slots WHERE rule_id = %s", (r.id,))
    have = {row["port"]: row for row in await cur.fetchall()}
    removed = [p for p, row in have.items() if p not in want and row["status"] in ("free", "disabled")]
    if removed:
        await conn.execute("DELETE FROM slots WHERE rule_id = %s AND port = ANY(%s)", (r.id, removed))
    added = 0
    changed = 0
    for port, host in want.items():
        if port not in have:
            await conn.execute(
                "INSERT INTO slots (port, rule_id, domain_id, host) VALUES (%s,%s,%s,%s)",
                (port, r.id, r.domain_id, host),
            )
            added += 1
        elif have[port]["host"] != host and have[port]["status"] in ("free", "disabled"):
            await conn.execute(
                "UPDATE slots SET host = %s, domain_id = %s, dns_state = 'none' WHERE port = %s",
                (host, r.domain_id, port),
            )
            changed += 1
    return {"added": added, "removed": len(removed), "changed": changed}
