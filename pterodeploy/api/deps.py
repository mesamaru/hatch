"""ルート共通の依存（テストでは app.dependency_overrides で差し替える）。

API から外部サービスを呼ぶのは「読み取り・短時間で終わるもの」だけ（ゾーンの確認、使用量、電源操作）。
作成・削除はジョブにする。
"""

from __future__ import annotations

from functools import lru_cache

from ..adapters.dns import CloudflareDns, DnsAdapter
from ..adapters.panel import PanelAdapter, PterodactylPanel
from ..config import get_settings
from ..games import Game, load_games


def get_games() -> dict[str, Game]:
    return load_games()


@lru_cache(maxsize=1)
def _dns() -> CloudflareDns:
    s = get_settings()
    return CloudflareDns(s.CF_API_TOKEN.get_secret_value(), s.PD_INSTANCE)


@lru_cache(maxsize=1)
def _panel() -> PterodactylPanel:
    s = get_settings()
    return PterodactylPanel(s.PANEL_URL, s.PANEL_APP_KEY.get_secret_value(), s.PANEL_CLIENT_KEY.get_secret_value())


def get_dns() -> DnsAdapter:
    return _dns()


def get_panel() -> PanelAdapter:
    return _panel()
