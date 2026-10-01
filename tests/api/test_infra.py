"""Linode のアカウント・edge・ファイアウォール（docs/TASKS.md T43）。"""

# ruff: noqa: F811 - 別のテストのフィクスチャ（env・with_rule）を使うため

from __future__ import annotations

import pytest

from hatch.api.infra import get_linode_factory
from hatch.main import app
from tests.api.test_domains_and_servers import ZONE, add_domain, create, env, q, with_rule  # noqa: F401 - fixture
from tests.fakes.linode import FakeLinode, FakeLinodeCloud, hatch_ports

TOKEN = "linode-token-0123456789abcdef"


async def drain(env):
    """待っているジョブをすべて実行する（同期のジョブが先に並んでいても、確かめたいジョブまで進める）。"""
    while await env["engine"].run_once() is not None:
        pass


@pytest.fixture
def cloud(env):
    c = FakeLinodeCloud(TOKEN)
    factory = lambda token: FakeLinode(c, token)  # noqa: E731
    app.dependency_overrides[get_linode_factory] = lambda: factory
    env["engine"].deps.linode = factory
    return c


async def register(env, cloud, *, edge="edge-1"):
    ad = env["admin"]
    r = await ad.post("/api/admin/linode-accounts", json={"label": "契約A", "token": TOKEN})
    assert r.status_code == 201, r.text
    assert TOKEN not in r.text
    acc = r.json()["id"]
    fws = (await ad.get(f"/api/admin/linode-accounts/{acc}/firewalls")).json()["items"]
    assert fws == [{"id": 501, "label": "hatch-edge", "status": "enabled", "managed_id": None}]
    r = await ad.post("/api/admin/firewalls", json={"linode_account_id": acc, "linode_firewall_id": 501})
    assert r.status_code == 201, r.text
    fw = r.json()["id"]
    # 既存の edge（テストの env が作ったもの）に Linode とファイアウォールを付ける
    r = await ad.patch(f"/api/admin/edges/{edge}", json={"linode_account_id": acc, "linode_id": 101, "firewall_id": fw})
    assert r.status_code == 200, r.text
    await drain(env)
    return acc, fw


async def test_account_token_is_never_returned(env, cloud):
    await register(env, cloud)
    listed = await env["admin"].get("/api/admin/linode-accounts")
    assert TOKEN not in listed.text and listed.json()["items"][0]["edges"] == 1
    assert q(env["url"], "SELECT token_enc <> %s FROM linode_accounts", (TOKEN,)) == [(True,)]
    assert TOKEN not in str(q(env["url"], "SELECT detail::text, target FROM audit_log"))
    assert (await env["tanaka"].get("/api/admin/linode-accounts")).status_code == 403
    # edge の Linode がファイアウォールに付く
    assert cloud.fw[501]["devices"] == {101}


async def test_wrong_token_is_rejected(env, cloud):
    r = await env["admin"].post("/api/admin/linode-accounts", json={"label": "x", "token": "t" * 30})
    assert r.status_code >= 400


async def test_ports_open_while_server_exists_and_close_on_purge(with_rule, cloud):
    env = with_rule
    await register(env, cloud)
    await create(env)  # 25561
    await create(env, "suzuki", "rpg", 25562)
    await drain(env)
    assert hatch_ports(cloud) == {"tcp": "25561-25562"}
    # 手で作った SSH のルールは残る
    assert cloud.fw[501]["rules"]["inbound"][0]["label"] == "ssh"

    # ゴミ箱の間は開いたまま
    assert (await env["tanaka"].delete("/api/servers/storia")).status_code == 202
    await drain(env)
    assert hatch_ports(cloud) == {"tcp": "25561-25562"}

    # 完全に削除すると締まる
    r = await env["tanaka"].post("/api/servers/storia/purge", json={"confirm_name": "storia"})
    assert r.status_code == 202
    await drain(env)
    assert hatch_ports(cloud) == {"tcp": "25562"}
    assert q(env["url"], "SELECT last_error FROM firewalls") == [(None,)]


