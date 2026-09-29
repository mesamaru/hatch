"""ユーザー・セッション・二段階認証。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

SESSION_DAYS = 30


async def by_discord_id(conn: AsyncConnection, discord_id: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT *, id::text AS id FROM users WHERE discord_id = %s", (discord_id,))
    return await cur.fetchone()


async def by_id(conn: AsyncConnection, user_id: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT *, id::text AS id FROM users WHERE id = %s", (user_id,))
    return await cur.fetchone()


async def username_taken(conn: AsyncConnection, username: str) -> bool:
    cur = await conn.execute("SELECT 1 FROM users WHERE username = %s", (username,))
    return await cur.fetchone() is not None


async def email_owner(conn: AsyncConnection, email: str) -> str | None:
    cur = await conn.execute("SELECT id::text AS id FROM users WHERE lower(email) = lower(%s)", (email,))
    row = await cur.fetchone()
    return row["id"] if row else None


async def role_rules(conn: AsyncConnection, role_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    if not role_ids:
        return []
    cur = await conn.execute("SELECT * FROM discord_role_rules WHERE discord_role_id = ANY(%s)", (list(role_ids),))
    return list(await cur.fetchall())


async def create(
    conn: AsyncConnection, *, username: str, email: str, discord_id: str, role: str, max_servers: int
) -> str:
    cur = await conn.execute(
        """INSERT INTO users (username, email, discord_id, role, max_servers, status)
           VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id::text AS id""",
        (username, email, discord_id, role, max_servers),
    )
    return (await cur.fetchone())["id"]


async def is_banned(conn: AsyncConnection, discord_hash: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM banned_identities WHERE discord_id_hash = %s AND (expires_at IS NULL OR expires_at > now())",
        (discord_hash,),
    )
    return await cur.fetchone() is not None


# ---- セッション ----
async def create_session(
    conn: AsyncConnection, *, sid_hash: str, user_id: str, csrf: str, user_agent: str | None, ip: str | None
) -> None:
    await conn.execute(
        """INSERT INTO sessions (id, user_id, csrf_token, expires_at, user_agent, ip)
           VALUES (%s, %s, %s, now() + make_interval(days => %s), %s, %s)""",
        (sid_hash, user_id, csrf, SESSION_DAYS, (user_agent or "")[:300], ip),
    )


async def get_session(conn: AsyncConnection, sid_hash: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        """SELECT s.id AS sid, s.csrf_token, s.totp_ok, s.totp_failures, s.expires_at, s.last_seen_at,
                  u.id::text AS user_id, u.username, u.email, u.role, u.status, u.totp_enabled, u.max_servers,
                  u.deletion_due_at
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.id = %s AND s.expires_at > now()""",
        (sid_hash,),
    )
    return await cur.fetchone()


async def touch_session(conn: AsyncConnection, sid_hash: str) -> None:
    await conn.execute(
        "UPDATE sessions SET last_seen_at = now() WHERE id = %s AND last_seen_at < now() - interval '5 minutes'",
        (sid_hash,),
    )


async def delete_session(conn: AsyncConnection, sid_hash: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = %s", (sid_hash,))


async def delete_user_sessions(conn: AsyncConnection, user_id: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
