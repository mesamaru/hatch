"""ゲームパネル（Pterodactyl）。docs/EXTERNAL.md 1章。将来の Pelican は同じ PanelAdapter を実装して差し替える。

- Application API（ptla_）: ユーザー・ノード・アロケーション・サーバー
- Client API（ptlc_、root 管理者のキー）: 電源・状態・バックアップ・スケジュール・サブユーザー・ファイル
- サーバーの指定: Application API は数値 ID、Client API は identifier（UUID の先頭8文字）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from ..errors import AppError, UpstreamError
from .http import ServiceClient

PowerSignal = Literal["start", "stop", "restart", "kill"]
PowerState = Literal["running", "starting", "stopping", "offline"]


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PanelUser:
    id: int
    external_id: str | None
    username: str
    email: str
    first_name: str
    last_name: str
    root_admin: bool


@dataclass(frozen=True)
class NewPanelUser:
    external_id: str
    username: str
    email: str
    root_admin: bool = False
    password: str | None = None


@dataclass(frozen=True)
class PanelUserChanges:
    username: str | None = None
    email: str | None = None
    password: str | None = None
    root_admin: bool | None = None


@dataclass(frozen=True)
class Egg:
    id: int
    docker_image: str
    startup: str
    variables: dict[str, str]


@dataclass(frozen=True)
class NewPanelServer:
    external_id: str
    name: str
    user_id: int
    egg: int
    docker_image: str
    startup: str
    environment: dict[str, str]
    memory_mb: int
    disk_mb: int
    cpu_percent: int
    backups: int
    allocation_id: int
    io: int = 500
    description: str = "Hatch が作成"


@dataclass(frozen=True)
class PanelServer:
    id: int
    uuid: str
    identifier: str
    external_id: str | None
    name: str
    suspended: bool
    allocation_id: int | None
    user_id: int | None


@dataclass(frozen=True)
class Resources:
    state: PowerState
    cpu_percent: float
    memory_bytes: int
    disk_bytes: int


@dataclass(frozen=True)
class Backup:
    uuid: str
    name: str
    bytes: int
    is_successful: bool
    is_locked: bool
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class Cron:
    minute: str = "0"
    hour: str = "4"
    day_of_month: str = "*"
    month: str = "*"
    day_of_week: str = "*"


# 画面の選択肢 → サブユーザーの権限。docs/EXTERNAL.md 1章「共同管理者」
_VIEW = ["websocket.connect"]
_CONSOLE = [*_VIEW, "control.console", "control.start", "control.stop", "control.restart"]
_FILES = [
    *_CONSOLE,
    "file.read",
    "file.read-content",
    "file.create",
    "file.update",
    "file.delete",
    "file.archive",
    "backup.read",
    "backup.create",
]
_FULL = [*_FILES, "backup.restore", "backup.download", "startup.read", "startup.update", "schedule.read"]
SUBUSER_PRESETS: dict[str, list[str]] = {"view": _VIEW, "console": _CONSOLE, "files": _FILES, "full": _FULL}


class PanelAdapter(Protocol):
    async def find_user(self, *, external_id: str | None = None, email: str | None = None) -> PanelUser | None: ...
    async def create_user(self, u: NewPanelUser) -> PanelUser: ...
    async def update_user(self, panel_user_id: int, changes: PanelUserChanges) -> PanelUser: ...
    async def delete_user(self, panel_user_id: int) -> None: ...
    async def get_egg(self, nest: int, egg: int) -> Egg: ...
    async def create_allocation(self, node_id: int, ip: str, port: int) -> int: ...
    async def delete_allocation(self, node_id: int, allocation_id: int) -> None: ...
    async def create_server(self, s: NewPanelServer) -> PanelServer: ...
    async def find_server(self, external_id: str) -> PanelServer | None: ...
    async def delete_server(self, panel_server_id: int) -> None: ...
    async def suspend(self, panel_server_id: int) -> None: ...
    async def unsuspend(self, panel_server_id: int) -> None: ...
    async def is_installing(self, identifier: str) -> bool: ...
    async def power(self, identifier: str, signal: PowerSignal) -> None: ...
    async def resources(self, identifier: str) -> Resources: ...
    async def list_backups(self, identifier: str) -> list[Backup]: ...
    async def create_backup(self, identifier: str, name: str) -> Backup: ...
    async def delete_backup(self, identifier: str, backup_uuid: str) -> None: ...
    async def backup_download_url(self, identifier: str, backup_uuid: str) -> str: ...
    async def restore_backup(self, identifier: str, backup_uuid: str, *, truncate: bool = True) -> None: ...
    async def ensure_schedule(self, identifier: str, name: str, cron: Cron, action: str = "backup") -> int: ...
    async def add_subuser(self, identifier: str, email: str, permissions: list[str]) -> str: ...
    async def remove_subuser(self, identifier: str, subuser_uuid: str) -> None: ...
    async def read_file(self, identifier: str, path: str) -> str: ...
    async def write_file(self, identifier: str, path: str, content: str) -> None: ...
    async def pull_file(self, identifier: str, url: str, directory: str = "/") -> None: ...


# ---------------------------------------------------------------------------
# Pterodactyl の実装
# ---------------------------------------------------------------------------
class _PteroClient(ServiceClient):
    def explain(self, res: httpx.Response, body: str) -> str:
        try:
            errs = res.json().get("errors") or []
            if errs:
                return " / ".join(str(e.get("detail") or e.get("code")) for e in errs)[:300]
        except ValueError:
            pass
        if res.status_code in (401, 403):
            return "API キーが無効か、権限が足りません"
        return f"HTTP {res.status_code}"


def _user(a: dict[str, Any]) -> PanelUser:
    return PanelUser(
        id=int(a["id"]),
        external_id=a.get("external_id"),
        username=a["username"],
        email=a["email"],
        first_name=a.get("first_name") or "",
        last_name=a.get("last_name") or "",
        root_admin=bool(a.get("root_admin")),
    )


def _server(a: dict[str, Any]) -> PanelServer:
    return PanelServer(
        id=int(a["id"]),
        uuid=a["uuid"],
        identifier=a["identifier"],
        external_id=a.get("external_id"),
        name=a["name"],
        suspended=bool(a.get("suspended")),
        allocation_id=a.get("allocation"),
        user_id=a.get("user"),
    )


def _backup(a: dict[str, Any]) -> Backup:
    return Backup(
        uuid=a["uuid"],
        name=a.get("name") or "",
        bytes=int(a.get("bytes") or 0),
        is_successful=bool(a.get("is_successful")),
        is_locked=bool(a.get("is_locked")),
        created_at=a.get("created_at") or "",
        completed_at=a.get("completed_at"),
    )


@dataclass
class PterodactylPanel:
    url: str
    app_key: str
    client_key: str
    client: httpx.AsyncClient | None = None
    _app: _PteroClient = field(init=False)
    _cli: _PteroClient = field(init=False)

    def __post_init__(self) -> None:
        self._app = _PteroClient(
            "ゲームパネル", self.url, {"Authorization": f"Bearer {self.app_key}"}, client=self.client
        )
        self._cli = _PteroClient(
            "ゲームパネル", self.url, {"Authorization": f"Bearer {self.client_key}"}, client=self.client
        )

    async def aclose(self) -> None:
        await self._app.aclose()
        await self._cli.aclose()

    # ---- ユーザー ----
    async def find_user(self, *, external_id: str | None = None, email: str | None = None) -> PanelUser | None:
        for key, val in (("external_id", external_id), ("email", email)):
            if not val:
                continue
            res = await self._app.request("GET", "/api/application/users", params={f"filter[{key}]": val})
            for item in res.json().get("data") or []:
                u = _user(item["attributes"])
                # 部分一致で返す版があるため、完全一致を確かめる
                if (key == "external_id" and u.external_id == val) or (
                    key == "email" and u.email.lower() == val.lower()
                ):
                    return u
        return None

    async def create_user(self, u: NewPanelUser) -> PanelUser:
        body: dict[str, Any] = {
            "external_id": u.external_id,
            "username": u.username,
            "email": u.email,
            "first_name": u.username,
            "last_name": u.username,
            "root_admin": u.root_admin,
        }
        if u.password:
            body["password"] = u.password
        res = await self._app.request("POST", "/api/application/users", json=body)
        return _user(res.json()["attributes"])

    async def update_user(self, panel_user_id: int, changes: PanelUserChanges) -> PanelUser:
        # 全項目を送らないと他の項目が消える（docs/EXTERNAL.md）ので、取得してから差分を当てる
        cur = await self._app.request("GET", f"/api/application/users/{panel_user_id}")
        a = cur.json()["attributes"]
        body: dict[str, Any] = {
            "email": changes.email or a["email"],
            "username": changes.username or a["username"],
            "first_name": a.get("first_name") or a["username"],
            "last_name": a.get("last_name") or a["username"],
            "root_admin": a.get("root_admin", False) if changes.root_admin is None else changes.root_admin,
            "language": a.get("language") or "en",
        }
        if a.get("external_id"):
            body["external_id"] = a["external_id"]
        if changes.password:
            body["password"] = changes.password
        res = await self._app.request("PATCH", f"/api/application/users/{panel_user_id}", json=body)
        return _user(res.json()["attributes"])

    async def delete_user(self, panel_user_id: int) -> None:
        await self._app.request("DELETE", f"/api/application/users/{panel_user_id}", ok=(204, 404))

    # ---- egg ----
    async def get_egg(self, nest: int, egg: int) -> Egg:
        res = await self._app.request(
            "GET", f"/api/application/nests/{nest}/eggs/{egg}", params={"include": "variables"}
        )
        a = res.json()["attributes"]
        variables = {
            v["attributes"]["env_variable"]: str(v["attributes"].get("default_value") or "")
            for v in (a.get("relationships", {}).get("variables", {}).get("data") or [])
        }
        image = a.get("docker_image") or next(iter((a.get("docker_images") or {}).values()), "")
        return Egg(id=int(a["id"]), docker_image=image, startup=a.get("startup") or "", variables=variables)

    # ---- アロケーション ----
    async def _allocations(self, node_id: int, port: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": 100, "filter[port]": port}
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            try:
                res = await self._app.request(
                    "GET", f"/api/application/nodes/{node_id}/allocations", params={**params, "page": page}
                )
            except UpstreamError as e:
                # filter[port] を受け付けない版では、絞り込みなしで全件を取る
                if e.status_code == 400 and "filter[port]" in params:
                    params.pop("filter[port]")
                    page, out = 1, []
                    continue
                raise
            body = res.json()
            out.extend(d["attributes"] for d in body.get("data") or [])
            pag = (body.get("meta") or {}).get("pagination") or {}
            if page >= int(pag.get("total_pages") or 1):
                return out
            page += 1

    async def _find_allocation(self, node_id: int, ip: str, port: int) -> dict[str, Any] | None:
        for a in await self._allocations(node_id, port):
            if int(a["port"]) == port and a["ip"] == ip:
                return a
        return None

    async def create_allocation(self, node_id: int, ip: str, port: int) -> int:
        """アロケーションを用意して ID を返す。既にあれば（空いていれば）それを使う。"""
        found = await self._find_allocation(node_id, ip, port)
        if found is None:
            # 作成の応答には ID が含まれない（204）ので、作った後にもう一度探す
            await self._app.request(
                "POST",
                f"/api/application/nodes/{node_id}/allocations",
                json={"ip": ip, "ports": [str(port)]},
                ok=(200, 201, 204),
            )
            found = await self._find_allocation(node_id, ip, port)
            if found is None:
                raise UpstreamError("ゲームパネル", f"アロケーション {ip}:{port} を作成しましたが見つかりません")
        if found.get("assigned"):
            raise AppError(
                "slot_taken", f"ポート {port} はゲームパネル上で別のサーバーが使っています。", 409, {"port": port}
            )
        return int(found["id"])

    async def delete_allocation(self, node_id: int, allocation_id: int) -> None:
        await self._app.request(
            "DELETE", f"/api/application/nodes/{node_id}/allocations/{allocation_id}", ok=(204, 404)
        )

    # ---- サーバー ----
    async def create_server(self, s: NewPanelServer) -> PanelServer:
        body = {
            "external_id": s.external_id,
            "name": s.name,
            "description": s.description,
            "user": s.user_id,
            "egg": s.egg,
            "docker_image": s.docker_image,
            "startup": s.startup,
            "environment": s.environment,
            "limits": {"memory": s.memory_mb, "swap": 0, "disk": s.disk_mb, "io": s.io, "cpu": s.cpu_percent},
            "feature_limits": {"databases": 0, "allocations": 1, "backups": s.backups},
            "allocation": {"default": s.allocation_id},
            "start_on_completion": False,
        }
        res = await self._app.request("POST", "/api/application/servers", json=body)
        return _server(res.json()["attributes"])

    async def find_server(self, external_id: str) -> PanelServer | None:
        res = await self._app.request("GET", f"/api/application/servers/external/{external_id}", ok=(200, 404))
        if res.status_code == 404:
            return None
        return _server(res.json()["attributes"])

    async def delete_server(self, panel_server_id: int) -> None:
        try:
            await self._app.request("DELETE", f"/api/application/servers/{panel_server_id}", ok=(204, 404))
        except UpstreamError:
            # Wings に届かないなどで通常の削除が失敗したら、強制削除を1回だけ試す
            await self._app.request("DELETE", f"/api/application/servers/{panel_server_id}/force", ok=(204, 404))

    async def suspend(self, panel_server_id: int) -> None:
        await self._app.request("POST", f"/api/application/servers/{panel_server_id}/suspend", ok=(204,))

    async def unsuspend(self, panel_server_id: int) -> None:
        await self._app.request("POST", f"/api/application/servers/{panel_server_id}/unsuspend", ok=(204,))

    # ---- Client API ----
    async def is_installing(self, identifier: str) -> bool:
        res = await self._cli.request("GET", f"/api/client/servers/{identifier}")
        a = res.json()["attributes"]
        return bool(a.get("is_installing")) or a.get("status") == "installing"

    async def power(self, identifier: str, signal: PowerSignal) -> None:
        await self._cli.request("POST", f"/api/client/servers/{identifier}/power", json={"signal": signal}, ok=(204,))

    async def resources(self, identifier: str) -> Resources:
        res = await self._cli.request("GET", f"/api/client/servers/{identifier}/resources")
        a = res.json()["attributes"]
        r = a.get("resources") or {}
        return Resources(
            state=a.get("current_state", "offline"),
            cpu_percent=float(r.get("cpu_absolute") or 0),
            memory_bytes=int(r.get("memory_bytes") or 0),
            disk_bytes=int(r.get("disk_bytes") or 0),
        )

    async def list_backups(self, identifier: str) -> list[Backup]:
        res = await self._cli.request("GET", f"/api/client/servers/{identifier}/backups", params={"per_page": 50})
        return [_backup(d["attributes"]) for d in res.json().get("data") or []]

    async def create_backup(self, identifier: str, name: str) -> Backup:
        res = await self._cli.request("POST", f"/api/client/servers/{identifier}/backups", json={"name": name})
        return _backup(res.json()["attributes"])

    async def delete_backup(self, identifier: str, backup_uuid: str) -> None:
        await self._cli.request("DELETE", f"/api/client/servers/{identifier}/backups/{backup_uuid}", ok=(204, 404))

    async def backup_download_url(self, identifier: str, backup_uuid: str) -> str:
        res = await self._cli.request("GET", f"/api/client/servers/{identifier}/backups/{backup_uuid}/download")
        return str(res.json()["attributes"]["url"])

    async def restore_backup(self, identifier: str, backup_uuid: str, *, truncate: bool = True) -> None:
        await self._cli.request(
            "POST",
            f"/api/client/servers/{identifier}/backups/{backup_uuid}/restore",
            json={"truncate": truncate},
            ok=(204,),
        )

    async def ensure_schedule(self, identifier: str, name: str, cron: Cron, action: str = "backup") -> int:
        res = await self._cli.request("GET", f"/api/client/servers/{identifier}/schedules")
        for d in res.json().get("data") or []:
            if d["attributes"]["name"] == name:
                return int(d["attributes"]["id"])
        res = await self._cli.request(
            "POST",
            f"/api/client/servers/{identifier}/schedules",
            json={
                "name": name,
                "minute": cron.minute,
                "hour": cron.hour,
                "day_of_month": cron.day_of_month,
                "month": cron.month,
                "day_of_week": cron.day_of_week,
                "is_active": True,
                "only_when_online": False,
            },
        )
        sid = int(res.json()["attributes"]["id"])
        await self._cli.request(
            "POST",
            f"/api/client/servers/{identifier}/schedules/{sid}/tasks",
            json={"action": action, "payload": "", "time_offset": 0},
        )
        return sid

    async def add_subuser(self, identifier: str, email: str, permissions: list[str]) -> str:
        res = await self._cli.request(
            "POST", f"/api/client/servers/{identifier}/users", json={"email": email, "permissions": permissions}
        )
        return str(res.json()["attributes"]["uuid"])

    async def remove_subuser(self, identifier: str, subuser_uuid: str) -> None:
        await self._cli.request("DELETE", f"/api/client/servers/{identifier}/users/{subuser_uuid}", ok=(204, 404))

    async def read_file(self, identifier: str, path: str) -> str:
        res = await self._cli.request("GET", f"/api/client/servers/{identifier}/files/contents", params={"file": path})
        return res.text

    async def write_file(self, identifier: str, path: str, content: str) -> None:
        await self._cli.request(
            "POST",
            f"/api/client/servers/{identifier}/files/write",
            params={"file": path},
            content=content.encode(),
            headers={"Content-Type": "text/plain"},
            ok=(204,),
        )

    async def pull_file(self, identifier: str, url: str, directory: str = "/") -> None:
        await self._cli.request(
            "POST", f"/api/client/servers/{identifier}/files/pull", json={"url": url, "directory": directory}, ok=(204,)
        )
