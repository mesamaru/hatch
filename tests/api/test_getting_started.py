"""はじめの設定の進み具合（docs/TASKS.md T48）。"""

# ruff: noqa: F811 - 別のテストのフィクスチャ（env）を使うため

from __future__ import annotations

import psycopg

from tests.api.test_domains_and_servers import ZONE, env  # noqa: F401 - fixture


async def drain(env):
    while await env["engine"].run_once() is not None:
        pass


async def status(env) -> dict:
    r = await env["admin"].get("/api/admin/getting-started")
    assert r.status_code == 200, r.text
    return r.json()


def states(s: dict) -> dict:
    return {x["key"]: x["state"] for x in s["steps"]}


def no_edges(env):
    with psycopg.connect(env["url"]) as c:
        c.execute("DELETE FROM edges")


async def add_rule(env) -> int:
    r = await env["admin"].post(
        "/api/admin/slot-rules",
        json={"name": "mc", "domain_id": 1, "port_start": 25560, "port_end": 25569, "host_template": "mc{nn}trt"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_nothing_registered(env):
    no_edges(env)
    s = await status(env)
    assert [x["key"] for x in s["steps"]] == ["linode", "firewall", "edge", "domain", "rule", "dns"]
    assert states(s) == {
        "linode": "todo",
        "firewall": "locked",
        "edge": "todo",
        "domain": "locked",
        "rule": "locked",
        "dns": "locked",
    }
    assert (s["done"], s["total"], s["complete"]) == (0, 4, False)
    assert (await env["tanaka"].get("/api/admin/getting-started")).status_code == 403


async def test_steps_in_order_until_complete(env):
    # テストの env には Linode を使わない edge が登録済み
    s = await status(env)
    assert states(s)["linode"] == "skipped" and states(s)["firewall"] == "skipped"
    assert states(s)["edge"] == "done" and states(s)["domain"] == "todo"
    assert s["steps"][2]["detail"] == "edge-1（203.0.113.10）"

    await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    assert states(await status(env))["domain"] == "working"
    await drain(env)
    s = await status(env)
    assert states(s)["domain"] == "done" and states(s)["rule"] == "todo"

    rid = await add_rule(env)
    s = await status(env)
    dns = s["steps"][5]
    assert dns["state"] == "todo" and dns["retry"] == {"rules": [rid]} and dns["detail"] == "mc"
    assert s["complete"] is False

    assert (await env["admin"].post(f"/api/admin/slot-rules/{rid}/publish")).status_code == 202
    assert states(await status(env))["dns"] == "working"
    await drain(env)
    s = await status(env)
    assert all(x["state"] == "done" for x in s["steps"] if x["required"])
    assert (s["done"], s["total"], s["complete"]) == (4, 4, True)


async def test_domain_before_edge_is_reported_and_recovers(env):
    no_edges(env)
    r = await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    assert r.status_code == 201, r.text
    await drain(env)  # edge が無いので A レコードを作れずに失敗する
    s = await status(env)
    dom = s["steps"][3]
    assert dom["state"] == "error" and dom["detail"] == "nuids.jp"
    assert "edge.nuids.jp" in dom["problem"] and "edge を登録" in dom["problem"]
    (bid,) = dom["retry"]["bindings"]

    # edge を登録すると A レコードが自動で作り直される
    r = await env["admin"].post(
        "/api/admin/edges", json={"id": "edge-1", "public_ip": "203.0.113.10", "tailscale_ip": "100.64.1.1"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["dns_jobs"]
    await drain(env)
    assert states(await status(env))["domain"] == "done"

    # やり直し（retry.bindings）の先が使えること
    assert (await env["admin"].post(f"/api/admin/bindings/{bid}/sync")).status_code == 202


async def test_rule_without_prepublish_needs_no_dns(env):
    await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    await drain(env)
    r = await env["admin"].post(
        "/api/admin/slot-rules",
        json={
            "name": "mc",
            "domain_id": 1,
            "port_start": 25560,
            "port_end": 25569,
            "host_template": "mc{nn}trt",
            "prepublish": False,
        },
    )
    assert r.status_code == 201, r.text
    s = await status(env)
    assert s["steps"][5]["state"] == "done" and s["complete"] is True


def test_failed_dns_shows_reason_and_retry():
    from hatch.api.getting_started import compute

    d = {
        "accounts": [],
        "firewalls": [],
        "edges": [{"id": "edge-1", "ip": "203.0.113.10", "is_active": True}],
        "domains": [{"name": "nuids.jp"}],
        "bindings": [],
        "rules": [
            {
                "id": 7,
                "name": "mc",
                "prepublish": True,
                "host_template": "mc{nn}trt",
                "unpublished": 9,
                "errors": 0,
                "dns_error": None,
                "job_status": "rolled_back",
                "job_error": "DNS を編集する権限がありません。",
            },
            # サーバー名を使う枠は事前作成しないので対象外
            {
                "id": 8,
                "name": "srv",
                "prepublish": True,
                "host_template": "{server}",
                "unpublished": 5,
                "errors": 0,
                "dns_error": None,
                "job_status": None,
                "job_error": None,
            },
        ],
    }
    dns = compute(d)["steps"][5]
    assert dns["state"] == "error" and dns["retry"] == {"rules": [7]}
    assert "権限がありません" in dns["problem"] and "「mc」" in dns["problem"]


async def test_domain_can_be_removed_with_its_edge_record(env):
    """やり直しのため、edge.<ドメイン> の紐付けが残っていてもドメインを外せる（A レコードも消える）。"""
    await env["admin"].post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    await drain(env)
    assert env["dns"].names(ZONE) == {("A", "edge.nuids.jp")}
    r = await env["admin"].delete("/api/admin/domains/1")
    assert r.status_code == 202 and r.json()["job"]["kind"] == "delete_domain", r.text
    await drain(env)
    assert env["dns"].names(ZONE) == set()
    assert (await env["admin"].get("/api/admin/domains")).json()["items"] == []
    assert states(await status(env))["domain"] == "todo"


async def test_screen_files_are_revalidated_after_update(env):
    """更新後に古い画面が残らないよう、画面のファイルは毎回確かめさせる。"""
    for path in ("/js/app.js", "/css/components.css", "/admin/start"):
        r = await env["admin"].get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path
