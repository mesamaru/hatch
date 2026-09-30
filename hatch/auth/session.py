"""セッション Cookie・CSRF・権限の依存関数（FastAPI の Depends で使う）。

@router.post("/x")
async def x(p: Principal = Depends(require_user)): ...      # ログイン必須（変更系は CSRF も確認）
async def y(p: Principal = Depends(require_admin)): ...     # 管理者＋二段階認証済み
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass

from fastapi import Request, Response

from .. import db
from ..config import get_core_settings
from ..domain.permissions import Actor
from ..errors import AppError
from ..repo import users as users_repo
from .crypto import sha256_hex

COOKIE = "pd_session"
STATE_COOKIE = "pd_oauth_state"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Principal:
    sid_hash: str
    user_id: str
    username: str
    email: str
    role: str
    status: str
    csrf_token: str
    totp_ok: bool
    totp_enabled: bool
    max_servers: int

    @property
    def actor(self) -> Actor:
        return Actor(
            id=self.user_id, role=self.role, deleting=self.status == "deleting", suspended=self.status == "suspended"
        )

    @property
    def needs_totp(self) -> bool:
        return self.role == "admin" and not self.totp_ok


def cookie_secure() -> bool:
    return get_core_settings().PD_PUBLIC_URL.startswith("https://")


def set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        COOKIE,
        token,
        max_age=users_repo.SESSION_DAYS * 86400,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE, path="/", secure=cookie_secure(), httponly=True, samesite="lax")


def new_session_token() -> tuple[str, str]:
    """(Cookie に入れる値, DB に保存するハッシュ)"""
    token = secrets.token_urlsafe(32)
    return token, sha256_hex(token)


async def _load(request: Request) -> Principal | None:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 100:
        return None
    sid = sha256_hex(token)
    async with db.transaction() as conn:
        row = await users_repo.get_session(conn, sid)
        if row is None:
            return None
        if row["status"] in ("deleted", "invited"):
            await users_repo.delete_session(conn, sid)
            return None
        await users_repo.touch_session(conn, sid)
    return Principal(
        sid,
        row["user_id"],
        row["username"],
        row["email"],
        row["role"],
        row["status"],
        row["csrf_token"],
        row["totp_ok"],
        row["totp_enabled"],
        row["max_servers"],
    )


def _check_csrf(request: Request, p: Principal) -> None:
    if request.method in SAFE_METHODS:
        return
    given = request.headers.get("x-csrf-token", "")
    if not given or not hmac.compare_digest(given.encode(), p.csrf_token.encode()):
        raise AppError("csrf_failed", "画面を再読み込みしてから、もう一度操作してください。", 403)


async def require_session(request: Request) -> Principal:
    """ログインしていれば誰でも（利用停止中でも）。CSRF は確認する。/api/me と退会の取り消し用。"""
    p = await _load(request)
    if p is None:
        raise AppError("unauthenticated", "ログインしてください。", 401)
    _check_csrf(request, p)
    return p


async def require_user(request: Request) -> Principal:
    p = await require_session(request)
    if p.status == "suspended":
        raise AppError("account_suspended", "このアカウントは利用停止中です。", 403)
    return p


async def require_admin(request: Request) -> Principal:
    p = await require_user(request)
    if p.role != "admin":
        raise AppError("forbidden", "管理者だけが使えます。", 403)
    if not p.totp_ok:
        raise AppError("totp_required", "二段階認証を行ってください。", 403)
    return p