async def test_shared_and_separate_firewalls(with_rule, cloud):
    env = with_rule
    acc, fw = await register(env, cloud)
    cloud.fw[502] = {"label": "edge-2-fw", "rules": {"inbound": [], "outbound": []}, "devices": set()}
    cloud.instances.append(type(cloud.instances[0])(102, "edge-2", "ap-west", ("45.33.2.20",)))
    ad = env["admin"]
    # 共有：edge-2 も同じファイアウォール
    r = await ad.post(
        "/api/admin/edges",
        json={
            "id": "edge-2",
            "public_ip": "45.33.2.20",
            "tailscale_ip": "100.64.1.2",
            "linode_account_id": acc,
            "linode_id": 102,
            "firewall_id": fw,
        },
    )
    assert r.status_code == 201, r.text
    assert cloud.fw[501]["devices"] == {101, 102}
    # 個別：edge-2 を別のファイアウォールに
    fw2 = (await ad.post("/api/admin/firewalls", json={"linode_account_id": acc, "linode_firewall_id": 502})).json()[
        "id"
    ]
    assert (await ad.patch("/api/admin/edges/edge-2", json={"firewall_id": fw2})).status_code == 200
    await create(env)
    await drain(env)
    assert hatch_ports(cloud, 501) == {"tcp": "25561"} and hatch_ports(cloud, 502) == {"tcp": "25561"}
    # 管理をやめると Hatch のルールだけ消える（使っている edge があるうちは不可）
    assert (await ad.delete(f"/api/admin/firewalls/{fw2}")).status_code == 409
    assert (await ad.patch("/api/admin/edges/edge-2", json={"clear_firewall": True})).status_code == 200
    assert (await ad.delete(f"/api/admin/firewalls/{fw2}")).status_code == 204
    assert hatch_ports(cloud, 502) == {}
    # 使っているアカウントは消せない
    assert (await ad.delete(f"/api/admin/linode-accounts/{acc}")).status_code == 409


async def test_edge_must_use_firewall_of_same_account(env, cloud):
    await register(env, cloud)
    other = FakeLinodeCloud("other-token-0123456789abcdef")
    app.dependency_overrides[get_linode_factory] = lambda: (
        lambda token: FakeLinode(cloud if token == TOKEN else other, token)
    )
    r = await env["admin"].post("/api/admin/linode-accounts", json={"label": "契約B", "token": other.token})
    acc2 = r.json()["id"]
    r = await env["admin"].patch("/api/admin/edges/edge-1", json={"linode_account_id": acc2, "linode_id": 101})
    assert r.status_code == 400  # ファイアウォールは契約A のもの


async def test_too_many_rules_fails_deploy_and_records_reason(with_rule, cloud):
    env = with_rule
    await register(env, cloud)
    cloud.fw[501]["rules"]["inbound"] += [
        {"label": f"m{i}", "action": "ACCEPT", "protocol": "TCP", "ports": str(1000 + i), "addresses": {}}
        for i in range(24)
    ]
    r = await env["tanaka"].post(
        "/api/servers", json={"name": "storia", "game": "paper", "plan": "light", "slot_port": 25561}
    )
    await drain(env)
    job = (await env["tanaka"].get(f"/api/jobs/{r.json()['job']['id']}")).json()
    assert job["status"] in ("rolled_back", "failed"), job
    assert "上限" in (job["error"] or "")
    # 取り消し後の反映では、このサーバーのポートは開かない（手で作ったルールはそのまま）
    assert hatch_ports(cloud) == {} and len(cloud.fw[501]["rules"]["inbound"]) == 25


