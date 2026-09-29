"""ジョブが使う外部サービスと設定のまとめ。本番は worker.py で組み立て、テストはフェイクを入れる。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapters.dns import DnsAdapter
from ..adapters.panel import PanelAdapter
from ..edge.render import Limits
from ..games import Game


@dataclass
class Deps:
    panel: PanelAdapter
    dns: DnsAdapter
    games: dict[str, Game]
    limits: Limits = field(default_factory=Limits)
    monitor: Any = None  # MonitorAdapter（T17 で実装。None の間は監視の手順を飛ばす）
    poll_interval: float = 5.0  # インストール完了・起動の確認間隔
    install_timeout: float = 600.0
    start_timeout: float = 180.0
    edge_wait: float = 60.0
