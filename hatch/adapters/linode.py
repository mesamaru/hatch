"""Linode（Akamai Cloud）の API。Cloud Firewall のルールと、edge の Linode の一覧。docs/EXTERNAL.md 7章。

- 認証は Personal Access Token（Linodes 読み取り・Firewalls 読み書き）。契約ごとに1つ。
- ファイアウォールのルールは PUT で「全体を置き換える」API なので、呼び出し側が全体を組み立てて渡す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .http import ServiceClient

API = "https://api.linode.com/v4"


@dataclass(frozen=True)
class LinodeInstance:
    id: int
    label: str
    region: str
    ipv4: tuple[str, ...]


@dataclass(frozen=True)
class LinodeFirewall:
    id: int
    label: str
    status: str


class LinodeAdapter(Protocol):
    async def check(self) -> None: ...
    async def linodes(self) -> list[LinodeInstance]: ...
    async def firewalls(self) -> list[LinodeFirewall]: ...
    async def get_rules(self, firewall_id: int) -> dict[str, Any]: ...
    async def put_rules(self, firewall_id: int, rules: dict[str, Any]) -> None: ...
    async def device_linode_ids(self, firewall_id: int) -> set[int]: ...
    async def attach_linode(self, firewall_id: int, linode_id: int) -> None: ...
    async def aclose(self) -> None: ...


class LinodeClient:
    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None):
        self.http = ServiceClient("Linode", API, {"Authorization": f"Bearer {token}"}, client=client)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _all(self, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            res = (await self.http.request("GET", path, params={"page": page, "page_size": 500})).json()
            out.extend(res.get("data", []))
            if page >= int(res.get("pages", 1)):
                return out
            page += 1

    async def check(self) -> None:
        """トークンが使えるか（Linodes の読み取りと Firewalls の読み取り）。"""
        await self.http.request("GET", "/linode/instances", params={"page_size": 25})
        await self.http.request("GET", "/networking/firewalls", params={"page_size": 25})

    async def linodes(self) -> list[LinodeInstance]:
        return [
            LinodeInstance(int(x["id"]), str(x["label"]), str(x.get("region") or ""), tuple(x.get("ipv4") or ()))
            for x in await self._all("/linode/instances")
        ]

    async def firewalls(self) -> list[LinodeFirewall]:
        return [
            LinodeFirewall(int(x["id"]), str(x["label"]), str(x.get("status") or ""))
            for x in await self._all("/networking/firewalls")
        ]

    async def get_rules(self, firewall_id: int) -> dict[str, Any]:
        return (await self.http.request("GET", f"/networking/firewalls/{firewall_id}/rules")).json()

    async def put_rules(self, firewall_id: int, rules: dict[str, Any]) -> None:
        await self.http.request("PUT", f"/networking/firewalls/{firewall_id}/rules", json=rules)

    async def device_linode_ids(self, firewall_id: int) -> set[int]:
        devices = await self._all(f"/networking/firewalls/{firewall_id}/devices")
        return {int(d["entity"]["id"]) for d in devices if (d.get("entity") or {}).get("type") == "linode"}

    async def attach_linode(self, firewall_id: int, linode_id: int) -> None:
        await self.http.request(
            "POST", f"/networking/firewalls/{firewall_id}/devices", json={"type": "linode", "id": linode_id}
        )
