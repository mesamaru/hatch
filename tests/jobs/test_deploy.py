"""デプロイ（docs/TASKS.md T12 の受け入れ条件）。外部サービスはフェイク。"""

from __future__ import annotations

import psycopg
import pytest

from hatch import db
from hatch.adapters.dns import DnsRecordSpec
from hatch.domain.permissions import Actor
from hatch.errors import AppError, UpstreamError
from hatch.games import parse
from hatch.jobs import deploy as _deploy  # noqa: F401 - ジョブの登録
from hatch.jobs.deps import Deps
from hatch.jobs.engine import Engine
from hatch.services.deploy import DeployRequest, request_deploy
from tests.fakes.dns import FakeDns
from tests.fakes.panel import FakePanel

GAMES = parse(
    {
        "paper": {"kind": "mc", "nest": 1, "egg": 3, "proxy_protocol": True},
        "palworld": {"kind": "other", "nest": 1, "egg": 3, "protocol": "udp", "monitor": "push"},
    }
)


@pytest.fixture
async def env(db_url):
    await db.open_pool(db_url, max_size=6)
    with psycopg.connect(db_url) as c:
        tanaka = c.execute(
            "INSERT INTO users (username, email, max_servers) VALUES ('tanaka','t@x',2) RETURNING id::text"
        ).fetchone()[0]
        suzuki = c.execute("INSERT INTO users (username, email) VALUES ('suzuki','s@x') RETURNING id::text").fetchone()[
            0
        ]
        c.execute(
            "INSERT INTO nodes (id, panel_node_id, tailscale_ip, memory_mb, disk_mb) VALUES "
            "('node-1', 1, '100.64.0.12', 16384, 200000), ('node-2', 2, '100.64.0.13', 32768, 400000)"
        )
        c.execute("INSERT INTO domains (name, cf_zone_id, is_default) VALUES ('nuids.jp','z1', true)")
        # 共有枠（固定ホスト名・事前作成あり）
        c.execute(
            "INSERT INTO slot_rules (name, domain_id, port_start, port_end, host_template, excluded_ports) "
            "VALUES ('mc', 1, 25560, 25569, 'mc{nn}trt', '{25560}')"
        )
        for p in range(25561, 25570):
            c.execute(
                "INSERT INTO slots (port, rule_id, domain_id, host) VALUES (%s,1,1,%s)", (p, f"mc{p - 25560:02d}trt")
            )
        # suzuki 専用（サーバー名がホスト名）
        c.execute(
            "INSERT INTO slot_rules (name, domain_id, port_start, port_end, host_template, prepublish, assign_to, "
            "user_id) "
            "VALUES ('suzuki', 1, 30020, 30029, '{server}', false, 'user', %s)",
            (suzuki,),
        )
        for p in range(30020, 30030):
            c.execute("INSERT INTO slots (port, rule_id, domain_id) VALUES (%s,2,1)", (p,))
        # 事前作成済みにしておくスロット
        c.execute("UPDATE slots SET dns_state='published' WHERE port = 25565")
    panel, dns = FakePanel(), FakeDns(instance="test", zones={"z1": "nuids.jp"})
    deps = Deps(panel=panel, dns=dns, games=GAMES, poll_interval=0, install_timeout=5, start_timeout=5, edge_wait=0)
    engine = Engine(deps, retry_delays=(0, 0, 0), heartbeat=3600)
    yield {"url": db_url, "tanaka": tanaka, "suzuki": suzuki, "panel": panel, "dns": dns, "engine": engine}
    await db.close_pool()


def q(url, sql, args=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, args).fetchall()


async def ask(env, owner="tanaka", name="storia", port=25561, game="paper", actor=None, key=None):
    uid = env[owner]
    act = actor or Actor(id=uid, role="user")
    async with db.transaction() as conn:
        return await request_deploy(conn, act, DeployRequest(name, game, "light", port, uid, "web", key), GAMES)


