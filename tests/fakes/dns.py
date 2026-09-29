"""Cloudflare のフェイク。upsert の判断ロジックは本物（CloudflareDns）をそのまま使い、通信だけをメモリで置き換える。"""

from __future__ import annotations

import itertools
from typing import Any

from pterodeploy.adapters.dns import CloudflareDns, DnsRecord, _parse
from pterodeploy.errors import TransientError


class FakeDns(CloudflareDns):
    def __init__(self, instance: str = "test", zones: dict[str, str] | None = None):
        super().__init__("fake", instance)
        self.zones = zones or {"z1": "nuids.jp"}
        self.records: dict[str, dict[str, dict[str, Any]]] = {z: {} for z in self.zones}
        self._ids = itertools.count(1)
        self.fail_next: list[Exception] = []  # 次の呼び出しで投げる例外（テスト用）
        self.calls: list[str] = []

    def _maybe_fail(self, op: str) -> None:
        self.calls.append(op)
        if self.fail_next:
            raise self.fail_next.pop(0)

    def add_manual(self, zone: str, type_: str, name: str, content: str, comment: str = "") -> str:
        rid = f"m{next(self._ids)}"
        self.records[zone][rid] = {
            "id": rid,
            "type": type_,
            "name": name,
            "content": content,
            "comment": comment,
            "proxied": False,
            "ttl": 300,
        }
        return rid

    async def verify_zone(self, zone_id: str) -> str:
        self._maybe_fail("verify")
        if zone_id not in self.zones:
            from pterodeploy.errors import UpstreamError

            raise UpstreamError("Cloudflare", "7003: Could not route to /zones/x", 400)
        return self.zones[zone_id]

    async def list_managed(self, zone_id: str) -> list[DnsRecord]:
        self._maybe_fail("list")
        return [r for r in map(_parse, self.records[zone_id].values()) if self.is_ours(r)]

    async def find(self, zone_id: str, name: str) -> list[DnsRecord]:
        self._maybe_fail("find")
        return [_parse(r) for r in self.records[zone_id].values() if r["name"] == name]

    async def _create(self, zone_id: str, body: dict[str, Any]) -> DnsRecord:
        self._maybe_fail("create")
        rid = f"r{next(self._ids)}"
        self.records[zone_id][rid] = {"id": rid, "proxied": False, **body}
        return _parse(self.records[zone_id][rid])

    async def _update(self, zone_id: str, record_id: str, body: dict[str, Any]) -> DnsRecord:
        self._maybe_fail("update")
        self.records[zone_id][record_id].update(body)
        return _parse(self.records[zone_id][record_id])

    async def delete(self, zone_id: str, record_id: str) -> None:
        self._maybe_fail("delete")
        self.records[zone_id].pop(record_id, None)

    def names(self, zone: str = "z1") -> set[tuple[str, str]]:
        return {(r["type"], r["name"]) for r in self.records[zone].values()}


__all__ = ["FakeDns", "TransientError"]
