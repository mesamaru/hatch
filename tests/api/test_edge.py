"""edge 設定の生成と配布（docs/TASKS.md T11）。"""

from __future__ import annotations

import shutil
import subprocess
import uuid

import httpx
import psycopg
import pytest

from pterodeploy import db
from pterodeploy.edge import publish as pub
from pterodeploy.edge.render import Limits, Target, render_haproxy, render_nft
from pterodeploy.games import parse
from pterodeploy.main import app

GAMES = parse(
    {
        "paper": {"kind": "mc", "nest": 1, "egg": 3, "proxy_protocol": True},
        "velocity": {"kind": "proxy", "nest": 1, "egg": 16, "proxy_protocol": False},
        "palworld": {"kind": "other", "nest": 5, "egg": 20, "protocol": "udp", "monitor": "push"},
    }
)
AUTH = {"Authorization": "Bearer edge-token"}


def test_render_is_deterministic_and_sorted():
    a = [Target(30013, "100.64.0.13", "tcp", False, "net"), Target(25561, "100.64.0.12", "tcp", True, "lobby")]
    one = render_haproxy(a, Limits())
    two = render_haproxy(list(reversed(a)), Limits())
    assert one == two
    assert one.index("fe_25561") < one.index("fe_30013")
    assert "send-proxy-v2 check-send-proxy" in one.split("\nbackend be_25561")[1].split("\nfrontend")[0]
    assert "send-proxy" not in one.split("\nbackend be_30013")[1]
    assert "timeout client 2h" in one


def test_render_rejects_bad_ip():
    with pytest.raises(ValueError):
        render_haproxy([Target(25561, "100.64.0.12; rm -rf /", "tcp", False, "x")], Limits())


def test_udp_goes_to_nft_only():
    t = [Target(8211, "100.64.0.13", "udp", False, "pal"), Target(25561, "100.64.0.12", "tcp", True, "lobby")]
    assert "8211" not in render_haproxy(t, Limits())
    nft = render_nft(t, Limits())
    assert "udp dport 8211 dnat to 100.64.0.13:8211" in nft
    assert "25561" not in nft
    assert render_nft([t[1]], Limits()) == ""


