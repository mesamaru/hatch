"""API テストの共通部品：DB に直接ユーザーとセッションを作り、ログイン済みのクライアントを返す。"""

from __future__ import annotations

import secrets

import httpx
import psycopg

from pterodeploy.auth.crypto import sha256_hex
from pterodeploy.main import app


def make_user(db_url: str, username: str, *, role: str = "user", max_servers: int = 3) -> str:
    with psycopg.connect(db_url) as c:
        return c.execute(
            "INSERT INTO users (username, email, role, max_servers, totp_enabled) VALUES (%s,%s,%s,%s,%s) "
            "RETURNING id::text",
            (username, f"{username}@example.com", role, max_servers, role == "admin"),
        ).fetchone()[0]


def client_for(db_url: str, user_id: str, *, totp_ok: bool = True) -> httpx.AsyncClient:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(16)
    with psycopg.connect(db_url) as c:
        c.execute(
            "INSERT INTO sessions (id, user_id, csrf_token, totp_ok, expires_at) "
            "VALUES (%s,%s,%s,%s, now() + interval '1 day')",
            (sha256_hex(token), user_id, csrf, totp_ok),
        )
    cl = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://panel.test", headers={"X-CSRF-Token": csrf}
    )
    cl.cookies.set("pd_session", token)
    return cl
