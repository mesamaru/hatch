"""操作ログ（追記のみ）。target・detail にユーザー名・メール・Discord 名を書かないこと（退会後も残るため）。"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


async def add(
    conn: AsyncConnection,
    *,
    action: str,
    via: str,
    actor_id: str | None = None,
    target: str | None = None,
    server_id: str | None = None,
    job_id: int | None = None,
    result: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO audit_log (actor_id, via, action, target, server_id, job_id, result, detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (actor_id, via, action, target, server_id, job_id, result, Jsonb(detail) if detail else None),
    )
