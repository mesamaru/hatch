"""初期設定画面の接続確認（docs/TASKS.md T41）。通信は respx で置き換える。"""

from __future__ import annotations

import httpx
import respx

from hatch.adapters.setup_checks import (
    CF_API,
    DISCORD_API,
    check_cloudflare,
    check_discord,
    check_kuma,
    check_panel,
)

PANEL = "https://gp.test"


@respx.mock
async def test_panel_ok():
    respx.get(f"{PANEL}/api/application/users").mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(f"{PANEL}/api/client/account").mock(
        return_value=httpx.Response(200, json={"attributes": {"admin": True}})
    )
    r = await check_panel(PANEL, "ptla_x", "ptlc_x")
    assert r.ok


@respx.mock
async def test_panel_client_key_must_be_admin():
    respx.get(f"{PANEL}/api/application/users").mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(f"{PANEL}/api/client/account").mock(
        return_value=httpx.Response(200, json={"attributes": {"admin": False}})
    )
    r = await check_panel(PANEL, "ptla_x", "ptlc_x")
    assert not r.ok and "管理者" in r.message


@respx.mock
async def test_panel_bad_app_key_and_secret_not_in_message():
    respx.get(f"{PANEL}/api/application/users").mock(return_value=httpx.Response(401, json={}))
    r = await check_panel(PANEL, "ptla_secret_value", "ptlc_x")
    assert not r.ok and "Application API" in r.message
    assert "ptla_secret_value" not in r.message


@respx.mock
async def test_panel_unreachable():
    respx.get(f"{PANEL}/api/application/users").mock(side_effect=httpx.ConnectError("x"))
    r = await check_panel(PANEL, "a", "b")
    assert not r.ok and "接続できません" in r.message


@respx.mock
async def test_cloudflare_lists_zones():
    respx.get(f"{CF_API}/user/tokens/verify").mock(
        return_value=httpx.Response(200, json={"result": {"status": "active"}})
    )
    respx.get(f"{CF_API}/zones").mock(return_value=httpx.Response(200, json={"result": [{"name": "example.com"}]}))
    r = await check_cloudflare("tok")
    assert r.ok and r.data["zones"] == ["example.com"]


@respx.mock
async def test_cloudflare_without_zones_fails():
    respx.get(f"{CF_API}/user/tokens/verify").mock(
        return_value=httpx.Response(200, json={"result": {"status": "active"}})
    )
    respx.get(f"{CF_API}/zones").mock(return_value=httpx.Response(200, json={"result": []}))
    r = await check_cloudflare("tok")
    assert not r.ok


@respx.mock
async def test_kuma_bad_key():
    respx.get("http://kuma.test:3001/metrics").mock(return_value=httpx.Response(401))
    r = await check_kuma("http://kuma.test:3001", "k")
    assert not r.ok and "API キー" in r.message


def _discord_ok_token():
    respx.post(f"{DISCORD_API}/oauth2/token").mock(return_value=httpx.Response(200, json={"access_token": "a"}))
    respx.get(f"{DISCORD_API}/users/@me").mock(return_value=httpx.Response(200, json={"id": "123"}))


@respx.mock
async def test_discord_bot_not_invited_returns_invite_url():
    _discord_ok_token()
    respx.get(f"{DISCORD_API}/users/@me/guilds").mock(return_value=httpx.Response(200, json=[]))
    r = await check_discord("123", "sec", "bot")
    assert not r.ok
    assert "client_id=123" in r.data["invite_url"]


@respx.mock
async def test_discord_lists_roles_and_text_channels():
    _discord_ok_token()
    respx.get(f"{DISCORD_API}/users/@me/guilds").mock(
        return_value=httpx.Response(200, json=[{"id": "456", "name": "サーバー"}])
    )
    respx.get(f"{DISCORD_API}/guilds/456/roles").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "456", "name": "@everyone", "position": 0},
                {"id": "900", "name": "運営", "position": 2},
                {"id": "901", "name": "メンバー", "position": 1},
                {"id": "902", "name": "Hatch", "position": 3, "managed": True},
            ],
        )
    )
    respx.get(f"{DISCORD_API}/guilds/456/channels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "10", "name": "お知らせ", "type": 0, "position": 1},
                {"id": "11", "name": "ボイス", "type": 2, "position": 2},
            ],
        )
    )
    r = await check_discord("123", "sec", "bot", "456")
    assert r.ok
    assert [x["id"] for x in r.data["roles"]] == ["900", "901"]
    assert [x["id"] for x in r.data["channels"]] == ["10"]


@respx.mock
async def test_discord_bad_client_secret():
    respx.post(f"{DISCORD_API}/oauth2/token").mock(return_value=httpx.Response(401, json={}))
    r = await check_discord("123", "bad", "bot")
    assert not r.ok and "シークレット" in r.message
