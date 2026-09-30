"""画面の通しテスト用のサーバー：外部サービス（ゲームパネル・Cloudflare）をフェイクにして、
API とジョブの実行を1つのプロセスで動かす。本物のサービスには一切つながない。

python -m tests.e2e.fake_server <ポート>
DATABASE_URL などの設定は環境変数から読む（tests/conftest.py の DUMMY_ENV と同じもの）。
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from hatch.api.deps import get_dns, get_games, get_panel
from hatch.api.infra import get_linode_factory
from hatch.games import parse
from hatch.jobs import bindings as _bindings  # noqa: F401 - ジョブの種類を登録する
from hatch.jobs import deploy as _deploy  # noqa: F401
from hatch.jobs import lifecycle as _lifecycle  # noqa: F401
from hatch.jobs import publish_slots as _publish_slots  # noqa: F401
from hatch.jobs.deps import Deps
from hatch.jobs.engine import Engine
from hatch.main import app
from tests.fakes.dns import FakeDns
from tests.fakes.linode import FakeLinode, FakeLinodeCloud
from tests.fakes.panel import FakePanel

ZONE = "a" * 32
LINODE_TOKEN = "linode-token-0123456789abcdef"  # フェイクの Linode で使えるトークン
GAMES = parse(
    {
        "paper": {"label": "Paper", "kind": "mc", "nest": 1, "egg": 3, "proxy_protocol": True},
        "velocity": {"label": "Velocity", "kind": "proxy", "nest": 1, "egg": 4},
    }
)


async def main(port: int) -> None:
    dns = FakeDns(instance="test", zones={ZONE: "nuids.jp"})
    panel = FakePanel()
    app.dependency_overrides[get_dns] = lambda: dns
    app.dependency_overrides[get_panel] = lambda: panel
    app.dependency_overrides[get_games] = lambda: GAMES
    cloud = FakeLinodeCloud(LINODE_TOKEN)
    linode = lambda token: FakeLinode(cloud, token)  # noqa: E731
    app.dependency_overrides[get_linode_factory] = lambda: linode
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    engine = Engine(
        Deps(panel=panel, dns=dns, games=GAMES, poll_interval=0, edge_wait=0, linode=linode),
        retry_delays=(0, 0, 0),
        heartbeat=3600,
    )
    stop = asyncio.Event()

    async def worker() -> None:
        while not server.started:  # noqa: ASYNC110 - uvicorn は起動の完了をイベントで知らせないため
            await asyncio.sleep(0.05)  # DB の接続（API の起動処理）を待つ
        await engine.run_forever(stop, idle_sleep=0.2)

    task = asyncio.create_task(worker())
    try:
        await server.serve()
    finally:
        stop.set()
        await task


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # psycopg は Proactor で動かない
    asyncio.run(main(int(sys.argv[1])), loop_factory=loop_factory)
