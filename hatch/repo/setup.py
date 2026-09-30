"""初期設定の完了状態と、初期設定で登録する Discord ロールの対応。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def completed(conn: AsyncConnection) -> bool:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = 'setup_completed_at'")
    row = await cur.fetchone()
    return bool(row and row["value"])


async def mark_completed(conn: AsyncConnection) -> None:
    await conn.execute(
        """INSERT INTO app_settings (key, value, updated_at) VALUES ('setup_completed_at', to_jsonb(now()), now())
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""
    )


async def role_rules(conn: AsyncConnection) -> list[dict[str, Any]]:
    """登録済みのロールの対応（初期設定をやり直すときに、今の割り当てを画面に出すため）。"""
    cur = await conn.execute(
        "SELECT discord_role_id AS id, grants_role AS grants, max_servers FROM discord_role_rules ORDER BY label"
    )
    return [dict(r) for r in await cur.fetchall()]


async def upsert_role_rule(
    conn: AsyncConnection, *, role_id: str, label: str, grants_role: str, max_servers: int
) -> None:
    await conn.execute(
        """INSERT INTO discord_role_rules (discord_role_id, label, grants_role, max_servers)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (discord_role_id) DO UPDATE
             SET label = EXCLUDED.label, grants_role = EXCLUDED.grants_role, max_servers = EXCLUDED.max_servers""",
        (role_id, label, grants_role, max_servers),
    )


async def replace_role_rules(conn: AsyncConnection, roles: list[dict[str, Any]]) -> None:
    """ロールの対応を roles のとおりにする（プランなど画面に無い項目は、残るロールでは保つ）。"""
    await conn.execute(
        "DELETE FROM discord_role_rules WHERE NOT (discord_role_id = ANY(%s))", ([r["id"] for r in roles],)
    )
    for r in roles:
        await upsert_role_rule(
            conn, role_id=r["id"], label=r["name"], grants_role=r["grants"], max_servers=r["max_servers"]
        )
