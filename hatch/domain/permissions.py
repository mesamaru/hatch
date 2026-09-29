"""権限の判定。表は docs/IMPLEMENTATION.md 3.5。ルートでは require() を使う。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import AppError

SHARE_LEVELS = ("view", "console", "files", "full")

# action → 共同管理者で許可する最低の権限（None は所有者と管理者のみ）
SERVER_ACTIONS: dict[str, str | None] = {
    "server.view": "view",
    "server.power": "console",
    "server.backup": "files",
    "server.plugins": "files",
    "server.restore": "full",
    "server.settings": None,
    "server.share": None,
    "server.domain": None,
    "server.trash": None,
    "server.dev_copy": None,
    "server.extend": None,  # 管理者は延長、所有者は申請（ルート側で分岐）
}
GLOBAL_ACTIONS = {"server.create"}


@dataclass(frozen=True)
class Actor:
    id: str
    role: str  # "admin" / "user"
    deleting: bool = False  # 退会の手続き中（読み取りのみ）
    suspended: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class ServerRef:
    owner_id: str
    status: str
    shares: dict[str, str] = field(default_factory=dict)  # user_id → view/console/files/full


def can(actor: Actor, action: str, server: ServerRef | None = None) -> bool:
    if action.startswith("admin."):
        return actor.is_admin
    if action in GLOBAL_ACTIONS:
        return actor.is_admin or not (actor.deleting or actor.suspended)
    if action not in SERVER_ACTIONS:
        raise ValueError(f"未知の action: {action}")
    if server is None:
        raise ValueError(f"{action} にはサーバーが必要です")
    if actor.is_admin:
        return True
    if actor.suspended:
        return False
    read_only = actor.deleting or server.status == "suspended"
    if read_only and action != "server.view":
        return False
    if server.owner_id == actor.id:
        return True
    level = server.shares.get(actor.id)
    need = SERVER_ACTIONS[action]
    if level is None or need is None:
        return False
    return SHARE_LEVELS.index(level) >= SHARE_LEVELS.index(need)


def require(actor: Actor, action: str, server: ServerRef | None = None) -> None:
    """ダメなら AppError。他人のサーバーは存在自体を知らせない（404）。"""
    if can(actor, action, server):
        return
    if server is not None and not can(actor, "server.view", server):
        raise AppError("not_found", "見つかりません。", 404)
    if actor.deleting:
        raise AppError("account_deleting", "退会の手続き中のため、この操作はできません。", 409)
    if server is not None and server.status == "suspended":
        raise AppError("server_suspended", "このサーバーは管理者により利用停止中です。", 409)
    raise AppError("forbidden", "この操作をする権限がありません。", 403)
