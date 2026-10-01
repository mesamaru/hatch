"""ドメイン・IP・紐付け（管理者）。docs/API.md「管理」。"""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import db
from ..adapters.dns import DnsAdapter
from ..auth.session import Principal, require_admin
from ..domain.names import domain_problem, host_label_problem
from ..domain.slots import expand
from ..errors import AppError, TransientError, UpstreamError
from ..jobs import bindings as _jobs  # noqa: F401 - ジョブの登録
from ..jobs.engine import enqueue
from ..repo import audit
from ..repo import slot_rules as rules_repo
from .deps import get_dns

router = APIRouter(prefix="/api/admin", tags=["domains"])


# ---------------------------------------------------------------------------
# ドメイン
# ---------------------------------------------------------------------------
class DomainIn(BaseModel):
    name: str = Field(min_length=3, max_length=253)
    cf_zone_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class DomainPatch(BaseModel):
    is_default: bool | None = None


@router.get("/domains")
async def list_domains(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT d.id, d.name, d.cf_zone_id, d.edge_host, d.is_default, d.verified_at,
                      (SELECT count(*) FROM slot_rules r WHERE r.domain_id = d.id) AS rules,
                      (SELECT count(*) FROM ip_bindings b WHERE b.domain_id = d.id) AS bindings
               FROM domains d ORDER BY d.name"""
        )
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.post("/domains", status_code=201)
async def add_domain(body: DomainIn, p: Principal = Depends(require_admin), dns: DnsAdapter = Depends(get_dns)) -> dict:
    name = body.name.strip().lower().rstrip(".")
    prob = domain_problem(name)
    if prob:
        raise AppError("validation", prob, 400, {"fields": {"name": prob}})
    try:
        zone_name = await dns.verify_zone(body.cf_zone_id)
    except (UpstreamError, TransientError):
        raise AppError(
            "zone_unreachable",
            "Cloudflare のゾーンを確認できませんでした。ゾーンIDと API トークンの権限を確認してください。",
            400,
        ) from None
    if zone_name.lower() != name:
        raise AppError(
            "zone_mismatch", f"このゾーンIDは {zone_name} のものです。{name} のゾーンIDを入力してください。", 400
        )
    try:
        await dns.check_edit(body.cf_zone_id, name)
    except UpstreamError as e:
        raise AppError("dns_permission", e.message, 400) from None
    except TransientError:
        raise AppError(
            "upstream_timeout", "Cloudflare が応答しません。少し待ってからもう一度試してください。", 504
        ) from None
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT 1 FROM domains WHERE name = %s", (name,))
        if await cur.fetchone():
            raise AppError("already_exists", "このドメインは登録済みです。", 409)
        cur = await conn.execute("SELECT NOT EXISTS (SELECT 1 FROM domains) AS first")
        first = (await cur.fetchone())["first"]
        cur = await conn.execute(
            "INSERT INTO domains (name, cf_zone_id, is_default, verified_at) VALUES (%s,%s,%s, now()) RETURNING id",
            (name, body.cf_zone_id, first),
        )
        did = (await cur.fetchone())["id"]
        cur = await conn.execute(
            "INSERT INTO ip_bindings (domain_id, host, follow_active_edge) VALUES (%s, 'edge', true) RETURNING id",
            (did,),
        )
        bid = (await cur.fetchone())["id"]
        job = await enqueue(conn, "sync_binding", via="web", params={"binding_id": bid}, requested_by=p.user_id)
        await audit.add(conn, action="ドメインを追加", via="web", actor_id=p.user_id, target=name, job_id=job)
    return {"id": did, "name": name, "is_default": first, "edge_binding_id": bid, "job": {"id": job}}


@router.patch("/domains/{domain_id}")
async def patch_domain(domain_id: int, body: DomainPatch, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT name FROM domains WHERE id = %s FOR UPDATE", (domain_id,))
        row = await cur.fetchone()
        if row is None:
            raise AppError("not_found", "ドメインが見つかりません。", 404)
        if body.is_default:
            await conn.execute("UPDATE domains SET is_default = false WHERE is_default AND id <> %s", (domain_id,))
            await conn.execute("UPDATE domains SET is_default = true WHERE id = %s", (domain_id,))
            await audit.add(conn, action="既定のドメインを変更", via="web", actor_id=p.user_id, target=row["name"])
    return {"id": domain_id, "is_default": bool(body.is_default)}


@router.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(domain_id: int, p: Principal = Depends(require_admin)) -> Response:
    """アドレス枠が無ければ外せる。紐付け（edge.<ドメイン> など）が残っていれば、
    A レコードを消してから外すジョブにする。
    """
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT d.name, (SELECT count(*) FROM slot_rules r WHERE r.domain_id = d.id) AS rules,
                      (SELECT count(*) FROM ip_bindings b WHERE b.domain_id = d.id) AS bindings
               FROM domains d WHERE d.id = %s FOR UPDATE""",
            (domain_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise AppError("not_found", "ドメインが見つかりません。", 404)
        if row["rules"]:
            raise AppError("in_use", "このドメインのアドレス枠を先に削除してください。", 409)
        if row["bindings"]:
            job = await enqueue(
                conn, "delete_domain", via="web", params={"domain_id": domain_id}, requested_by=p.user_id
            )
            await audit.add(
                conn, action="ドメインを削除", via="web", actor_id=p.user_id, target=row["name"], job_id=job
            )
            return JSONResponse({"job": {"id": job, "kind": "delete_domain", "status": "queued"}}, status_code=202)
        await conn.execute("DELETE FROM domains WHERE id = %s", (domain_id,))
        await audit.add(conn, action="ドメインを削除", via="web", actor_id=p.user_id, target=row["name"])
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# IP アドレス
# ---------------------------------------------------------------------------
class IpIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    address: str
    edge_id: str | None = None


@router.get("/ips")
async def list_ips(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT id, label, host(address) AS address, edge_id FROM ip_addresses ORDER BY id")
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.post("/ips", status_code=201)
async def add_ip(body: IpIn, p: Principal = Depends(require_admin)) -> dict:
    try:
        ip = ipaddress.ip_address(body.address.strip())
    except ValueError:
        raise AppError("validation", "IP アドレスの形式が正しくありません。", 400) from None
    if ip.version != 4 or ip.is_private or ip.is_loopback or ip.is_reserved:
        raise AppError("validation", "公開されている IPv4 アドレスを入力してください。", 400)
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT 1 FROM ip_addresses WHERE address = %s", (str(ip),))
        if await cur.fetchone():
            raise AppError("already_exists", "この IP は登録済みです。", 409)
        cur = await conn.execute(
            "INSERT INTO ip_addresses (label, address, edge_id) VALUES (%s,%s,%s) RETURNING id",
            (body.label, str(ip), body.edge_id),
        )
        iid = (await cur.fetchone())["id"]
        await audit.add(conn, action="IP を追加", via="web", actor_id=p.user_id, target=str(ip))
    return {"id": iid, "label": body.label, "address": str(ip), "edge_id": body.edge_id}


@router.delete("/ips/{ip_id}", status_code=204)
async def delete_ip(ip_id: int, p: Principal = Depends(require_admin)) -> Response:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT host(address) AS address,
                      (SELECT count(*) FROM ip_bindings WHERE ip_id = %s)
                        + (SELECT count(*) FROM slot_rules WHERE ip_id = %s) AS used
               FROM ip_addresses WHERE id = %s""",
            (ip_id, ip_id, ip_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise AppError("not_found", "IP が見つかりません。", 404)
        if row["used"]:
            raise AppError("in_use", "この IP を使っている紐付け・アドレス枠を先に変更してください。", 409)
        await conn.execute("DELETE FROM ip_addresses WHERE id = %s", (ip_id,))
        await audit.add(conn, action="IP を削除", via="web", actor_id=p.user_id, target=row["address"])
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# IP の紐付け（ホスト名 → IP の A レコード）
# ---------------------------------------------------------------------------
class BindingIn(BaseModel):
    domain_id: int
    host: str = Field(min_length=1, max_length=63)
    ip_id: int | None = None
    follow_active_edge: bool = False


@router.get("/bindings")
async def list_bindings(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT b.id, b.domain_id, d.name AS domain, b.host, b.ip_id, host(ip.address) AS ip,
                      b.follow_active_edge, b.synced_at
               FROM ip_bindings b JOIN domains d ON d.id = b.domain_id LEFT JOIN ip_addresses ip ON ip.id = b.ip_id
               ORDER BY d.name, b.host"""
        )
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.post("/bindings", status_code=202)
async def add_binding(body: BindingIn, p: Principal = Depends(require_admin)) -> dict:
    host = body.host.strip().lower()
    if host != "@":
        prob = host_label_problem(host, set())
        if prob:
            raise AppError("validation", prob, 400, {"fields": {"host": prob}})
    if not body.follow_active_edge and body.ip_id is None:
        raise AppError("validation", "向き先の IP を選ぶか、「使用中の edge に追従」を選んでください。", 400)
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT 1 FROM ip_bindings WHERE domain_id = %s AND host = %s", (body.domain_id, host))
        if await cur.fetchone():
            raise AppError("already_exists", "同じホスト名の紐付けがあります。", 409)
        for r in await rules_repo.all_rules(conn):
            if (
                r.domain_id == body.domain_id
                and not r.uses_server_name
                and any(s.host == host and not s.excluded for s in expand(r))
            ):
                raise AppError("in_use", f"このホスト名はアドレス枠「{r.name}」で使われています。", 409)
        if body.ip_id is not None:
            cur = await conn.execute("SELECT 1 FROM ip_addresses WHERE id = %s", (body.ip_id,))
            if not await cur.fetchone():
                raise AppError("validation", "IP が見つかりません。", 400)
        cur = await conn.execute(
            "INSERT INTO ip_bindings (domain_id, host, ip_id, follow_active_edge) VALUES (%s,%s,%s,%s) RETURNING id",
            (body.domain_id, host, None if body.follow_active_edge else body.ip_id, body.follow_active_edge),
        )
        bid = (await cur.fetchone())["id"]
        job = await enqueue(conn, "sync_binding", via="web", params={"binding_id": bid}, requested_by=p.user_id)
        await audit.add(conn, action="IP の紐付けを追加", via="web", actor_id=p.user_id, target=host, job_id=job)
    return {"id": bid, "job": {"id": job, "kind": "sync_binding", "status": "queued"}}


@router.post("/bindings/{binding_id}/sync", status_code=202)
async def resync_binding(binding_id: int, p: Principal = Depends(require_admin)) -> dict:
    """A レコードを今の設定で作り直す（edge を後から登録したとき、権限を直したときなど）。"""
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT host FROM ip_bindings WHERE id = %s", (binding_id,))
        row = await cur.fetchone()
        if row is None:
            raise AppError("not_found", "紐付けが見つかりません。", 404)
        job = await enqueue(conn, "sync_binding", via="web", params={"binding_id": binding_id}, requested_by=p.user_id)
        await audit.add(
            conn, action="IP の紐付けを反映し直す", via="web", actor_id=p.user_id, target=row["host"], job_id=job
        )
    return {"job": {"id": job, "kind": "sync_binding", "status": "queued"}}


@router.delete("/bindings/{binding_id}", status_code=202)
async def delete_binding(binding_id: int, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT b.host, b.domain_id, d.edge_host,
                      EXISTS (SELECT 1 FROM slot_rules r
                              WHERE r.domain_id = b.domain_id AND r.record_mode = 'cname_edge')
                        AS edge_used
               FROM ip_bindings b JOIN domains d ON d.id = b.domain_id WHERE b.id = %s""",
            (binding_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise AppError("not_found", "紐付けが見つかりません。", 404)
        if row["host"] == row["edge_host"] and row["edge_used"]:
            raise AppError("in_use", "アドレス枠がこの名前（edge）に向いているため削除できません。", 409)
        job = await enqueue(
            conn, "delete_binding", via="web", params={"binding_id": binding_id}, requested_by=p.user_id
        )
        await audit.add(conn, action="IP の紐付けを削除", via="web", actor_id=p.user_id, target=row["host"], job_id=job)
    return {"job": {"id": job, "kind": "delete_binding", "status": "queued"}}
