"""アドレス枠・スロット。docs/API.md「管理」「アドレス」。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from .. import db
from ..auth.session import Principal, require_admin, require_user
from ..domain.slots import SlotRule, validate
from ..errors import AppError
from ..jobs import publish_slots as _jobs  # noqa: F401 - ジョブの登録
from ..jobs.engine import enqueue
from ..repo import audit
from ..repo import slot_rules as repo

router = APIRouter(tags=["slots"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    domain_id: int
    port_start: int
    port_end: int
    host_template: str = Field(min_length=1, max_length=63)
    number_start: int = 0
    excluded_ports: list[int] = Field(default_factory=list, max_length=200)
    record_mode: Literal["cname_edge", "a_ip"] = "cname_edge"
    ip_id: int | None = None
    create_srv: bool = True
    prepublish: bool = True
    assign_to: Literal["shared", "user"] = "shared"
    user_id: str | None = None


async def _domain_name(conn, domain_id: int) -> str:
    cur = await conn.execute("SELECT name FROM domains WHERE id = %s", (domain_id,))
    row = await cur.fetchone()
    if row is None:
        raise AppError("validation", "ドメインが見つかりません。", 400, {"fields": {"domain_id": "選べない値です"}})
    return row["name"]


async def _check(conn, body: RuleIn, rule_id: int | None):
    domain = await _domain_name(conn, body.domain_id)
    rule = SlotRule(
        id=rule_id, domain=domain, **{**body.model_dump(), "excluded_ports": tuple(sorted(set(body.excluded_ports)))}
    )
    others = [r for r in await repo.all_rules(conn) if r.id != rule_id]
    original = None
    in_use: set[int] = set()
    if rule_id is not None:
        row = await repo.get_rule(conn, rule_id, lock=True)
        if row is None:
            raise AppError("not_found", "アドレス枠が見つかりません。", 404)
        original = repo.to_rule(row)
        in_use = await repo.in_use_ports(conn, rule_id)
    v = validate(rule, others, await repo.bindings(conn), await repo.reserved(conn), in_use, original)
    return rule, v


def _rule_out(row) -> dict:
    keys = (
        "id",
        "name",
        "domain_id",
        "domain",
        "port_start",
        "port_end",
        "host_template",
        "number_start",
        "excluded_ports",
        "record_mode",
        "ip_id",
        "create_srv",
        "prepublish",
        "assign_to",
        "user_id",
    )
    out = {k: row[k] for k in keys}
    if "total" in row:
        out["stats"] = {k: row[k] for k in ("total", "free", "assigned", "held", "disabled")}
        out["dns_published"] = row["dns_published"]
    return out


async def _with_stats(conn, rule_id: int) -> dict:
    return _rule_out(next(r for r in await repo.list_rules(conn) if r["id"] == rule_id))


@router.get("/api/admin/slot-rules")
async def list_rules(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        return {"items": [_rule_out(r) for r in await repo.list_rules(conn)]}


@router.post("/api/admin/slot-rules/preview")
async def preview(
    body: RuleIn, rule_id: int | None = Query(default=None), p: Principal = Depends(require_admin)
) -> dict:
    async with db.transaction() as conn:
        rule, v = await _check(conn, body, rule_id)
    return {
        "problems": v.problems,
        "slots": [{"port": s.port, "fqdn": s.fqdn(rule.domain), "excluded": s.excluded} for s in v.slots[:200]],
    }


@router.post("/api/admin/slot-rules", status_code=201)
async def create_rule(body: RuleIn, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        rule, v = await _check(conn, body, None)
        if not v.ok:
            raise AppError("rule_invalid", "アドレス枠の内容を確認してください。", 400, {"problems": v.problems})
        rid = await repo.insert_rule(conn, rule)
        await repo.sync_slots(conn, SlotRule(**{**rule.__dict__, "id": rid}))
        await audit.add(conn, action="アドレス枠を追加", via="web", actor_id=p.user_id, target=rule.name)
        return await _with_stats(conn, rid)


@router.get("/api/admin/slot-rules/{rule_id}")
async def get_rule(rule_id: int, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        row = await repo.get_rule(conn, rule_id)
        if row is None:
            raise AppError("not_found", "アドレス枠が見つかりません。", 404)
        return {**_rule_out(row), "slots": await repo.slots_of(conn, rule_id)}


@router.patch("/api/admin/slot-rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        rule, v = await _check(conn, body, rule_id)
        if not v.ok:
            raise AppError("rule_invalid", "アドレス枠の内容を確認してください。", 400, {"problems": v.problems})
        await repo.update_rule(conn, rule)
        changes = await repo.sync_slots(conn, rule)
        await audit.add(
            conn, action="アドレス枠を変更", via="web", actor_id=p.user_id, target=rule.name, detail=changes
        )
        return {
            **(await _with_stats(conn, rule_id)),
            "changes": changes,
            "needs_republish": bool(changes["added"] or changes["changed"]) and rule.prepublish,
        }


@router.delete("/api/admin/slot-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, p: Principal = Depends(require_admin)) -> Response:
    async with db.transaction() as conn:
        row = await repo.get_rule(conn, rule_id, lock=True)
        if row is None:
            raise AppError("not_found", "アドレス枠が見つかりません。", 404)
        if await repo.in_use_ports(conn, rule_id):
            raise AppError("in_use", "使用中またはゴミ箱のスロットがあるため削除できません。", 409)
        cur = await conn.execute("SELECT 1 FROM slots WHERE rule_id = %s AND dns_state <> 'none' LIMIT 1", (rule_id,))
        if await cur.fetchone():
            raise AppError("in_use", "先に「DNS を削除」を実行してください。", 409)
        await conn.execute("DELETE FROM slots WHERE rule_id = %s", (rule_id,))
        await conn.execute("DELETE FROM slot_rules WHERE id = %s", (rule_id,))
        await audit.add(conn, action="アドレス枠を削除", via="web", actor_id=p.user_id, target=row["name"])
    return Response(status_code=204)


async def _enqueue_dns(rule_id: int, kind: str, p: Principal, action: str) -> dict:
    async with db.transaction() as conn:
        row = await repo.get_rule(conn, rule_id)
        if row is None:
            raise AppError("not_found", "アドレス枠が見つかりません。", 404)
        if kind == "publish_slots" and not row["prepublish"]:
            raise AppError("validation", "この枠は DNS を先に作成しない設定です。", 400)
        job = await enqueue(conn, kind, via="web", params={"rule_id": rule_id}, requested_by=p.user_id)
        await audit.add(conn, action=action, via="web", actor_id=p.user_id, target=row["name"], job_id=job)
    return {"job": {"id": job, "kind": kind, "status": "queued"}}


@router.post("/api/admin/slot-rules/{rule_id}/publish", status_code=202)
async def publish(rule_id: int, p: Principal = Depends(require_admin)) -> dict:
    return await _enqueue_dns(rule_id, "publish_slots", p, "DNS を事前作成")


@router.post("/api/admin/slot-rules/{rule_id}/unpublish", status_code=202)
async def unpublish(rule_id: int, p: Principal = Depends(require_admin)) -> dict:
    return await _enqueue_dns(rule_id, "unpublish_slots", p, "事前作成した DNS を削除")


class SlotPatch(BaseModel):
    status: Literal["free", "disabled"]


@router.patch("/api/admin/slots/{port}")
async def patch_slot(port: int, body: SlotPatch, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            "UPDATE slots SET status = %s WHERE port = %s AND status IN ('free','disabled') RETURNING port, status",
            (body.status, port),
        )
        row = await cur.fetchone()
        if row is None:
            raise AppError("in_use", "使用中・ゴミ箱のスロットは変更できません。", 409)
        await audit.add(
            conn,
            action="スロットを停止" if body.status == "disabled" else "スロットを再開",
            via="web",
            actor_id=p.user_id,
            target=str(port),
        )
    return dict(row)


@router.get("/api/slots/available")
async def available(owner_id: str | None = Query(default=None), p: Principal = Depends(require_user)) -> dict:
    owner = owner_id or p.user_id
    if owner != p.user_id and p.role != "admin":
        raise AppError("forbidden", "他の人の選択肢は見られません。", 403)
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT sl.port, sl.host, sl.dns_state, r.id AS rule_id, r.name AS rule_name, r.host_template,
                      d.name AS domain
               FROM slots sl JOIN slot_rules r ON r.id = sl.rule_id JOIN domains d ON d.id = sl.domain_id
               WHERE sl.status = 'free' AND (r.assign_to = 'shared' OR r.user_id = %s)
               ORDER BY (sl.host IS NULL), d.name, sl.port""",
            (owner,),
        )
        rows = await cur.fetchall()
    return {
        "items": [
            {
                "port": r["port"],
                "fqdn": f"{r['host']}.{r['domain']}" if r["host"] else None,
                "rule": {
                    "id": r["rule_id"],
                    "name": r["rule_name"],
                    "domain": r["domain"],
                    "host_template": r["host_template"],
                },
                "dns_ready": r["dns_state"] == "published",
            }
            for r in rows
        ]
    }