async def test_registering_first_edge_fixes_edge_record(env):
    """edge を登録する前にドメインを追加すると edge.<ドメイン> は作れないが、edge を登録すると自動で作り直す。"""
    q(env["url"], "DELETE FROM edges RETURNING 1")
    ad = env["admin"]
    r = await ad.post("/api/admin/domains", json={"name": "nuids.jp", "cf_zone_id": ZONE})
    assert r.status_code == 201, r.text
    await drain(env)
    assert ("A", "edge.nuids.jp") not in env["dns"].names(ZONE)
    assert q(env["url"], "SELECT status FROM jobs WHERE kind = 'sync_binding'") == [("failed",)] or q(
        env["url"], "SELECT status FROM jobs WHERE kind = 'sync_binding'"
    ) == [("rolled_back",)]

    # Linode 以外の edge として登録（最初の edge なので使用中になる）
    r = await ad.post(
        "/api/admin/edges", json={"id": "edge-1", "public_ip": "45.33.1.10", "tailscale_ip": "100.64.1.1"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_active"] is True and len(r.json()["dns_jobs"]) == 1
    await drain(env)
    rec = next(x for x in env["dns"].records[ZONE].values() if x["name"] == "edge.nuids.jp")
    assert rec["type"] == "A" and rec["content"] == "45.33.1.10"

    # 公開 IP を変えると追従する
    r = await ad.patch("/api/admin/edges/edge-1", json={"public_ip": "45.33.1.11"})
    assert len(r.json()["dns_jobs"]) == 1
    await drain(env)
    rec = next(x for x in env["dns"].records[ZONE].values() if x["name"] == "edge.nuids.jp")
    assert rec["content"] == "45.33.1.11"

    # 手動で反映し直すこともできる
    bid = q(env["url"], "SELECT id FROM ip_bindings WHERE host = 'edge'")[0][0]
    r = await ad.post(f"/api/admin/bindings/{bid}/sync")
    assert r.status_code == 202
    assert (await env["tanaka"].post(f"/api/admin/bindings/{bid}/sync")).status_code == 403


async def test_read_only_linodes_token_explains_and_can_be_replaced(env, cloud):
    """Linodes が Read Only のトークンでは edge をファイアウォールに付けられない。理由を示し、入れ替えで直る。"""
    ro = "read-only-token-0123456789"
    cloud.read_only_tokens.add(ro)
    ad = env["admin"]
    acc = (await ad.post("/api/admin/linode-accounts", json={"label": "Linode", "token": ro})).json()["id"]
    r = await ad.post("/api/admin/firewalls", json={"linode_account_id": acc, "linode_firewall_id": 501})
    fw = r.json()["id"]
    body = {"id": "edge-2", "public_ip": "45.33.1.10", "linode_account_id": acc, "linode_id": 101, "firewall_id": fw}
    r = await ad.post("/api/admin/edges", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "linode_attach_forbidden", r.text
    assert "Read/Write" in r.json()["error"]["message"]
    assert q(env["url"], "SELECT count(*) FROM edges WHERE id = 'edge-2'") == [(0,)]

    # トークンを入れ替える（間違ったトークンは断られ、元のまま）
    assert (await ad.patch(f"/api/admin/linode-accounts/{acc}", json={"token": "x" * 30})).status_code == 400
    r = await ad.patch(f"/api/admin/linode-accounts/{acc}", json={"token": TOKEN})
    assert r.status_code == 200 and TOKEN not in r.text
    assert TOKEN not in str(q(env["url"], "SELECT detail::text, target FROM audit_log"))
    r = await ad.post("/api/admin/edges", json=body)
    assert r.status_code == 201, r.text
    assert cloud.fw[501]["devices"] == {101}
    # Tailscale の IP は省略でき、edge からの報告（Tailscale 経由）で入る
    assert q(env["url"], "SELECT tailscale_ip FROM edges WHERE id = 'edge-2'") == [(None,)]


async def test_firewall_can_be_removed_even_if_linode_refuses(env, cloud):
    """トークンの権限が足りずに Linode のルールを消せなくても、Hatch の登録だけは外せる（アカウントも消せる）。"""
    acc, fw = await register(env, cloud)
    ad = env["admin"]
    r = await ad.patch("/api/admin/edges/edge-1", json={"clear_firewall": True})
    assert r.status_code == 200, r.text
    cloud.token = "rotated-token-0123456789"  # 登録したトークンが使えなくなった
    r = await ad.delete(f"/api/admin/firewalls/{fw}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "linode_cleanup_failed", r.text
    assert (await ad.delete(f"/api/admin/firewalls/{fw}", params={"keep_rules": "true"})).status_code == 204
    assert q(env["url"], "SELECT count(*) FROM firewalls") == [(0,)]
    # edge を「Linode 以外」に戻すと、アカウントも削除できる
    assert (await ad.patch("/api/admin/edges/edge-1", json={"clear_linode": True})).status_code == 200
    assert q(env["url"], "SELECT linode_account_id, linode_id FROM edges") == [(None, None)]
    assert (await ad.delete(f"/api/admin/linode-accounts/{acc}")).status_code == 204
