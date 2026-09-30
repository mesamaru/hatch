"""ログイン・ログアウト・二段階認証。docs/API.md「認証・自分」。"""

from __future__ import annotations

import hmac
import logging
import re
import secrets

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .. import db
from ..adapters.discord_oauth import DiscordIdentity, DiscordOAuth, DiscordOAuthClient
from ..auth import crypto
from ..auth.session import (
    STATE_COOKIE,
    Principal,
    clear_session_cookie,
    cookie_secure,
    new_session_token,
    require_session,
    require_user,
    set_session_cookie,
)
from ..config import get_settings
from ..errors import AppError, TransientError, UpstreamError
from ..repo import audit
from ..repo import supporters as supporters_repo
from ..repo import users as users_repo

log = logging.getLogger("hatch.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])
TOTP_MAX_FAILURES = 5


def get_oauth() -> DiscordOAuth:
    s = get_settings()
    return DiscordOAuthClient(
        s.DISCORD_CLIENT_ID,
        s.DISCORD_CLIENT_SECRET.get_secret_value(),
        f"{s.PD_PUBLIC_URL}/api/auth/callback",
        s.DISCORD_GUILD_ID,
        s.DISCORD_BOT_TOKEN.get_secret_value(),
    )


ROLE_RANK = ("user", "supporter", "admin")
OWNER_DEFAULT_MAX = 10


def grant_for(rules: list[dict], is_owner: bool) -> tuple[str, int] | None:
    """ロールの対応表から (権限, 台数) を決める。複数あれば高い権限・多い台数。対象外なら None。

    Discord サーバーのオーナーは、ロールが無くても管理者にする（最初の管理者が締め出されないように）。
    """
    if not rules and not is_owner:
        return None
    role = max((r["grants_role"] for r in rules), key=ROLE_RANK.index, default="user")
    max_servers = max((r["max_servers"] for r in rules), default=OWNER_DEFAULT_MAX)
    if is_owner:
        role = "admin"
    return role, max_servers


def _fail(code: str) -> RedirectResponse:
    """ログインの失敗は画面に戻して理由を表示する（画面側が code から日本語の説明を出す）。"""
    r = RedirectResponse(f"/?login_error={code}", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/api/auth")
    return r


@router.get("/login")
async def login(oauth: DiscordOAuth = Depends(get_oauth)) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    r = RedirectResponse(oauth.authorize_url(state), status_code=303)
    r.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True, secure=cookie_secure(), samesite="lax", path="/api/auth"
    )
    return r


def _username_from(ident: DiscordIdentity) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "", ident.username.lower())[:32]
    if not re.match(r"^[a-z0-9][a-z0-9_.-]{2,31}$", base):
        base = f"user{ident.id[-6:]}"
    return base


async def _unique_username(conn, base: str) -> str:
    name, n = base, 2
    while await users_repo.username_taken(conn, name):
        suffix = f"-{n}"
        name = base[: 32 - len(suffix)] + suffix
        n += 1
    return name


