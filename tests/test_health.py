"""自己監視のテスト。DATABASE_URL の DB に移行済みであること（CI と同じ）。"""

import os
from datetime import timedelta

import psycopg
import pytest

from hatch import __version__, health


@pytest.fixture(autouse=True)
def clean(db_url):
    yield


def levels(rep):
    return {c.name: c.level for c in rep.checks}


def test_no_heartbeat_is_error():
    rep = health.full_report()
    assert levels(rep)["worker"] == "error"
    assert levels(rep)["bot"] == "degraded"
    assert rep.status == "error"


def test_all_beating_is_ok():
    for comp in ("worker", "scheduler", "bot"):
        health.beat(comp)
    rep = health.full_report()
    assert levels(rep)["worker"] == "ok"
    assert levels(rep)["db"] == "ok"


def test_stale_worker():
    for comp in ("worker", "scheduler", "bot"):
        health.beat(comp)
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        c.execute("UPDATE component_heartbeats SET beat_at = now() - interval '5 minutes' WHERE component = 'worker'")
    rep = health.full_report()
    assert levels(rep)["worker"] == "error"


def test_version_mismatch_is_degraded():
    for comp in ("worker", "scheduler", "bot"):
        health.beat(comp)
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        c.execute("UPDATE component_heartbeats SET version = '0.0.1' WHERE component = 'bot'")
    assert levels(health.full_report())["bot"] == "degraded"
    assert __version__ != "0.0.1"


def test_endpoint_requires_token(monkeypatch):
    from fastapi.testclient import TestClient

    from hatch.main import app

    monkeypatch.setenv("PD_HEALTH_TOKEN", "t0ken")
    cl = TestClient(app)
    assert cl.get("/api/health/full").status_code == 401
    assert cl.get("/api/health/full", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = cl.get("/api/health/full", headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 503 and r.json()["status"] == "error"
    for comp in ("worker", "scheduler", "bot"):
        health.beat(comp)
    r = cl.get("/api/health/full", headers={"Authorization": "Bearer t0ken"})
    assert r.status_code == 200 and r.json()["status"] in ("ok", "degraded")
    assert cl.get("/api/health").status_code == 200


def test_timedelta_import_used():
    assert timedelta(seconds=health.STALE_SECONDS).total_seconds() == 90
