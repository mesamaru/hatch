"""初期設定の完了状態と、初期設定で登録する Discord ロールの対応。"""

from __future__ import annotations

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
