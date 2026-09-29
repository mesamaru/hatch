"""ゲームパネルのフェイク（メモリ上）。本物の癖（アロケーション作成で ID が返らない等）も再現する。"""

from __future__ import annotations

import itertools
import uuid as uuidlib
from dataclasses import replace

from pterodeploy.adapters.panel import (
    Backup,
    Cron,
    Egg,
    NewPanelServer,
    NewPanelUser,
    PanelServer,
    PanelUser,
    PanelUserChanges,
    Resources,
)
from pterodeploy.errors import AppError, UpstreamError


class FakePanel:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.users: dict[int, PanelUser] = {}
        self.passwords: dict[int, str] = {}
        self.allocations: dict[int, dict] = {}  # id → {node, ip, port, server}
        self.servers: dict[int, PanelServer] = {}
        self.states: dict[str, str] = {}  # identifier → state
        self.installing: dict[str, int] = {}  # identifier → あと何回「作成中」を返すか
        self.backups: dict[str, list[Backup]] = {}
        self.schedules: dict[str, dict[str, int]] = {}
        self.subusers: dict[str, dict[str, list[str]]] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.fail: dict[str, Exception] = {}  # メソッド名 → 次の1回で投げる例外
        self.calls: list[str] = []
        self.eggs = {
            (1, 3): Egg(
                3, "ghcr.io/pterodactyl/yolks:java_21", "java -jar server.jar", {"SERVER_JARFILE": "server.jar"}
            )
        }

    def _hit(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise self.fail.pop(name)

    # ---- ユーザー ----
    async def find_user(self, *, external_id=None, email=None):
        self._hit("find_user")
        for u in self.users.values():
            if (external_id and u.external_id == external_id) or (email and u.email == email):
                return u
        return None

    async def create_user(self, u: NewPanelUser):
        self._hit("create_user")
        if any(x.username == u.username or x.email == u.email for x in self.users.values()):
            raise UpstreamError("ゲームパネル", "The username has already been taken.", 422)
        pu = PanelUser(next(self._ids), u.external_id, u.username, u.email, u.username, u.username, u.root_admin)
        self.users[pu.id] = pu
        return pu

    async def update_user(self, panel_user_id: int, changes: PanelUserChanges):
        self._hit("update_user")
        u = self.users[panel_user_id]
        u = replace(
            u,
            username=changes.username or u.username,
            email=changes.email or u.email,
            root_admin=u.root_admin if changes.root_admin is None else changes.root_admin,
        )
        self.users[panel_user_id] = u
        if changes.password:
            self.passwords[panel_user_id] = changes.password
        return u

    async def delete_user(self, panel_user_id: int):
        self._hit("delete_user")
        self.users.pop(panel_user_id, None)

    async def get_egg(self, nest: int, egg: int):
        self._hit("get_egg")
        if (nest, egg) not in self.eggs:
            raise UpstreamError("ゲームパネル", "egg が見つかりません", 404)
        return self.eggs[(nest, egg)]

    # ---- アロケーション ----
    async def create_allocation(self, node_id: int, ip: str, port: int) -> int:
        self._hit("create_allocation")
        for aid, a in self.allocations.items():
            if (a["node"], a["ip"], a["port"]) == (node_id, ip, port):
                if a["server"]:
                    raise AppError("slot_taken", f"ポート {port} はゲームパネル上で別のサーバーが使っています。", 409)
                return aid
        aid = next(self._ids)
        self.allocations[aid] = {"node": node_id, "ip": ip, "port": port, "server": None}
        return aid

    async def delete_allocation(self, node_id: int, allocation_id: int):
        self._hit("delete_allocation")
        a = self.allocations.get(allocation_id)
        if a and a["server"]:
            raise UpstreamError("ゲームパネル", "Cannot delete allocation assigned to a server", 400)
        self.allocations.pop(allocation_id, None)

    # ---- サーバー ----
    async def create_server(self, s: NewPanelServer):
        self._hit("create_server")
        if any(x.external_id == s.external_id for x in self.servers.values()):
            raise UpstreamError("ゲームパネル", "external_id は既に使われています", 422)
        u = str(uuidlib.uuid4())
        ps = PanelServer(next(self._ids), u, u[:8], s.external_id, s.name, False, s.allocation_id, s.user_id)
        self.servers[ps.id] = ps
        self.allocations[s.allocation_id]["server"] = ps.id
        self.states[ps.identifier] = "offline"
        self.installing.setdefault(ps.identifier, 1)
        return ps

    async def find_server(self, external_id: str):
        self._hit("find_server")
        return next((s for s in self.servers.values() if s.external_id == external_id), None)

    async def delete_server(self, panel_server_id: int):
        self._hit("delete_server")
        s = self.servers.pop(panel_server_id, None)
        if s:
            for a in self.allocations.values():
                if a["server"] == s.id:
                    a["server"] = None

    async def suspend(self, panel_server_id: int):
        self._hit("suspend")
        self.servers[panel_server_id] = replace(self.servers[panel_server_id], suspended=True)

    async def unsuspend(self, panel_server_id: int):
        self._hit("unsuspend")
        self.servers[panel_server_id] = replace(self.servers[panel_server_id], suspended=False)

    async def is_installing(self, identifier: str) -> bool:
        self._hit("is_installing")
        left = self.installing.get(identifier, 0)
        if left > 0:
            self.installing[identifier] = left - 1
            return True
        return False

    async def power(self, identifier: str, signal: str):
        self._hit("power")
        self.states[identifier] = {"start": "running", "restart": "running", "stop": "offline", "kill": "offline"}[
            signal
        ]

    async def resources(self, identifier: str):
        self._hit("resources")
        return Resources(self.states.get(identifier, "offline"), 10.0, 1 << 30, 1 << 30)

    # ---- バックアップ・スケジュール・サブユーザー・ファイル ----
    async def list_backups(self, identifier: str):
        self._hit("list_backups")
        return list(self.backups.get(identifier, []))

    async def create_backup(self, identifier: str, name: str):
        self._hit("create_backup")
        b = Backup(str(uuidlib.uuid4()), name, 1 << 30, True, False, "2026-10-01T00:00:00Z", "2026-10-01T00:01:00Z")
        self.backups.setdefault(identifier, []).append(b)
        return b

    async def delete_backup(self, identifier: str, backup_uuid: str):
        self._hit("delete_backup")
        self.backups[identifier] = [b for b in self.backups.get(identifier, []) if b.uuid != backup_uuid]

    async def backup_download_url(self, identifier: str, backup_uuid: str):
        self._hit("backup_download_url")
        return f"https://storage.test/{backup_uuid}?sig=x"

    async def restore_backup(self, identifier: str, backup_uuid: str, *, truncate: bool = True):
        self._hit("restore_backup")

    async def ensure_schedule(self, identifier: str, name: str, cron: Cron, action: str = "backup") -> int:
        self._hit("ensure_schedule")
        sch = self.schedules.setdefault(identifier, {})
        if name not in sch:
            sch[name] = next(self._ids)
        return sch[name]

    async def add_subuser(self, identifier: str, email: str, permissions: list[str]) -> str:
        self._hit("add_subuser")
        u = str(uuidlib.uuid4())
        self.subusers.setdefault(identifier, {})[u] = permissions
        return u

    async def remove_subuser(self, identifier: str, subuser_uuid: str):
        self._hit("remove_subuser")
        self.subusers.get(identifier, {}).pop(subuser_uuid, None)

    async def read_file(self, identifier: str, path: str) -> str:
        self._hit("read_file")
        return self.files.get((identifier, path), "")

    async def write_file(self, identifier: str, path: str, content: str):
        self._hit("write_file")
        self.files[(identifier, path)] = content

    async def pull_file(self, identifier: str, url: str, directory: str = "/"):
        self._hit("pull_file")
