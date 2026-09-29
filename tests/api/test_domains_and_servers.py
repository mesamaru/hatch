"""ドメイン・IP・紐付け（T09）と、サーバーの一覧・詳細・操作（T13）。"""

from __future__ import annotations

import psycopg
import pytest

from pterodeploy import db
from pterodeploy.api.deps import get_dns, get_games, get_panel
from pterodeploy.errors import TransientError
from pterodeploy.games import parse
from pterodeploy.jobs.deps import Deps
from pterodeploy.jobs.engine import Engine
from pterodeploy.main import app
from tests.api.helpers import client_for, make_user
from tests.fakes.dns import FakeDns
from tests.fakes.panel import FakePanel

ZONE = "a" * 32
GAMES = parse({"paper": {"kind": "mc", "nest": 1, "egg": 3, "proxy_protocol": True}})


@pytest.fixture
async def env(db_url):
    await db.open_pool(db_url, max_size=6)
    with psycopg.connect(db_url) as c:
        c.execute(
            "INSERT INTO edges (id, public_ip, tailscale_ip, is_active) "
            "VALUES ('edge-1','203.0.113.10','100.64.1.1', true)"
        )
        # edge エージェントの代わり：どの版も適用済みとして扱う
        c.execute("UPDATE edges SET applied_version = 1000000000")
        c.execute(
            "INSERT INTO nodes (id, panel_node_id, tailscale_ip, memory_mb, disk_mb) "
            "VALUES ('node-1', 1, '100.64.0.12', 32768, 400000)"
        )
    dns = FakeDns(instance="test", zones={ZONE: "nuids.jp"})
    panel = FakePanel()
    app.dependency_overrides[get_dns] = lambda: dns
    app.dependency_overrides[get_panel] = lambda: panel
    app.dependency_overrides[get_games] = lambda: GAMES
    engine = Engine(
        Deps(panel=panel, dns=dns, games=GAMES, poll_interval=0, edge_wait=0), retry_delays=(0, 0, 0), heartbeat=3600
    )
    admin, tanaka, suzuki = (
        make_user(db_url, "admin", role="admin"),
        make_user(db_url, "tanaka"),
        make_user(db_url, "suzuki"),
    )
    cl = {
        "admin": client_for(db_url, admin),
        "tanaka": client_for(db_url, tanaka),
        "suzuki": client_for(db_url, suzuki),
    }
    yield {
        "url": db_url,
        "dns": dns,
        "panel": panel,
        "engine": engine,
        "ids": {"tanaka": tanaka, "suzuki": suzuki},
        **cl,
    }
    for c in cl.values():
        await c.aclose()
    app.dependency_overrides.clear()
    await db.close_pool()


