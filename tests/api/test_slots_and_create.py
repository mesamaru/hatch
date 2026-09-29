"""アドレス枠の管理（T10）と、画面からの作成（T12 のルート）を API 越しに通しで確認する。"""

from __future__ import annotations

import psycopg
import pytest

from pterodeploy import db
from pterodeploy.api.deps import get_games
from pterodeploy.games import parse
from pterodeploy.jobs.deps import Deps
from pterodeploy.jobs.engine import Engine
from pterodeploy.main import app
from tests.api.helpers import client_for, make_user
from tests.fakes.dns import FakeDns
from tests.fakes.panel import FakePanel

GAMES = parse({"paper": {"kind": "mc", "nest": 1, "egg": 3, "proxy_protocol": True}})
RULE = {
    "name": "Minecraft 共有枠",
    "domain_id": 1,
    "port_start": 25560,
    "port_end": 25569,
    "host_template": "mc{nn}trt",
    "number_start": 0,
    "excluded_ports": [25560],
}


@pytest.fixture
async def env(db_url):
    await db.open_pool(db_url, max_size=6)
    with psycopg.connect(db_url) as c:
        c.execute("INSERT INTO domains (name, cf_zone_id, is_default) VALUES ('nuids.jp','z1', true)")
        c.execute("INSERT INTO ip_bindings (domain_id, host, follow_active_edge) VALUES (1, 'edge', true)")
        c.execute(
            "INSERT INTO nodes (id, panel_node_id, tailscale_ip, memory_mb, disk_mb) "
            "VALUES ('node-1', 1, '100.64.0.12', 32768, 400000)"
        )
    admin = make_user(db_url, "admin", role="admin")
    tanaka = make_user(db_url, "tanaka")
    app.dependency_overrides[get_games] = lambda: GAMES
    dns = FakeDns(instance="test", zones={"z1": "nuids.jp"})
    engine = Engine(
        Deps(panel=FakePanel(), dns=dns, games=GAMES, poll_interval=0, edge_wait=0),
        retry_delays=(0, 0, 0),
        heartbeat=3600,
    )
    ad, us = client_for(db_url, admin), client_for(db_url, tanaka)
    yield {"url": db_url, "admin": ad, "user": us, "tanaka": tanaka, "dns": dns, "engine": engine}
    await ad.aclose()
    await us.aclose()
    app.dependency_overrides.clear()
    await db.close_pool()


