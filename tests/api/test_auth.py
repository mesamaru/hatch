"""ログイン・セッション・CSRF・二段階認証（docs/TASKS.md T05）。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi import APIRouter, Depends

from hatch import db
from hatch.adapters.discord_oauth import DiscordIdentity
from hatch.api import auth as auth_api
from hatch.auth import crypto
from hatch.auth.session import Principal, require_admin
from hatch.errors import AppError
from hatch.main import app

ADMIN_ROLE, USER_ROLE = "900", "901"


class FakeOAuth:
    def __init__(self):
        self.ident: DiscordIdentity | Exception | None = None

    def authorize_url(self, state: str) -> str:
        return f"https://discord.test/authorize?state={state}"

    async def identify(self, code: str) -> DiscordIdentity:
        if isinstance(self.ident, Exception):
            raise self.ident
        return self.ident


# 管理者専用の確認用ルート（テストのときだけ登録）
_probe = APIRouter()


@_probe.get("/api/admin/_probe")
async def _admin_probe(p: Principal = Depends(require_admin)):
    return {"ok": True}


if not any(getattr(r, "path", "") == "/api/admin/_probe" for r in app.router.routes):
    app.router.routes.insert(0, _probe.routes[0])


@pytest.fixture
async def env(db_url):
    await db.open_pool(db_url, max_size=4)
    with psycopg.connect(db_url) as c:
        c.execute(
            "INSERT INTO discord_role_rules (discord_role_id, label, grants_role, max_servers) VALUES "
            "(%s, '運営', 'admin', 0), (%s, 'サポーター', 'user', 5)",
            (ADMIN_ROLE, USER_ROLE),
        )
    fake = FakeOAuth()
    app.dependency_overrides[auth_api.get_oauth] = lambda: fake
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://panel.test")
    yield {"url": db_url, "oauth": fake, "client": client}
    await client.aclose()
    app.dependency_overrides.clear()
    await db.close_pool()


def ident(id_="111", name="Tanaka_Craft!", roles=(USER_ROLE,), email="t@example.com", verified=True):
    return DiscordIdentity(id_, name, email, verified, tuple(roles))


async def login(env, identity) -> httpx.Response:
    cl = env["client"]
    env["oauth"].ident = identity
    r = await cl.get("/api/auth/login")
    assert r.status_code == 303
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return await cl.get("/api/auth/callback", params={"code": "c", "state": state})


def q(url, sql, args=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


async def test_login_creates_user_and_session(env):
    r = await login(env, ident())
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert "pd_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
    assert q(env["url"], "SELECT username, role, max_servers FROM users") == [("tanaka_craft", "user", 5)]
    # DB にはトークンそのものではなくハッシュを保存している
    token = env["client"].cookies.get("pd_session")
    assert q(env["url"], "SELECT count(*) FROM sessions WHERE id = %s", (crypto.sha256_hex(token),)) == [(1,)]
    me = (await env["client"].get("/api/me")).json()
    assert me["user"]["username"] == "tanaka_craft" and me["needs_totp"] is False
    assert me["limits"] == {"max_servers": 5, "used": 0} and me["csrf_token"]


async def test_state_mismatch_is_rejected(env):
    env["oauth"].ident = ident()
    await env["client"].get("/api/auth/login")
    r = await env["client"].get("/api/auth/callback", params={"code": "c", "state": "forged"})
    assert r.headers["location"] == "/?login_error=state"
    assert q(env["url"], "SELECT count(*) FROM users") == [(0,)]


@pytest.mark.parametrize(
    ("identity", "code"),
    [
        (ident(roles=()), "not_allowed"),
        (ident(verified=False), "email"),
        (AppError("not_member", "x", 403), "not_member"),
    ],
)
async def test_rejections(env, identity, code):
    r = await login(env, identity)
    assert r.headers["location"] == f"/?login_error={code}"


async def test_banned_identity(env):
    with psycopg.connect(env["url"]) as c:
        c.execute(
            "INSERT INTO banned_identities (discord_id_hash, reason) VALUES (%s, '違反')",
            (crypto.hmac_hex("111", "discord-id"),),
        )
    r = await login(env, ident())
    assert r.headers["location"] == "/?login_error=not_allowed"


async def test_suspended_user_cannot_login(env):
    await login(env, ident())
    with psycopg.connect(env["url"]) as c:
        c.execute("UPDATE users SET status='suspended'")
    r = await login(env, ident())
    assert r.headers["location"] == "/?login_error=suspended"


async def test_username_collision_gets_suffix(env):
    await login(env, ident(id_="111", name="tanaka", email="a@x"))
    env["client"].cookies.clear()
    await login(env, ident(id_="222", name="tanaka", email="b@x"))
    assert sorted(r[0] for r in q(env["url"], "SELECT username FROM users")) == ["tanaka", "tanaka-2"]


async def test_csrf_and_logout(env):
    await login(env, ident())
    cl = env["client"]
    r = await cl.post("/api/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    csrf = (await cl.get("/api/me")).json()["csrf_token"]
    r = await cl.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    assert (await cl.get("/api/me")).status_code == 401
    assert q(env["url"], "SELECT count(*) FROM sessions") == [(0,)]


async def test_expired_session(env):
    await login(env, ident())
    with psycopg.connect(env["url"]) as c:
        c.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
    r = await env["client"].get("/api/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"


async def test_admin_needs_totp(env):
    await login(env, ident(roles=(ADMIN_ROLE,)))
    cl = env["client"]
    me = (await cl.get("/api/me")).json()
    assert me["needs_totp"] is True and me["limits"]["max_servers"] is None
    r = await cl.get("/api/admin/_probe")
    assert r.status_code == 403 and r.json()["error"]["code"] == "totp_required"
    h = {"X-CSRF-Token": me["csrf_token"]}
    secret = (await cl.post("/api/auth/totp/setup", headers=h)).json()["secret"]
    r = await cl.post("/api/auth/totp/verify", json={"code": "000000"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "totp_invalid"
    code = crypto.totp_now(secret)
    assert (await cl.post("/api/auth/totp/verify", json={"code": code}, headers=h)).status_code == 204
    assert (await cl.get("/api/admin/_probe")).json() == {"ok": True}
    # 同じコードの再利用はできない
    assert (await cl.post("/api/auth/totp/verify", json={"code": code}, headers=h)).status_code == 400
    # 設定済みなら作り直せない
    assert (await cl.post("/api/auth/totp/setup", headers=h)).status_code == 409
    assert q(env["url"], "SELECT secret_enc <> %s FROM user_totp", (secret,)) == [(True,)]  # 暗号化して保存


async def test_totp_failures_revoke_session(env):
    await login(env, ident(roles=(ADMIN_ROLE,)))
    cl = env["client"]
    h = {"X-CSRF-Token": (await cl.get("/api/me")).json()["csrf_token"]}
    await cl.post("/api/auth/totp/setup", headers=h)
    for _ in range(4):
        assert (await cl.post("/api/auth/totp/verify", json={"code": "111111"}, headers=h)).status_code == 400
    r = await cl.post("/api/auth/totp/verify", json={"code": "111111"}, headers=h)
    assert r.status_code == 401
    assert (await cl.get("/api/me")).status_code == 401


async def test_non_admin_forbidden(env):
    await login(env, ident())
    r = await env["client"].get("/api/admin/_probe")
    assert r.json()["error"]["code"] == "forbidden"


async def test_role_rules_update_existing_user(env):
    await login(env, ident())
    await login(env, ident(roles=(ADMIN_ROLE, USER_ROLE)))
    assert q(env["url"], "SELECT role, max_servers FROM users") == [("admin", 5)]


async def test_tos_acceptance(env):
    with psycopg.connect(env["url"]) as c:
        c.execute("INSERT INTO tos_versions (version, body_md) VALUES ('2026-10-01', '規約')")
        c.execute("UPDATE app_settings SET value = '\"2026-10-01\"' WHERE key = 'tos_current_version'")
    await login(env, ident())
    cl = env["client"]
    me = (await cl.get("/api/me")).json()
    assert me["needs_tos"] is True and me["tos_version"] == "2026-10-01"
    h = {"X-CSRF-Token": me["csrf_token"]}
    assert (await cl.post("/api/me/tos", json={"version": "old"}, headers=h)).status_code == 409
    assert (await cl.post("/api/me/tos", json={"version": "2026-10-01"}, headers=h)).status_code == 204
    assert (await cl.get("/api/me")).json()["needs_tos"] is False


def test_totp_rfc6238_vector():
    # RFC 6238 の SHA-1 のテストベクター（秘密 "12345678901234567890"、59秒 → 94287082 の下6桁）
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert crypto.totp_now(secret, at=59) == "287082"
    assert crypto.totp_match(secret, "287082", at=59) == 1
    assert crypto.totp_match(secret, "287082", after_counter=1, at=59) is None


def test_encrypt_roundtrip():
    t = crypto.encrypt("秘密", "totp")
    assert crypto.decrypt(t, "totp") == "秘密"
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        crypto.decrypt(t, "other")