async def test_deploy_success_fixed_host(env):
    acc = await ask(env)
    assert acc.fqdn == "mc01trt.nuids.jp"
    await env["engine"].run_once()
    status, err = q(env["url"], "SELECT status, error FROM jobs WHERE id=%s", (acc.job_id,))[0]
    assert status == "succeeded", err
    assert q(env["url"], "SELECT status, node_id FROM servers WHERE id=%s", (acc.server_id,)) == [("running", "node-2")]
    assert q(env["url"], "SELECT status FROM slots WHERE port=25561") == [("assigned",)]
    p = env["panel"]
    assert len(p.servers) == 1 and next(iter(p.states.values())) == "running"
    assert {("CNAME", "mc01trt.nuids.jp"), ("SRV", "_minecraft._tcp.mc01trt.nuids.jp")} <= env["dns"].names()
    cfg = q(env["url"], "SELECT haproxy_cfg FROM edge_config_versions ORDER BY version DESC LIMIT 1")[0][0]
    assert "fe_25561" in cfg and "100.64.0.13:25561 send-proxy-v2" in cfg
    assert len(p.schedules[next(iter(p.servers.values())).identifier]) == 1
    actions = [r[0] for r in q(env["url"], "SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["サーバーの作成を依頼", "サーバーを作成"]


async def test_deploy_server_named_host_and_udp(env):
    acc = await ask(env, owner="suzuki", name="pal", port=30022, game="palworld")
    assert acc.fqdn == "pal.nuids.jp"
    await env["engine"].run_once()
    assert q(env["url"], "SELECT status FROM jobs")[0][0] == "succeeded"
    assert env["dns"].names() == {("CNAME", "pal.nuids.jp")}  # UDP のゲームに SRV は作らない
    nft = q(env["url"], "SELECT nft_rules FROM edge_config_versions ORDER BY version DESC LIMIT 1")[0][0]
    assert "udp dport 30022" in nft


async def test_prepublished_dns_is_kept_on_rollback(env):
    env["dns"].records["z1"]["pre1"] = {
        "id": "pre1",
        "type": "CNAME",
        "name": "mc05trt.nuids.jp",
        "content": "edge.nuids.jp",
        "comment": "hatch:test slot=25565",
        "proxied": False,
        "ttl": 60,
    }
    env["panel"].fail["power"] = UpstreamError("ゲームパネル", "boom", 500)
    acc = await ask(env, port=25565)
    await env["engine"].run_once()
    assert q(env["url"], "SELECT status FROM jobs WHERE id=%s", (acc.job_id,))[0][0] == "rolled_back"
    assert ("CNAME", "mc05trt.nuids.jp") in env["dns"].names()  # 事前作成したものは残る


@pytest.mark.parametrize(
    "fail_at",
    [
        "create_allocation",
        "get_egg",
        "create_server",
        "is_installing",
        "power",
        "resources",
        "ensure_schedule",
        "dns_create",
    ],
)
async def test_failure_leaves_nothing_behind(env, fail_at):
    if fail_at == "dns_create":
        env["dns"].fail_next = [UpstreamError("Cloudflare", "boom", 400)]
        # find は先に呼ばれるので、create の時に失敗させる
        env["dns"].fail_next = []
        orig = env["dns"]._create

        async def boom(*a, **k):
            raise UpstreamError("Cloudflare", "boom", 400)

        env["dns"]._create = boom
    else:
        env["panel"].fail[fail_at] = UpstreamError("ゲームパネル", "boom", 500)
    acc = await ask(env, owner="suzuki", name="broken", port=30021)
    await env["engine"].run_once()
    status, error = q(env["url"], "SELECT status, error FROM jobs WHERE id=%s", (acc.job_id,))[0]
    assert status == "rolled_back", error
    p = env["panel"]
    assert p.servers == {} and p.allocations == {}
    assert p.users == {}  # この手順で作ったユーザーも消える
    assert env["dns"].names() == set()
    assert q(env["url"], "SELECT status FROM slots WHERE port=30021") == [("free",)]
    assert q(env["url"], "SELECT status FROM servers WHERE id=%s", (acc.server_id,)) == [("failed",)]
    assert q(env["url"], "SELECT count(*) FROM dns_records") == [(0,)]
    if fail_at == "dns_create":
        env["dns"]._create = orig
    # 名前とアドレスはすぐに使える
    again = await ask(env, owner="suzuki", name="broken", port=30021)
    await env["engine"].run_once()
    assert q(env["url"], "SELECT status FROM jobs WHERE id=%s", (again.job_id,))[0][0] == "succeeded"


async def test_existing_panel_user_is_reused_and_kept(env):
    from hatch.adapters.panel import NewPanelUser

    u = await env["panel"].create_user(NewPanelUser(env["tanaka"], "tanaka", "t@x"))
    env["panel"].fail["create_server"] = UpstreamError("ゲームパネル", "boom", 500)
    await ask(env)
    await env["engine"].run_once()
    assert u.id in env["panel"].users  # 既存のユーザーは消さない


async def test_request_validation(env):
    await ask(env, name="one")
    with pytest.raises(AppError) as e:
        await ask(env, name="one", port=25562)
    assert e.value.code == "name_taken"
    with pytest.raises(AppError) as e:
        await ask(env, name="two", port=25561)
    assert e.value.code == "slot_taken"
    with pytest.raises(AppError) as e:
        await ask(env, name="two", port=30020)  # suzuki 専用
    assert e.value.code == "slot_not_allowed"
    await ask(env, name="two", port=25562)
    with pytest.raises(AppError) as e:
        await ask(env, name="three", port=25563)  # tanaka は2台まで
    assert e.value.code == "limit_reached"
    with pytest.raises(AppError) as e:
        await ask(env, name="edge", port=25563, owner="suzuki")
    assert e.value.code == "validation"
    with pytest.raises(AppError) as e:
        await ask(env, owner="suzuki", name="x" * 40, port=30020)
    assert e.value.code == "validation"


async def test_other_owner_requires_admin(env):
    with pytest.raises(AppError) as e:
        await ask(env, owner="suzuki", name="abc", port=30020, actor=Actor(id=env["tanaka"], role="user"))
    assert e.value.code == "forbidden"
    acc = await ask(env, owner="suzuki", name="abc", port=30020, actor=Actor(id=env["tanaka"], role="admin"))
    assert acc.fqdn == "abc.nuids.jp"


async def test_tos_required(env):
    with psycopg.connect(env["url"]) as c:
        c.execute("INSERT INTO tos_versions (version, body_md) VALUES ('2026-10-01', '規約')")
        c.execute("UPDATE app_settings SET value = '\"2026-10-01\"' WHERE key = 'tos_current_version'")
    with pytest.raises(AppError) as e:
        await ask(env)
    assert e.value.code == "tos_required"
    with psycopg.connect(env["url"]) as c:
        c.execute("INSERT INTO tos_acceptances (user_id, tos_version) VALUES (%s, '2026-10-01')", (env["tanaka"],))
    await ask(env)


async def test_idempotent_request(env):
    a = await ask(env, key="discord:1")
    b = await ask(env, key="discord:1")
    assert a == b


async def test_no_capacity(env):
    with psycopg.connect(env["url"]) as c:
        c.execute("UPDATE nodes SET accepting = false")
    acc = await ask(env)
    await env["engine"].run_once()
    status, error = q(env["url"], "SELECT status, error FROM jobs WHERE id=%s", (acc.job_id,))[0]
    assert status == "rolled_back" and "空きのあるノード" in error


async def test_resume_after_crash_does_not_duplicate(env):
    """サーバー作成の直後にワーカーが落ちても、再開時に二重に作らない。"""
    acc = await ask(env)
    orig = env["panel"].is_installing

    async def crash(ident):
        raise KeyboardInterrupt  # ワーカーの停止を真似る（通常の例外ではない）

    env["panel"].is_installing = crash
    with pytest.raises(KeyboardInterrupt):
        await env["engine"].run_once()
    env["panel"].is_installing = orig
    with psycopg.connect(env["url"]) as c:
        c.execute("UPDATE jobs SET locked_at = now() - interval '10 minutes' WHERE id=%s", (acc.job_id,))
    await env["engine"].run_once()
    assert q(env["url"], "SELECT status FROM jobs WHERE id=%s", (acc.job_id,))[0][0] == "succeeded"
    assert len(env["panel"].servers) == 1 and len(env["panel"].allocations) == 1


async def test_dns_spec_for_fixture():
    assert DnsRecordSpec("A", "x", "c", content="1.1.1.1").ttl == 60
