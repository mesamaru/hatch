"""自分の情報。docs/API.md「認証・自分」。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from .. import db
from ..auth.session import Principal, require_session
from ..errors import AppError
from ..repo import audit
from ..repo import servers as servers_repo

router = APIRouter(prefix="/api/me", tags=["me"])


async def _tos(conn, user_id: str) -> tuple[str | None, bool]:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = 'tos_current_version'")
    row = await cur.fetchone()
    version = row["value"] if row and row["value"] else None
    if not version:
        return None, False
    cur = await conn.execute(
        "SELECT 1 FROM tos_acceptances WHERE user_id = %s AND tos_version = %s", (user_id, version)
    )
    return str(version), await cur.fetchone() is None


@router.get("")
async def me(p: Principal = Depends(require_session)) -> dict:
    async with db.transaction() as conn:
        version, needs_tos = await _tos(conn, p.user_id)
        used = await servers_repo.count_owned(conn, p.user_id)
        cur = await conn.execute("SELECT deletion_due_at FROM users WHERE id = %s", (p.user_id,))
        due = (await cur.fetchone())["deletion_due_at"]
    return {
        "user": {
            "id": p.user_id,
            "username": p.username,
            "role": p.role,
            "status": p.status,
            "deletion_due_at": due.isoformat().replace("+00:00", "Z") if due else None,
        },
        "csrf_token": p.csrf_token,
        "needs_tos": needs_tos,
        "tos_version": version,
        "needs_totp": p.needs_totp,
        "totp_enabled": p.totp_enabled,
        "limits": {"max_servers": None if p.role == "admin" else p.max_servers, "used": used},
    }


class TosAccept(BaseModel):
    version: str = Field(min_length=1, max_length=40)


@router.post("/tos", status_code=204)
async def accept_tos(body: TosAccept, p: Principal = Depends(require_session)) -> Response:
    async with db.transaction() as conn:
        version, _ = await _tos(conn, p.user_id)
        if version is None or body.version != version:
            raise AppError("tos_outdated", "利用規約が更新されています。画面を再読み込みしてください。", 409)
        await conn.execute(
            "INSERT INTO tos_acceptances (user_id, tos_version) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (p.user_id, version),
        )
        await audit.add(conn, action="利用規約に同意", via="web", actor_id=p.user_id, target=version)
    return Response(status_code=204)