def q(url, sql, args=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


async def add_domain(env):
    r = await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    assert r.status_code == 201, r.text
    await env["engine"].run_once()
    return r.json()


# ---------------- T09 ----------------
async def test_add_domain_creates_edge_record(env):
    d = await add_domain(env)
    assert d["is_default"] is True
    assert env["dns"].names(ZONE) == {("A", "edge.nuids.jp")}
    rec = next(iter(env["dns"].records[ZONE].values()))
    assert rec["content"] == "203.0.113.10" and rec["comment"] == f"pterodeploy:test binding={d['edge_binding_id']}"
    assert q(env["url"], "SELECT synced_at IS NOT NULL FROM ip_bindings") == [(True,)]


async def test_zone_mismatch_and_duplicates(env):
    other = "b" * 32
    env["dns"].zones[other] = "example.net"
    env["dns"].records[other] = {}
    r = await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": other})
    assert r.json()["error"]["code"] == "zone_mismatch"
    r = await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": "c" * 32})
    assert r.json()["error"]["code"] == "zone_unreachable"
    await add_domain(env)
    r = await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    assert r.status_code == 409


async def test_bindings_and_ips(env):
    await add_domain(env)
    ad = env["admin"]
    r = await ad.post("/api/admin/ips", json={"label": "予備", "address": "10.0.0.1"})
    assert r.status_code == 400  # 非公開の IP は不可
    ip = (await ad.post("/api/admin/ips", json={"label": "予備", "address": "139.162.10.20"})).json()
    r = await ad.post("/api/admin/bindings", json={"domain_id": 1, "host": "panel", "ip_id": ip["id"]})
    assert r.status_code == 202
    await env["engine"].run_once()
    assert ("A", "panel.nuids.jp") in env["dns"].names(ZONE)
    assert (await ad.delete(f"/api/admin/ips/{ip['id']}")).status_code == 409  # 使用中
    bid = r.json()["id"]
    await ad.delete(f"/api/admin/bindings/{bid}")
    await env["engine"].run_once()
    assert ("A", "panel.nuids.jp") not in env["dns"].names(ZONE)
    assert (await ad.delete(f"/api/admin/ips/{ip['id']}")).status_code == 204
    # 紐付けとアドレス枠のホスト名の重複
    await ad.post(
        "/api/admin/slot-rules",
        json={"name": "mc", "domain_id": 1, "port_start": 25560, "port_end": 25569, "host_template": "mc{nn}trt"},
    )
    r = await ad.post("/api/admin/bindings", json={"domain_id": 1, "host": "mc03trt", "follow_active_edge": True})
    assert r.status_code == 409
    # アドレス枠が向いている edge の紐付けは消せない。ドメインも消せない
    edge_bid = q(env["url"], "SELECT id FROM ip_bindings WHERE host='edge'")[0][0]
    assert (await ad.delete(f"/api/admin/bindings/{edge_bid}")).status_code == 409
    assert (await ad.delete("/api/admin/domains/1")).status_code == 409


async def test_user_cannot_touch_domains(env):
    assert (await env["tanaka"].get("/api/admin/domains")).status_code == 403


# ---------------- T13 ----------------
async def create(env, who="tanaka", name="storia", port=25561):
    r = await env[who].post("/api/servers", json={"name": name, "game": "paper", "plan": "light", "slot_port": port})
    assert r.status_code == 202, r.text
    await env["engine"].run_once()
    return r.json()["server"]["id"]


@pytest.fixture
async def with_rule(env):
    await add_domain(env)
    await env["admin"].post(
        "/api/admin/slot-rules",
        json={
            "name": "mc",
            "domain_id": 1,
            "port_start": 25560,
            "port_end": 25569,
            "host_template": "mc{nn}trt",
            "excluded_ports": [25560],
        },
    )
    return env


async def test_list_and_detail_visibility(with_rule):
    env = with_rule
    sid = await create(env)
    await create(env, "suzuki", "rpg", 25562)
    mine = (await env["tanaka"].get("/api/servers")).json()["items"]
    assert [s["name"] for s in mine] == ["storia"]
    s = mine[0]
    assert s["address"] == "mc01trt.nuids.jp" and s["direct"] == "edge.nuids.jp:25561"
    assert s["status"] == "running" and s["my_permission"] == "owner" and s["job"] is None
    assert len((await env["admin"].get("/api/servers")).json()["items"]) == 2
    assert (await env["tanaka"].get("/api/servers", params={"owner": env["ids"]["suzuki"]})).status_code == 403
    # 名前でも ID でも引ける。他人のサーバーは存在を知らせない
    assert (await env["tanaka"].get("/api/servers/storia")).json()["id"] == sid
    assert (await env["tanaka"].get(f"/api/servers/{sid}")).status_code == 200
    assert (await env["tanaka"].get("/api/servers/rpg")).status_code == 404
    # 共有すると見えるようになる（閲覧のみなら操作はできない）
    with psycopg.connect(env["url"]) as c:
        c.execute(
            "INSERT INTO server_shares (server_id, user_id, permission) "
            "SELECT id, %s, 'view' FROM servers WHERE name='rpg'",
            (env["ids"]["tanaka"],),
        )
    r = (await env["tanaka"].get("/api/servers/rpg")).json()
    assert r["my_permission"] == "view"
    assert (await env["tanaka"].post("/api/servers/rpg/power", json={"signal": "stop"})).status_code == 403


async def test_power_and_maintenance(with_rule):
    env = with_rule
    await create(env)
    t = env["tanaka"]
    assert (await t.post("/api/servers/storia/power", json={"signal": "stop"})).status_code == 204
    assert (await t.get("/api/servers/storia")).json()["status"] == "stopped"
    assert next(iter(env["panel"].states.values())) == "offline"
    assert (await t.post("/api/servers/storia/power", json={"signal": "start"})).status_code == 204
    r = await t.post("/api/servers/storia/maintenance", json={"enabled": True, "hours": 1})
    assert r.json()["status"] == "maintenance" and r.json()["maintenance_until"]
    assert (await t.post("/api/servers/storia/power", json={"signal": "stop"})).status_code == 409
    assert (await t.post("/api/servers/storia/maintenance", json={"enabled": False})).json()["status"] == "running"
    r = await t.patch("/api/servers/storia", json={"auto_restart": False, "public_status": True})
    assert r.json()["auto_restart"] is False and r.json()["public_status"] is True
    acts = [a for (a,) in q(env["url"], "SELECT action FROM audit_log WHERE server_id IS NOT NULL ORDER BY id")]
    assert acts[-5:] == ["停止", "起動", "メンテナンスを開始", "メンテナンスを終了", "設定を変更"]


async def test_resources_and_panel_errors(with_rule):
    env = with_rule
    await create(env)
    r = (await env["tanaka"].get("/api/servers/storia/resources")).json()
    assert r["state"] == "running" and r["memory_limit"] == 2048 * 1024 * 1024
    env["panel"].fail["resources"] = TransientError("ゲームパネル", "503")
    r = await env["tanaka"].get("/api/servers/storia/resources")
    assert r.status_code == 504 and r.json()["error"]["code"] == "upstream_timeout"


async def test_suspended_server_read_only(with_rule):
    env = with_rule
    await create(env)
    with psycopg.connect(env["url"]) as c:
        c.execute("UPDATE servers SET status='suspended', suspend_reason='規約違反'")
    t = env["tanaka"]
    assert (await t.get("/api/servers", params={"status": "suspended"})).json()["items"][0][
        "suspend_reason"
    ] == "規約違反"
    r = await t.post("/api/servers/storia/power", json={"signal": "start"})
    assert r.json()["error"]["code"] == "server_suspended"