def q(url, sql, args=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


async def test_preview_and_create_rule(env):
    ad = env["admin"]
    r = await ad.post("/api/admin/slot-rules/preview", json=RULE)
    body = r.json()
    assert body["problems"] == []
    assert [s["fqdn"] for s in body["slots"][:2]] == ["mc00trt.nuids.jp", "mc01trt.nuids.jp"]
    assert body["slots"][0]["excluded"] is True
    with psycopg.connect(env["url"]) as c:
        c.execute("INSERT INTO ip_bindings (domain_id, host, follow_active_edge) VALUES (1, 'mc05trt', true)")
    r = await ad.post("/api/admin/slot-rules/preview", json=RULE)
    assert any("mc05trt.nuids.jp は IP の紐付け" in p for p in r.json()["problems"])
    with psycopg.connect(env["url"]) as c:
        c.execute("DELETE FROM ip_bindings WHERE host = 'mc05trt'")
    r = await ad.post("/api/admin/slot-rules", json=RULE)
    assert r.status_code == 201 and r.json()["stats"]["total"] == 9
    assert q(env["url"], "SELECT count(*) FROM slots WHERE port = 25560") == [(0,)]  # 対象外は行を作らない
    # 重なる枠は作れない
    r = await ad.post(
        "/api/admin/slot-rules",
        json={
            **RULE,
            "name": "x",
            "port_start": 25565,
            "port_end": 25570,
            "host_template": "x{port}",
            "excluded_ports": [],
        },
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "rule_invalid"


async def test_user_cannot_manage_rules(env):
    r = await env["user"].post("/api/admin/slot-rules", json=RULE)
    assert r.status_code == 403


async def test_publish_then_create_server_end_to_end(env):
    ad, us, url = env["admin"], env["user"], env["url"]
    rid = (await ad.post("/api/admin/slot-rules", json=RULE)).json()["id"]
    r = await ad.post(f"/api/admin/slot-rules/{rid}/publish")
    assert r.status_code == 202
    await env["engine"].run_once()
    assert q(url, "SELECT count(*) FROM slots WHERE dns_state='published'") == [(9,)]
    assert len(env["dns"].records["z1"]) == 18  # CNAME と SRV が9つずつ

    avail = (await us.get("/api/slots/available")).json()["items"]
    assert avail[0] == {
        "port": 25561,
        "fqdn": "mc01trt.nuids.jp",
        "dns_ready": True,
        "rule": {"id": rid, "name": "Minecraft 共有枠", "domain": "nuids.jp", "host_template": "mc{nn}trt"},
    }
    r = await us.post(
        "/api/servers",
        json={"name": "storia", "game": "paper", "plan": "light", "slot_port": 25561},
        headers={"Idempotency-Key": "abc"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job"]["id"]
    assert r.json()["server"]["address"] == "mc01trt.nuids.jp"
    # 同じ Idempotency-Key の再送は同じジョブ
    again = await us.post(
        "/api/servers",
        json={"name": "storia", "game": "paper", "plan": "light", "slot_port": 25561},
        headers={"Idempotency-Key": "abc"},
    )
    assert again.json()["job"]["id"] == job_id
    await env["engine"].run_once()
    job = (await us.get(f"/api/jobs/{job_id}")).json()
    assert job["status"] == "succeeded", job
    assert [s["status"] for s in job["steps"] if s["name"].startswith("DNS")] == ["done", "done"]
    assert len(env["dns"].records["z1"]) == 18  # 事前作成済みなので増えない
    # 使ったスロットは選択肢から消える
    assert 25561 not in [s["port"] for s in (await us.get("/api/slots/available")).json()["items"]]
    # 他人のジョブは見えない
    other = client_for(url, make_user(url, "suzuki"))
    assert (await other.get(f"/api/jobs/{job_id}")).status_code == 404
    await other.aclose()


async def test_rule_change_limits_and_delete(env):
    ad, us, url = env["admin"], env["user"], env["url"]
    rid = (await ad.post("/api/admin/slot-rules", json=RULE)).json()["id"]
    await us.post("/api/servers", json={"name": "storia", "game": "paper", "plan": "light", "slot_port": 25562})
    # 使用中の間はテンプレートを変えられない・使用中のポートを外せない
    r = await ad.patch(f"/api/admin/slot-rules/{rid}", json={**RULE, "host_template": "x{nn}"})
    assert r.status_code == 400 and any("変更できません" in p for p in r.json()["error"]["detail"]["problems"])
    r = await ad.patch(f"/api/admin/slot-rules/{rid}", json={**RULE, "excluded_ports": [25560, 25562]})
    assert r.status_code == 400
    # 範囲を広げるのは可
    r = await ad.patch(f"/api/admin/slot-rules/{rid}", json={**RULE, "port_end": 25579})
    assert r.status_code == 200 and r.json()["changes"]["added"] == 10 and r.json()["needs_republish"] is True
    assert (await ad.delete(f"/api/admin/slot-rules/{rid}")).status_code == 409
    # スロットの停止（使用中は不可）
    assert (await ad.patch("/api/admin/slots/25563", json={"status": "disabled"})).status_code == 200
    assert (await ad.patch("/api/admin/slots/25562", json={"status": "disabled"})).status_code == 409
    assert 25563 not in [s["port"] for s in (await us.get("/api/slots/available")).json()["items"]]
    assert q(url, "SELECT action FROM audit_log ORDER BY id")[0] == ("アドレス枠を追加",)


async def test_unpublish_keeps_records_of_used_slots(env):
    ad, us, url = env["admin"], env["user"], env["url"]
    rid = (await ad.post("/api/admin/slot-rules", json=RULE)).json()["id"]
    await ad.post(f"/api/admin/slot-rules/{rid}/publish")
    await env["engine"].run_once()
    await us.post("/api/servers", json={"name": "storia", "game": "paper", "plan": "light", "slot_port": 25561})
    await env["engine"].run_once()
    await ad.post(f"/api/admin/slot-rules/{rid}/unpublish")
    await env["engine"].run_once()
    assert env["dns"].names() == {("CNAME", "mc01trt.nuids.jp"), ("SRV", "_minecraft._tcp.mc01trt.nuids.jp")}
    assert q(url, "SELECT port FROM slots WHERE dns_state='published'") == [(25561,)]
