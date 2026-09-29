"""ゲームの定義（/etc/pterodeploy/games.yml）。見本は deploy/games.example.yml。

パネルの nest・egg の ID や、PROXY プロトコル・UDP などゲームごとの違いはコードに書かず、ここから読む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError

KINDS = ("mc", "mod", "proxy", "other")
DEFAULT_PATH = "/etc/pterodeploy/games.yml"


@dataclass(frozen=True)
class Game:
    id: str
    label: str
    kind: str  # mc / mod / proxy / other
    nest: int
    egg: int
    protocol: str = "tcp"  # tcp / udp
    monitor: str = "gamedig"  # gamedig / tcp / push
    proxy_protocol: bool = False  # edge から PROXY プロトコル v2 で渡すか
    plugin_loader: str | None = None
    environment: dict[str, str] = field(default_factory=dict)

    @property
    def is_minecraft(self) -> bool:
        """SRV レコードを作る・Velocity に参加できる種類。"""
        return self.kind in ("mc", "mod", "proxy")


def parse(data: dict[str, Any]) -> dict[str, Game]:
    if not isinstance(data, dict) or not data:
        raise ConfigError("games.yml にゲームが1つも定義されていません")
    out: dict[str, Game] = {}
    for gid, g in data.items():
        where = f"games.yml の {gid}"
        if not isinstance(g, dict):
            raise ConfigError(f"{where} の形式が正しくありません")
        try:
            game = Game(
                id=str(gid),
                label=str(g.get("label") or gid),
                kind=str(g["kind"]),
                nest=int(g["nest"]),
                egg=int(g["egg"]),
                protocol=str(g.get("protocol", "tcp")),
                monitor=str(g.get("monitor", "gamedig" if g.get("kind") in ("mc", "mod", "proxy") else "tcp")),
                proxy_protocol=bool(g.get("proxy_protocol", False)),
                plugin_loader=g.get("plugin_loader"),
                environment={str(k): str(v) for k, v in (g.get("environment") or {}).items()},
            )
        except KeyError as e:
            raise ConfigError(f"{where} に {e.args[0]} がありません") from None
        except (TypeError, ValueError):
            raise ConfigError(f"{where} の nest・egg は整数で書いてください") from None
        if game.kind not in KINDS:
            raise ConfigError(f"{where} の kind は {' / '.join(KINDS)} のどれかにしてください")
        if game.protocol not in ("tcp", "udp"):
            raise ConfigError(f"{where} の protocol は tcp か udp にしてください")
        if game.monitor not in ("gamedig", "tcp", "push"):
            raise ConfigError(f"{where} の monitor は gamedig / tcp / push のどれかにしてください")
        if game.protocol == "udp" and game.monitor != "push":
            raise ConfigError(f"{where}：UDP のゲームは monitor: push にしてください（TCP で確認できないため）")
        if game.protocol == "udp" and game.proxy_protocol:
            raise ConfigError(f"{where}：UDP のゲームでは proxy_protocol は使えません")
        out[game.id] = game
    return out


@lru_cache(maxsize=1)
def load_games(path: str | None = None) -> dict[str, Game]:
    p = Path(path or os.environ.get("PD_GAMES_FILE", DEFAULT_PATH))
    if not p.exists():
        raise ConfigError(
            f"{p} がありません。deploy/games.example.yml をコピーして、パネルの nest・egg に合わせてください"
        )
    return parse(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
