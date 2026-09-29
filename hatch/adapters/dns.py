"""DNS（Cloudflare）。docs/EXTERNAL.md 2章。

扱うのは comment が `hatch:<PD_INSTANCE>` で始まるレコードだけ。
手動で作ったレコードや別インスタンス（テスト環境など）のレコードには触らない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from ..errors import AppError
from .http import ServiceClient

RecordType = Literal["A", "CNAME", "SRV"]
CF_API = "https://api.cloudflare.com/client/v4"


@dataclass(frozen=True)
class Srv:
    priority: int
    weight: int
    port: int
    target: str


@dataclass(frozen=True)
class DnsRecordSpec:
    type: RecordType
    name: str  # FQDN（例 mc05trt.nuids.jp / _minecraft._tcp.mc05trt.nuids.jp）
    comment: str  # 例 "slot=25565"。先頭に "hatch:<instance> " を自動で付ける
    content: str | None = None  # A: IP、CNAME: 向き先
    srv: Srv | None = None
    ttl: int = 60


@dataclass(frozen=True)
class DnsRecord:
    id: str
    type: str
    name: str
    content: str | None
    srv: Srv | None
    comment: str
    proxied: bool
    ttl: int


class DnsConflict(AppError):
    def __init__(self, name: str, what: str):
        super().__init__(
            "dns_conflict",
            f"{name} に{what}があるため、作成できません。Cloudflare で確認してください。",
            409,
            {"name": name},
        )


class DnsAdapter(Protocol):
    comment_prefix: str

    async def verify_zone(self, zone_id: str) -> str: ...
    async def list_managed(self, zone_id: str) -> list[DnsRecord]: ...
    async def upsert(self, zone_id: str, spec: DnsRecordSpec) -> DnsRecord: ...
    async def delete(self, zone_id: str, record_id: str) -> None: ...
    async def find(self, zone_id: str, name: str) -> list[DnsRecord]: ...


def _parse(r: dict[str, Any]) -> DnsRecord:
    srv = None
    if r.get("type") == "SRV" and isinstance(r.get("data"), dict):
        d = r["data"]
        srv = Srv(int(d.get("priority", 0)), int(d.get("weight", 0)), int(d.get("port", 0)), str(d.get("target", "")))
    return DnsRecord(
        id=r["id"],
        type=r["type"],
        name=r["name"],
        content=r.get("content"),
        srv=srv,
        comment=r.get("comment") or "",
        proxied=bool(r.get("proxied")),
        ttl=int(r.get("ttl") or 1),
    )


def same_content(rec: DnsRecord, spec: DnsRecordSpec, comment: str) -> bool:
    if rec.type != spec.type or rec.comment != comment or rec.ttl != spec.ttl or rec.proxied:
        return False
    if spec.type == "SRV":
        return rec.srv == spec.srv
    return (rec.content or "").rstrip(".").lower() == (spec.content or "").rstrip(".").lower()


class CloudflareDns:
    def __init__(self, token: str, instance: str, *, client: httpx.AsyncClient | None = None, base_url: str = CF_API):
        self.http = _CfClient("Cloudflare", base_url, {"Authorization": f"Bearer {token}"}, client=client)
        self.comment_prefix = f"hatch:{instance}"

    def full_comment(self, comment: str) -> str:
        return f"{self.comment_prefix} {comment}".strip()

    def is_ours(self, rec: DnsRecord) -> bool:
        return rec.comment == self.comment_prefix or rec.comment.startswith(self.comment_prefix + " ")

    async def verify_zone(self, zone_id: str) -> str:
        res = await self.http.request("GET", f"/zones/{zone_id}")
        return str(res.json()["result"]["name"])

    async def list_managed(self, zone_id: str) -> list[DnsRecord]:
        out: list[DnsRecord] = []
        page = 1
        while True:
            res = await self.http.request(
                "GET",
                f"/zones/{zone_id}/dns_records",
                params={"comment.startswith": self.comment_prefix, "per_page": 100, "page": page},
            )
            data = res.json()
            out.extend(r for r in map(_parse, data.get("result") or []) if self.is_ours(r))
            info = data.get("result_info") or {}
            if page >= int(info.get("total_pages") or 1):
                return out
            page += 1

    async def find(self, zone_id: str, name: str) -> list[DnsRecord]:
        res = await self.http.request("GET", f"/zones/{zone_id}/dns_records", params={"name": name, "per_page": 100})
        return [_parse(r) for r in res.json().get("result") or []]

    def _body(self, spec: DnsRecordSpec) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": spec.type,
            "name": spec.name,
            "ttl": spec.ttl,
            "comment": self.full_comment(spec.comment),
        }
        if spec.type == "SRV":
            if spec.srv is None:
                raise ValueError("SRV には srv が必要です")
            s = spec.srv
            body["data"] = {"priority": s.priority, "weight": s.weight, "port": s.port, "target": s.target}
        else:
            if not spec.content:
                raise ValueError(f"{spec.type} には content が必要です")
            body["content"] = spec.content
            body["proxied"] = False  # ゲームの通信は Cloudflare のプロキシを通せない
        return body

    async def upsert(self, zone_id: str, spec: DnsRecordSpec) -> DnsRecord:
        existing = await self.find(zone_id, spec.name)
        foreign = [r for r in existing if not self.is_ours(r) and _conflicts(r.type, spec.type)]
        if foreign:
            raise DnsConflict(spec.name, "手動で作られたレコード（または別の環境のレコード）")
        ours = [r for r in existing if self.is_ours(r)]
        # 種類が変わった（A ⇔ CNAME）自分のレコードは消す。CNAME は他の種類と共存できない
        for r in ours:
            if r.type != spec.type and _conflicts(r.type, spec.type):
                await self.delete(zone_id, r.id)
        same = [r for r in ours if r.type == spec.type]
        for extra in same[1:]:
            await self.delete(zone_id, extra.id)
        body = self._body(spec)
        if same:
            if same_content(same[0], spec, body["comment"]):
                return same[0]
            return await self._update(zone_id, same[0].id, body)
        return await self._create(zone_id, body)

    # ---- 通信の最小単位（フェイクはここを差し替える） ----
    async def _create(self, zone_id: str, body: dict[str, Any]) -> DnsRecord:
        res = await self.http.request("POST", f"/zones/{zone_id}/dns_records", json=body)
        return _parse(res.json()["result"])

    async def _update(self, zone_id: str, record_id: str, body: dict[str, Any]) -> DnsRecord:
        res = await self.http.request("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", json=body)
        return _parse(res.json()["result"])

    async def delete(self, zone_id: str, record_id: str) -> None:
        await self.http.request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}", ok=(200, 204, 404))

    async def aclose(self) -> None:
        await self.http.aclose()


def _conflicts(a: str, b: str) -> bool:
    """同じ名前に同時に置けない組み合わせか（CNAME は何とも共存できない。SRV は名前が別なので通常は重ならない）。"""
    if a == b:
        return True
    return "CNAME" in (a, b)


class _CfClient(ServiceClient):
    def explain(self, res: httpx.Response, body: str) -> str:
        try:
            errs = res.json().get("errors") or []
            if errs:
                return " / ".join(f"{e.get('code')}: {e.get('message')}" for e in errs)[:300]
        except ValueError:
            pass
        if res.status_code in (401, 403):
            return "API トークンの権限が足りません（Zone:Read と DNS:Edit が必要です）"
        return f"HTTP {res.status_code}"


def slot_records(
    host: str, domain: str, port: int, *, mode: str, edge_host: str, ip: str | None, srv: bool
) -> list[DnsRecordSpec]:
    """スロット1つ分のレコード。docs/IMPLEMENTATION.md 4.4。"""
    fqdn = f"{host}.{domain}"
    edge_fqdn = f"{edge_host}.{domain}"
    comment = f"slot={port}"
    if mode == "cname_edge":
        out = [DnsRecordSpec("CNAME", fqdn, comment, content=edge_fqdn)]
        target = edge_fqdn
    elif mode == "a_ip":
        if not ip:
            raise ValueError("a_ip には ip が必要です")
        out = [DnsRecordSpec("A", fqdn, comment, content=ip)]
        target = fqdn  # SRV の向き先は A レコードのホスト名にする（CNAME を向けない）
    else:
        raise ValueError(f"不明な record_mode: {mode}")
    if srv:
        out.append(DnsRecordSpec("SRV", f"_minecraft._tcp.{fqdn}", comment, srv=Srv(0, 5, port, target)))
    return out
