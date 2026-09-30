"""初期設定画面の API（docs/TASKS.md T41）。"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import psycopg
import pytest

from hatch import BASE_DIR, config, db
from hatch import setup as st
from hatch.adapters import setup_checks
from hatch.adapters.setup_checks import CheckResult
from hatch.main import app

ADMIN = {"id": "90000", "name": "運営", "grants": "admin", "max_servers": 10}
SUPPORT = {"id": "90002", "name": "サポーター", "grants": "supporter", "max_servers": 3}
MEMBER = {"id": "90001", "name": "メンバー", "grants": "user", "max_servers": 2}


@pytest.fixture
async def env(db_url, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PD_DATA_DIR", str(tmp_path))
    config.get_core_settings.cache_clear()
    await db.open_pool(db_url, max_size=4)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://panel.test")
    yield {"url": db_url, "client": client, "dir": tmp_path}
    await client.aclose()
    await db.close_pool()


def q(url: str, sql: str, args=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


async def unlocked(env) -> str:
    r = await env["client"].get("/api/setup/status")
    assert r.json()["needed"] is True
    return (env["dir"] / "setup-code").read_text(encoding="utf-8").strip()


async def test_fresh_install_needs_setup_and_creates_code(env):
    code = await unlocked(env)
    assert len(code) == 14 and code.count("-") == 2


async def test_wrong_code_is_rejected(env):
    await unlocked(env)
    r = await env["client"].post("/api/setup/unlock", json={"code": "AAAA-BBBB-CCCC"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "setup_code_invalid"


async def test_unlock_returns_public_values_but_never_secrets(env):
    code = await unlocked(env)
    r = await env["client"].post("/api/setup/unlock", json={"code": code.lower().replace("-", "")})
    assert r.status_code == 200
    body = r.json()
    assert body["values"]["PANEL_URL"] == "https://gp.test"
    assert "PANEL_APP_KEY" in body["secrets_set"]
    assert "ptla_dummy_app_key_for_tests" not in r.text


async def test_check_requires_code(env):
    await unlocked(env)
    r = await env["client"].post("/api/setup/check/kuma", json={"values": {}})
    assert r.status_code == 403


async def test_check_uses_current_value_when_blank(env, monkeypatch):
    code = await unlocked(env)
    seen = {}

    async def fake(url, key):
        seen.update(url=url, key=key)
        return CheckResult(True, "ok")

    monkeypatch.setattr(setup_checks, "check_kuma", fake)
    r = await env["client"].post(
        "/api/setup/check/kuma",
        json={"values": {"KUMA_URL": "http://kuma2.test:3001/"}},
        headers={"X-Setup-Code": code},
    )
    assert r.json() == {"ok": True, "message": "ok"}
    assert seen == {"url": "http://kuma2.test:3001", "key": "kuma-metrics"}


async def test_unsafe_value_is_rejected(env):
    code = await unlocked(env)
    r = await env["client"].post(
        "/api/setup/check/cloudflare",
        json={"values": {"CF_API_TOKEN": "abc def"}},
        headers={"X-Setup-Code": code},
    )
    assert r.status_code == 400
    assert "CF_API_TOKEN" in r.json()["error"]["detail"]["fields"]


async def test_unknown_key_is_rejected(env):
    code = await unlocked(env)
    r = await env["client"].post(
        "/api/setup/check/panel", json={"values": {"PD_REPO": "evil/repo"}}, headers={"X-Setup-Code": code}
    )
    assert r.status_code == 400


async def test_complete_saves_settings_roles_and_finishes(env):
    code = await unlocked(env)
    r = await env["client"].post(
        "/api/setup/complete",
        json={
            "values": {"PD_PUBLIC_URL": "https://hatch.example.com/", "DISCORD_BOT_TOKEN": "new-bot-token"},
            "roles": [ADMIN, SUPPORT, MEMBER],
        },
        headers={"X-Setup-Code": code},
    )
    assert r.status_code == 200, r.text
    saved = (env["dir"] / "setup.env").read_text(encoding="utf-8")
    assert "PD_PUBLIC_URL=https://hatch.example.com\n" in saved
    assert "DISCORD_BOT_TOKEN=new-bot-token\n" in saved
    if os.name == "posix":
        assert (env["dir"] / "setup.env").stat().st_mode & 0o077 == 0
    rules = q(env["url"], "SELECT discord_role_id, grants_role, max_servers FROM discord_role_rules ORDER BY 1")
    assert rules == [("90000", "admin", 10), ("90001", "user", 2), ("90002", "supporter", 3)]
    assert not (env["dir"] / "setup-code").exists()
    assert (env["dir"] / "restart-request").exists()
    audit = q(env["url"], "SELECT action, detail::text FROM audit_log")
    assert audit[0][0] == "初期設定を保存" and "new-bot-token" not in audit[0][1]

    r = await env["client"].get("/api/setup/status")
    assert r.json() == {"needed": False, "restarting": True}
    r = await env["client"].post("/api/setup/unlock", json={"code": code})
    assert r.status_code == 409


async def test_complete_requires_all_settings(env, monkeypatch):
    monkeypatch.delenv("CF_API_TOKEN")
    code = await unlocked(env)
    r = await env["client"].post(
        "/api/setup/complete", json={"values": {}, "roles": [ADMIN]}, headers={"X-Setup-Code": code}
    )
    assert r.status_code == 400
    assert "CF_API_TOKEN" in r.json()["error"]["detail"]["fields"]
    assert not (env["dir"] / "setup.env").exists()


async def test_complete_requires_admin_role(env):
    code = await unlocked(env)
    r = await env["client"].post(
        "/api/setup/complete", json={"values": {}, "roles": [MEMBER]}, headers={"X-Setup-Code": code}
    )
    assert r.status_code == 400
    assert "管理者" in r.json()["error"]["message"]


async def test_redo_setup_shows_current_roles_and_removes_unselected(env):
    q(
        env["url"],
        "INSERT INTO discord_role_rules (discord_role_id, label, grants_role, max_servers) "
        "VALUES ('90000', '運営', 'admin', 10), ('99999', '旧', 'user', 1) RETURNING 1",
    )
    code = await unlocked(env)
    r = await env["client"].post("/api/setup/unlock", json={"code": code})
    assert {x["id"]: x["grants"] for x in r.json()["role_rules"]} == {"90000": "admin", "99999": "user"}
    r = await env["client"].post(
        "/api/setup/complete", json={"values": {}, "roles": [ADMIN, SUPPORT]}, headers={"X-Setup-Code": code}
    )
    assert r.status_code == 200, r.text
    rules = q(env["url"], "SELECT discord_role_id, grants_role FROM discord_role_rules ORDER BY 1")
    assert rules == [("90000", "admin"), ("90002", "supporter")]


async def test_setup_reopens_when_settings_break(env, monkeypatch):
    q(env["url"], "UPDATE app_settings SET value = to_jsonb(now()) WHERE key = 'setup_completed_at' RETURNING 1")
    assert (await env["client"].get("/api/setup/status")).json()["needed"] is False
    monkeypatch.delenv("DISCORD_CLIENT_ID")
    assert (await env["client"].get("/api/setup/status")).json()["needed"] is True


async def test_other_api_explains_setup_is_required(env, monkeypatch):
    monkeypatch.delenv("DISCORD_CLIENT_ID")
    config.get_settings.cache_clear()
    r = await env["client"].get("/api/auth/login")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "setup_required"


async def test_migration_marks_existing_installs_as_completed(env):
    q(env["url"], "DELETE FROM app_settings WHERE key = 'setup_completed_at' RETURNING 1")
    q(
        env["url"],
        "INSERT INTO discord_role_rules (discord_role_id, label, grants_role) VALUES ('1', 'a', 'admin') RETURNING 1",
    )
    sql = (BASE_DIR / "db" / "migrations" / "0002_setup_state.sql").read_text(encoding="utf-8")
    with psycopg.connect(env["url"]) as c:
        c.execute(sql)
    assert (await env["client"].get("/api/setup/status")).json()["needed"] is False


def test_env_file_keeps_earlier_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PD_DATA_DIR", str(tmp_path))
    config.get_core_settings.cache_clear()
    st.write_env_file({"KUMA_URL": "http://a:3001", "PD_REPO": "ignored"})
    st.write_env_file({"CF_API_TOKEN": "t"})
    assert st.read_env_file() == {"KUMA_URL": "http://a:3001", "CF_API_TOKEN": "t"}