@router.get("/callback")
async def callback(
    request: Request, code: str = "", state: str = "", oauth: DiscordOAuth = Depends(get_oauth)
) -> RedirectResponse:
    want = request.cookies.get(STATE_COOKIE, "")
    if not code or not state or not want or not hmac.compare_digest(state.encode(), want.encode()):
        return _fail("state")
    try:
        ident = await oauth.identify(code)
    except AppError as e:
        return _fail(e.code if e.code == "not_member" else "discord")
    except (UpstreamError, TransientError):
        return _fail("discord")

    async with db.transaction() as conn:
        if await users_repo.is_banned(conn, crypto.hmac_hex(ident.id, "discord-id")):
            return _fail("not_allowed")
        user = await users_repo.by_discord_id(conn, ident.id)
        rules = await users_repo.role_rules(conn, ident.role_ids)
        grant = grant_for(rules, ident.is_owner)
        if user is None:
            if grant is None:
                # 理由を調べられるように、件数だけ残す（ロールの名前や ID、利用者の名前は残さない）
                log.info(
                    "ログインを断りました：対応するロールがありません"
                    "（持っているロール %d 個・登録済みのロール %d 個）",
                    len(ident.role_ids),
                    await users_repo.count_role_rules(conn),
                )
                return _fail("no_role")
            if not ident.email or not ident.email_verified:
                return _fail("email")
            if await users_repo.email_owner(conn, ident.email):
                return _fail("email_taken")
            role, max_servers = grant
            user_id = await users_repo.create(
                conn,
                username=await _unique_username(conn, _username_from(ident)),
                email=ident.email,
                discord_id=ident.id,
                role=role,
                max_servers=max_servers,
            )
            await audit.add(conn, action="アカウントを作成", via="web", actor_id=user_id, detail={"role": role})
        else:
            user_id = user["id"]
            if user["status"] == "deleted":
                return _fail("not_allowed")
            if user["status"] == "suspended":
                return _fail("suspended")
            if user["status"] == "invited":
                await conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (user_id,))
            if grant:  # ロールの対応表に合わせて権限と台数を更新する（ロールが無い人は招待で入った人なので変えない）
                role, max_servers = grant
                await conn.execute(
                    "UPDATE users SET role = %s, max_servers = %s, updated_at = now() WHERE id = %s",
                    (role, max_servers, user_id),
                )
                if role != user["role"]:
                    if role != "supporter":
                        await supporters_repo.clear_user(conn, user_id)
                    await audit.add(
                        conn,
                        action="Discord のロールで権限が変わりました",
                        via="web",
                        actor_id=user_id,
                        detail={"from": user["role"], "to": role},
                    )
        token, sid = new_session_token()
        await users_repo.create_session(
            conn,
            sid_hash=sid,
            user_id=user_id,
            csrf=secrets.token_urlsafe(24),
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
        await audit.add(conn, action="ログイン", via="web", actor_id=user_id)
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(STATE_COOKIE, path="/api/auth")
    set_session_cookie(r, token)
    return r


@router.post("/logout", status_code=204)
async def logout(p: Principal = Depends(require_session)) -> Response:
    async with db.transaction() as conn:
        await users_repo.delete_session(conn, p.sid_hash)
    r = Response(status_code=204)
    clear_session_cookie(r)
    return r


# ---- 二段階認証（任意。本人が設定から有効・無効にする） ----
@router.post("/totp/setup")
async def totp_setup(p: Principal = Depends(require_session)) -> dict:
    """鍵を作る。コードを確かめる（/totp/verify）までは有効にならない。"""
    if p.totp_enabled:
        raise AppError("totp_already", "二段階認証は既に有効です。設定し直すには、いったん無効にしてください。", 409)
    secret = crypto.new_totp_secret()
    async with db.transaction() as conn:
        await conn.execute(
            """INSERT INTO user_totp (user_id, secret_enc) VALUES (%s, %s)
               ON CONFLICT (user_id) DO UPDATE SET secret_enc = EXCLUDED.secret_enc, last_counter = 0,
                                                   created_at = now()""",
            (p.user_id, crypto.encrypt(secret, "totp")),
        )
    return {"secret": secret, "otpauth_url": crypto.otpauth_url(secret, p.username)}


class TotpCode(BaseModel):
    code: str = Field(min_length=6, max_length=8)


async def _match_code(p: Principal, code: str) -> int:
    """コードを確かめ、使ったカウンターを返す。続けて間違えるとログアウトさせる。"""
    async with db.transaction() as conn:
        cur = await conn.execute(
            "SELECT secret_enc, last_counter FROM user_totp WHERE user_id = %s FOR UPDATE", (p.user_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise AppError("totp_not_set", "先に二段階認証を設定してください。", 409)
        counter = crypto.totp_match(crypto.decrypt(row["secret_enc"], "totp"), code, after_counter=row["last_counter"])
        if counter is None:
            cur = await conn.execute(
                "UPDATE sessions SET totp_failures = totp_failures + 1 WHERE id = %s RETURNING totp_failures",
                (p.sid_hash,),
            )
            failures = (await cur.fetchone())["totp_failures"]
            if failures >= TOTP_MAX_FAILURES:
                await users_repo.delete_session(conn, p.sid_hash)
                await audit.add(conn, action="二段階認証に続けて失敗", via="web", actor_id=p.user_id, result="failed")
    if counter is None:
        if failures >= TOTP_MAX_FAILURES:
            raise AppError(
                "unauthenticated", "続けて間違えたため、ログアウトしました。もう一度ログインしてください。", 401
            )
        raise AppError("totp_invalid", "コードが正しくありません。認証アプリの最新のコードを入力してください。", 400)
    return counter


@router.post("/totp/verify", status_code=204)
async def totp_verify(body: TotpCode, p: Principal = Depends(require_session)) -> Response:
    """有効にする（初回）か、ログイン後にコードを入力する。"""
    counter = await _match_code(p, body.code)
    async with db.transaction() as conn:
        await conn.execute("UPDATE user_totp SET last_counter = %s WHERE user_id = %s", (counter, p.user_id))
        await conn.execute("UPDATE users SET totp_enabled = true WHERE id = %s", (p.user_id,))
        await conn.execute("UPDATE sessions SET totp_ok = true, totp_failures = 0 WHERE id = %s", (p.sid_hash,))
        action = "二段階認証" if p.totp_enabled else "二段階認証を有効化"
        await audit.add(conn, action=action, via="web", actor_id=p.user_id)
    return Response(status_code=204)


@router.post("/totp/disable", status_code=204)
async def totp_disable(body: TotpCode, p: Principal = Depends(require_user)) -> Response:
    """無効にする。今のコードの入力が必要（セッションを盗まれただけでは外せないように）。"""
    if not p.totp_enabled:
        raise AppError("totp_not_set", "二段階認証は有効になっていません。", 409)
    await _match_code(p, body.code)
    async with db.transaction() as conn:
        await conn.execute("DELETE FROM user_totp WHERE user_id = %s", (p.user_id,))
        await conn.execute("UPDATE users SET totp_enabled = false WHERE id = %s", (p.user_id,))
        await audit.add(conn, action="二段階認証を無効化", via="web", actor_id=p.user_id)
    return Response(status_code=204)
