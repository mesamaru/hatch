"""Linode のフェイク（メモリ上）。ルールの上限（25個）も本物と同じように検証する。"""

from __future__ import annotations

import copy
from typing import Any

from hatch.adapters.linode import LinodeFirewall, LinodeInstance
from hatch.errors import UpstreamError


class FakeLinodeCloud:
    """1つの契約（トークン）の中身。LinodeAdapter は FakeLinode(cloud) で作る。"""

    def __init__(self, token: str = "tok") -> None:
        self.token = token
        self.instances = [LinodeInstance(101, "edge-1", "ap-northeast", ("45.33.1.10",))]
        self.fw: dict[int, dict[str, Any]] = {
            501: {
                "label": "hatch-edge",
                "rules": {
                    "inbound": [
                        {"label": "ssh", "action": "ACCEPT", "protocol": "TCP", "ports": "22", "addresses": {}}
                    ],
                    "outbound": [],
                    "inbound_policy": "DROP",
                    "outbound_policy": "ACCEPT",
                },
                "devices": set(),
            }
        }
        self.puts = 0
        self.read_only_tokens: set[str] = set()  # Linodes が Read Only のトークン（一覧は読めるが付けられない）


class FakeLinode:
    def __init__(self, cloud: FakeLinodeCloud, token: str) -> None:
        self.cloud = cloud
        self.read_only = token in cloud.read_only_tokens
        self.ok = token == cloud.token or self.read_only

    def _auth(self) -> None:
        if not self.ok:  # 本物と同じく 401 を返す
            raise UpstreamError("Linode", "Invalid Token", 401)

    async def check(self) -> None:
        self._auth()

    async def linodes(self):
        self._auth()
        return list(self.cloud.instances)

    async def firewalls(self):
        self._auth()
        return [LinodeFirewall(i, f["label"], "enabled") for i, f in self.cloud.fw.items()]

    async def get_rules(self, firewall_id: int):
        self._auth()
        return copy.deepcopy(self.cloud.fw[firewall_id]["rules"])

    async def put_rules(self, firewall_id: int, rules):
        self._auth()
        total = len(rules.get("inbound", [])) + len(rules.get("outbound", []))
        if total > 25:
            raise UpstreamError("Linode", "rules: too many", 400)
        self.cloud.fw[firewall_id]["rules"] = copy.deepcopy(rules)
        self.cloud.puts += 1

    async def device_linode_ids(self, firewall_id: int):
        self._auth()
        return set(self.cloud.fw[firewall_id]["devices"])

    async def attach_linode(self, firewall_id: int, linode_id: int):
        self._auth()
        if self.read_only:  # 本物は、その Linode への read_write が無いと 401
            raise UpstreamError("Linode", "Unauthorized", 401)
        self.cloud.fw[firewall_id]["devices"].add(linode_id)

    async def aclose(self) -> None:
        return None


def hatch_ports(cloud: FakeLinodeCloud, firewall_id: int = 501) -> dict[str, str]:
    """Hatch のルールのポート（プロトコル → "25561-25563,30000"）。テストの確認用。"""
    out: dict[str, list[str]] = {}
    for r in cloud.fw[firewall_id]["rules"]["inbound"]:
        if str(r.get("label", "")).startswith("hatch-"):
            out.setdefault(r["protocol"].lower(), []).append(r["ports"])
    return {k: ",".join(v) for k, v in out.items()}
