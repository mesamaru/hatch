"""サポーターへのサーバーの割り当て。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection


async def list_supporter_users(conn: AsyncConnection) -> list[dict[str, Any]]:
    """割り当ての候補（サポーターの権限を持つ、利用中のユーザー）。"""
    cur = await conn.execute(
        """SELECT id::text AS id, username FROM users
           WHERE role = 'supporter' AND status IN ('active','invited') ORDER BY username"""
    )
    return list(await cur.fetchall())


async def of_server(conn: AsyncConnection, server_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """SELECT u.id::text AS id, u.username, u.role, x.created_at
           FROM server_supporters x JOIN users u ON u.id = x.user_id
           WHERE x.server_id = %s ORDER BY u.username""",
        (server_id,),
    )
    return list(await cur.fetchall())


async def assign(conn: AsyncConnection, server_id: str, user_id: str, by: str) -> bool:
    """割り当てる。既に割り当て済みなら False。"""
    cur = await conn.execute(
        """INSERT INTO server_supporters (server_id, user_id, assigned_by) VALUES (%s, %s, %s)
           ON CONFLICT DO NOTHING RETURNING 1""",
        (server_id, user_id, by),
    )
    return await cur.fetchone() is not None


async def unassign(conn: AsyncConnection, server_id: str, user_id: str) -> bool:
    cur = await conn.execute(
        "DELETE FROM server_supporters WHERE server_id = %s AND user_id = %s RETURNING 1", (server_id, user_id)
    )
    return await cur.fetchone() is not None


async def clear_user(conn: AsyncConnection, user_id: str) -> int:
    """サポーターでなくなった人の割り当てを外す。"""
    cur = await conn.execute("DELETE FROM server_supporters WHERE user_id = %s", (user_id,))
    return cur.rowcount
