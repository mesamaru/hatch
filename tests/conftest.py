"""テスト共通の準備。

- DB を使うテストは `db_url` フィクスチャを使う。移行を当てたテンプレート DB をセッションで1回作り、
  テストごとにそこから空の DB を複製する（速く、テスト同士が干渉しない）。
- DATABASE_URL が無い環境では DB を使うテストを飛ばす。
- 必須の設定値にはダミーを入れる（本物のキーは絶対に使わない）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

DUMMY_ENV = {
    "PD_PUBLIC_URL": "https://panel.test",
    "PD_INTERNAL_URL": "http://pterodeploy.test:8080",
    "PD_INSTANCE": "test",
    "PD_SECRET_KEY": "0" * 64,
    "PD_HEALTH_TOKEN": "health-token",
    "PD_DATA_DIR": "/tmp",
    "PANEL_URL": "https://gp.test",
    "PANEL_APP_KEY": "ptla_dummy_app_key_for_tests",
    "PANEL_CLIENT_KEY": "ptlc_dummy_client_key_for_tests",
    "CF_API_TOKEN": "cf-dummy-token",
    "KUMA_URL": "http://kuma.test:3001",
    "KUMA_USERNAME": "pd",
    "KUMA_PASSWORD": "pd-password",
    "KUMA_METRICS_KEY": "kuma-metrics",
    "KUMA_WEBHOOK_SECRET": "kuma-secret",
    "DISCORD_BOT_TOKEN": "discord-bot-token",
    "DISCORD_CLIENT_ID": "123",
    "DISCORD_CLIENT_SECRET": "discord-secret",
    "DISCORD_GUILD_ID": "456",
    "EDGE_AGENT_TOKEN": "edge-token",
}


@pytest.fixture(autouse=True)
def _dummy_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k, v in DUMMY_ENV.items():
        monkeypatch.setenv(k, v)
    if "DATABASE_URL" not in os.environ:
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    from pterodeploy import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _with_db(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + dbname))


_TEMPLATE = f"pd_test_tpl_{os.getpid()}"


@pytest.fixture(scope="session")
def _template_db() -> Iterator[str]:
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL が設定されていないため、DB を使うテストを飛ばします")
    admin = _with_db(base, "postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{_TEMPLATE}"')
        c.execute(f'CREATE DATABASE "{_TEMPLATE}"')
    tpl_url = _with_db(base, _TEMPLATE)
    from pterodeploy import migrate

    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = tpl_url
    try:
        migrate.main(quiet=True)
    finally:
        os.environ["DATABASE_URL"] = old
    yield admin
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{_TEMPLATE}" WITH (FORCE)')


@pytest.fixture
def db_url(_template_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """移行済みの空の DB。DATABASE_URL もこの DB に向ける。"""
    name = f"pd_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_template_db, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{_TEMPLATE}"')
    url = _with_db(os.environ["DATABASE_URL"], name)
    monkeypatch.setenv("DATABASE_URL", url)
    yield url
    with psycopg.connect(_template_db, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
