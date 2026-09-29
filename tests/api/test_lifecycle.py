"""ゴミ箱・元に戻す・完全に削除（T14）。"""

from __future__ import annotations

import psycopg
import pytest

from hatch import db
from hatch.errors import UpstreamError
from hatch.scheduler import enqueue_due_purges
from tests.api.test_domains_and_servers import env  # noqa: F401 - フィクスチャを共有

pytestmark = pytest.mark.usefixtures("env")


def q(url, sql, args=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


@pytest.fixture
async def ready(env):
    ad = env["admin"]
    r = await ad.post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": "a" * 32})
    assert r.status_code == 201
    await env["engine"].run_once()
    # 固定ホスト名（事前作成あり）と、サーバー名の枠
    rid = (
        await ad.post(
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
    ).json()["id"]
    await ad.post(f"/api/admin/slot-rules/{rid}/publish")
    await env["engine"].run_once()
    await ad.post(
        "/api/admin/slot-rules",
        json={
            "name": "named",
            "domain_id": 1,
            "port_start": 30010,
            "port_end": 30019,
            "host_template": "{server}",
            "prepublish": False,
        },
    )
    return env


async def run_all(env):
    while await env["engine"].run_once():
        pass


async def make(env, name, port):
    r = await env["tanaka"].post(
        "/api/servers", json={"name": name, "game": "paper", "plan": "light", "slot_port": port}
    )
    assert r.status_code == 202, r.text
    await run_all(env)


async def test_trash_restore_named_server(ready):
    env, t, url = ready, ready["tanaka"], ready["url"]
    await make(env, "storia", 30011)
    assert ("CNAME", "storia.nuids.jp") in env["dns"].names("a" * 32)
    assert (await t.delete("/api/servers/storia")).status_code == 202
    await run_all(env)
    s = q(url, "SELECT status, exposed, purge_after > now() + interval '71 hours' FROM servers WHERE name='storia'")
    assert s == [("trashed", False, True)]
    assert q(url, "SELECT status FROM slots WHERE port=30011") == [("held",)]
    assert ("CNAME", "storia.nuids.jp") not in env["dns"].names("a" * 32)
    cfg = q(url, "SELECT haproxy_cfg FROM edge_config_versions ORDER BY version DESC LIMIT 1")[0][0]
    assert "fe_30011" not in cfg
    # ゴミ箱の間は名前もアドレスも使えない
    r = await t.post("/api/servers", json={"name": "storia", "game": "paper", "plan": "light", "slot_port": 30012})
    assert r.json()["error"]["code"] == "name_taken"
    assert 30011 not in [x["port"] for x in (await t.get("/api/slots/available")).json()["items"]]
    # 一覧には出ず、status=trashed で出る
    assert (await t.get("/api/servers")).json()["items"] == []
    assert len((await t.get("/api/servers", params={"status": "trashed"})).json()["items"]) == 1
    # 元に戻す
    assert (await t.post("/api/servers/storia/restore")).status_code == 202
    await run_all(env)
    assert q(url, "SELECT status, exposed, trashed_at FROM servers WHERE name='storia'") == [("running", True, None)]
    assert q(url, "SELECT status FROM slots WHERE port=30011") == [("assigned",)]
    assert ("CNAME", "storia.nuids.jp") in env["dns"].names("a" * 32)


async def test_prepublished_dns_survives_trash_and_purge(ready):
    env, t, url = ready, ready["tanaka"], ready["url"]
    await make(env, "lobby", 25561)
    before = len(env["dns"].records["a" * 32])
    await t.delete("/api/servers/lobby")
    await run_all(env)
    assert len(env["dns"].records["a" * 32]) == before
    r = await t.post("/api/servers/lobby/purge", json={"confirm_name": "wrong"})
    assert r.json()["error"]["code"] == "confirm_mismatch"
    assert (await t.post("/api/servers/lobby/purge", json={"confirm_name": "lobby"})).status_code == 202
    await run_all(env)
    assert q(url, "SELECT status FROM servers WHERE name='lobby'") == [("purged",)]
    assert q(url, "SELECT status, server_id FROM slots WHERE port=25561") == [("free", None)]
    assert env["panel"].servers == {} and env["panel"].allocations == {}
    assert len(env["dns"].records["a" * 32]) == before  # 事前作成のレコードは枠に残る
    # 名前とアドレスがすぐ使える
    await make(env, "lobby", 25561)
    assert q(url, "SELECT count(*) FROM servers WHERE name='lobby' AND status='running'") == [(1,)]


async def test_trash_failure_rolls_back(ready):
    env, t, url = ready, ready["tanaka"], ready["url"]
    await make(env, "storia", 30011)
    env["dns"].fail_next = []
    orig = env["dns"].delete

    async def boom(*a, **k):
        raise UpstreamError("Cloudflare", "boom", 400)

    env["dns"].delete = boom
    await t.delete("/api/servers/storia")
    await run_all(env)
    env["dns"].delete = orig
    assert q(url, "SELECT status FROM jobs WHERE kind='trash'") == [("rolled_back",)]
    assert q(url, "SELECT status, exposed FROM servers WHERE name='storia'") == [("running", True)]
    assert q(url, "SELECT status FROM slots WHERE port=30011") == [("assigned",)]
    assert next(iter(env["panel"].states.values())) == "running"  # 停止も元に戻る


async def test_auto_purge_after_deadline(ready):
    env, t, url = ready, ready["tanaka"], ready["url"]
    await make(env, "storia", 30011)
    await t.delete("/api/servers/storia")
    await run_all(env)
    async with db.transaction() as conn:
        assert await enqueue_due_purges(conn) == []
    with psycopg.connect(url) as c:
        c.execute("UPDATE servers SET purge_after = now() - interval '1 minute'")
    async with db.transaction() as conn:
        first = await enqueue_due_purges(conn)
    async with db.transaction() as conn:
        assert await enqueue_due_purges(conn) == []  # 二重に登録しない
    assert len(first) == 1
    await run_all(env)
    assert q(url, "SELECT status FROM servers WHERE name='storia'") == [("purged",)]
    assert q(url, "SELECT via FROM jobs WHERE kind='purge'") == [("system",)]


async def test_permissions(ready):
    env = ready
    await make(env, "storia", 30011)
    assert (await env["suzuki"].delete("/api/servers/storia")).status_code == 404
    assert (await env["tanaka"].post("/api/servers/storia/restore")).status_code == 409  # ゴミ箱に無い
    assert (await env["tanaka"].post("/api/servers/storia/purge", json={"confirm_name": "storia"})).status_code == 409