@pytest.mark.skipif(not shutil.which("haproxy"), reason="haproxy が入っていない")
def test_haproxy_accepts_generated_config(tmp_path):
    cfg = tmp_path / "pd.cfg"
    cfg.write_text(render_haproxy([Target(25561, "100.64.0.12", "tcp", True, "lobby")], Limits()))
    base = tmp_path / "base.cfg"
    base.write_text("global\n    log stdout format raw local0\n")
    r = subprocess.run(["haproxy", "-c", "-f", str(base), "-f", str(cfg)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not shutil.which("nft"), reason="nft が入っていない")
def test_nft_accepts_generated_rules(tmp_path):
    f = tmp_path / "pd.nft"
    body = render_nft([Target(8211, "100.64.0.13", "udp", False, "pal")], Limits())
    f.write_text("table ip pterodeploy\ndelete table ip pterodeploy\n" + body)
    r = subprocess.run(["nft", "-c", "-f", str(f)], capture_output=True, text=True)
    if r.returncode != 0 and "Operation not permitted" in r.stderr:
        pytest.skip("nft -c に権限が必要な環境")
    assert r.returncode == 0, r.stderr


# ---- DB を使うテスト ----
@pytest.fixture
async def pool(db_url):
    await db.open_pool(db_url, max_size=4)
    yield
    await db.close_pool()


def seed(db_url):
    with psycopg.connect(db_url) as c:
        c.execute(
            "INSERT INTO edges (id, public_ip, tailscale_ip, is_active) VALUES "
            "('edge-1','203.0.113.10','100.64.1.1', true), ('edge-2','198.51.100.20','100.64.1.2', false)"
        )
        c.execute(
            "INSERT INTO nodes (id, panel_node_id, tailscale_ip, memory_mb, disk_mb) VALUES "
            "('node-1', 1, '100.64.0.12', 32768, 500000)"
        )
        uid = c.execute("INSERT INTO users (username, email) VALUES ('tanaka','t@x') RETURNING id").fetchone()[0]
        c.execute("INSERT INTO domains (name, cf_zone_id, is_default) VALUES ('nuids.jp','z', true)")
        c.execute(
            "INSERT INTO slot_rules (name, domain_id, port_start, port_end, host_template, excluded_ports) "
            "VALUES ('mc', 1, 25560, 25569, 'mc{nn}trt', '{25560}')"
        )
        for p in range(25561, 25570):
            c.execute(
                "INSERT INTO slots (port, rule_id, domain_id, host) VALUES (%s, 1, 1, %s)", (p, f"mc{p - 25560:02d}trt")
            )
        c.execute(
            "INSERT INTO slot_rules (name, domain_id, port_start, port_end, host_template, prepublish) "
            "VALUES ('udp', 1, 8211, 8212, '{server}', false)"
        )
        c.execute("INSERT INTO slots (port, rule_id, domain_id) VALUES (8211, 2, 1)")
        return uid


def add_server(db_url, uid, name, game, port, status="running", exposed=True):
    sid = str(uuid.uuid4())
    with psycopg.connect(db_url) as c:
        c.execute(
            "INSERT INTO servers (id, name, owner_id, plan_id, game, node_id, status, exposed, expires_at) "
            "VALUES (%s,%s,%s,'light',%s,'node-1',%s,%s, now()+interval '30 days')",
            (sid, name, uid, game, status, exposed),
        )
        c.execute("UPDATE slots SET status='assigned', server_id=%s WHERE port=%s", (sid, port))
    return sid


async def test_publish_creates_versions_only_on_change(pool, db_url):
    uid = seed(db_url)
    add_server(db_url, uid, "lobby", "paper", 25561)
    add_server(db_url, uid, "pal", "palworld", 8211)
    add_server(db_url, uid, "hidden", "paper", 25562, exposed=False)
    add_server(db_url, uid, "gone", "paper", 25563, status="trashed")
    async with db.transaction() as conn:
        v1 = await pub.publish(conn, GAMES, Limits())
    async with db.transaction() as conn:
        v2 = await pub.publish(conn, GAMES, Limits())
    assert v1 == v2
    with psycopg.connect(db_url) as c:
        cfg, nft = c.execute(
            "SELECT haproxy_cfg, nft_rules FROM edge_config_versions WHERE version=%s", (v1,)
        ).fetchone()
    assert "fe_25561" in cfg and "fe_25562" not in cfg and "fe_25563" not in cfg
    assert "udp dport 8211" in nft
    add_server(db_url, uid, "survival", "paper", 25564)
    async with db.transaction() as conn:
        v3 = await pub.publish(conn, GAMES, Limits())
    assert v3 > v1


async def test_edge_config_api(pool, db_url):
    uid = seed(db_url)
    add_server(db_url, uid, "lobby", "paper", 25561)
    async with db.transaction() as conn:
        v = await pub.publish(conn, GAMES, Limits())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cl:
        r = await cl.get("/api/edge/config", params={"edge": "edge-1", "have": 0})
        assert r.status_code == 401
        r = await cl.get(
            "/api/edge/config", params={"edge": "edge-1", "have": 0}, headers={"Authorization": "Bearer nope"}
        )
        assert r.status_code == 401
        r = await cl.get("/api/edge/config", params={"edge": "edge-9", "have": 0}, headers=AUTH)
        assert r.status_code == 404 and r.json()["error"]["code"] == "edge_unknown"
        r = await cl.get("/api/edge/config", params={"edge": "edge-1", "have": 0}, headers=AUTH)
        assert r.status_code == 200 and r.json()["version"] == v and "fe_25561" in r.json()["haproxy_cfg"]
        r = await cl.get("/api/edge/config", params={"edge": "edge-1", "have": v}, headers=AUTH)
        assert r.status_code == 204
        r = await cl.post(
            "/api/edge/report", json={"edge_id": "edge-1", "applied_version": v, "error": None}, headers=AUTH
        )
        assert r.status_code == 204
        r = await cl.get("/api/edge/config", params={"edge": "EDGE;1"}, headers=AUTH)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation"
    assert await pub.wait_applied(v, within=0) is True  # edge-2 は応答が無いので数えない
    with psycopg.connect(db_url) as c:
        c.execute("UPDATE edges SET last_seen_at = now() WHERE id='edge-2'")
        assert c.execute("SELECT last_seen_at IS NOT NULL FROM edges WHERE id='edge-1'").fetchone()[0]
    assert await pub.wait_applied(v, within=0) is False  # 応答のある edge-2 がまだ適用していない
    with psycopg.connect(db_url) as c:
        c.execute("UPDATE edges SET applied_version=%s WHERE id='edge-2'", (v,))
    assert await pub.wait_applied(v, within=0) is True


async def test_report_masks_secrets(pool, db_url):
    seed(db_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cl:
        await cl.post(
            "/api/edge/report", json={"edge_id": "edge-1", "error": "failed Bearer abcdefghijklmn"}, headers=AUTH
        )
    with psycopg.connect(db_url) as c:
        err = c.execute("SELECT last_error FROM edges WHERE id='edge-1'").fetchone()[0]
    assert "abcdefghijklmn" not in err


def test_games_validation():
    from pterodeploy.config import ConfigError

    with pytest.raises(ConfigError, match="UDP"):
        parse({"x": {"kind": "other", "nest": 1, "egg": 1, "protocol": "udp"}})
    with pytest.raises(ConfigError, match="egg"):
        parse({"x": {"kind": "mc", "nest": 1}})
    g = parse({"x": {"kind": "mc", "nest": 1, "egg": 2}})["x"]
    assert g.monitor == "gamedig" and g.is_minecraft
