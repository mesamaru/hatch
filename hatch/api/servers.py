"""サーバーの作成とジョブの参照。一覧・詳細・操作は T13 で追加する。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from .. import db
from ..adapters.panel import PanelAdapter
from ..auth.session import Principal, require_admin, require_user
from ..domain.permissions import ServerRef, effective_level, require
from ..errors import AppError, TransientError, UpstreamError
from ..games import Game
from ..jobs import deploy as _deploy  # noqa: F401 - ジョブの登録
from ..jobs import lifecycle as _lifecycle  # noqa: F401
from ..jobs.engine import enqueue
from ..repo import audit
from ..repo import servers as servers_repo
from ..repo import supporters as supporters_repo
from ..services.deploy import DeployRequest, request_deploy
from .deps import get_games, get_panel

router = APIRouter(tags=["servers"])


class CreateServer(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    game: str = Field(min_length=1, max_length=40)
    plan: str = Field(min_length=1, max_length=40)
    slot_port: int = Field(ge=1024, le=65535)
    owner_id: str | None = None


@router.post("/api/servers", status_code=202)
async def create_server(
    body: CreateServer,
    p: Principal = Depends(require_user),
    games: dict[str, Game] = Depends(get_games),
    idempotency_key: str | None = Header(default=None, max_length=100),
) -> dict:
    req = DeployRequest(
        name=body.name,
        game=body.game,
        plan=body.plan,
        slot_port=body.slot_port,
        owner_id=body.owner_id or p.user_id,
        via="web",
        idempotency_key=f"web:{p.user_id}:{idempotency_key}" if idempotency_key else None,
    )
    async with db.transaction() as conn:
        acc = await request_deploy(conn, p.actor, req, games)
    return {
        "server": {"id": acc.server_id, "name": body.name, "address": acc.fqdn, "status": "pending"},
        "job": {"id": acc.job_id, "kind": "deploy", "status": "queued"},
    }


@router.get("/api/catalog")
async def catalog(p: Principal = Depends(require_user), games: dict[str, Game] = Depends(get_games)) -> dict:
    """作成画面の選択肢（ゲームとプラン）。"""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT id, name, memory_mb, cpu_percent, disk_mb, backup_limit, period_days
               FROM plans WHERE is_active ORDER BY memory_mb, id"""
        )
        plans = [dict(r) for r in await cur.fetchall()]
    return {
        "games": [{"id": g.id, "label": g.label, "kind": g.kind} for g in games.values()],
        "plans": plans,
    }


@router.get("/api/admin/users")
async def list_users(p: Principal = Depends(require_admin)) -> dict:
    """ユーザーの一覧（作成時の所有者や、専用枠の相手を選ぶため）。"""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT id::text AS id, username, role, status, max_servers FROM users
               WHERE status IN ('active','invited','suspended','deleting') ORDER BY username"""
        )
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: int, p: Principal = Depends(require_user)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT j.id, j.kind, j.status, j.current_step, j.error, j.server_id::text AS server_id,
                      j.requested_by::text AS requested_by, s.owner_id::text AS owner_id, j.created_at, j.finished_at
               FROM jobs j LEFT JOIN servers s ON s.id = j.server_id WHERE j.id = %s""",
            (job_id,),
        )
        job = await cur.fetchone()
        if job is None or (p.role != "admin" and p.user_id not in (job["requested_by"], job["owner_id"])):
            raise AppError("not_found", "見つかりません。", 404)
        cur = await conn.execute(
            "SELECT seq, name, status, note, error FROM job_steps WHERE job_id = %s ORDER BY seq", (job_id,)
        )
        steps = [dict(r) for r in await cur.fetchall()]
    out = {k: job[k] for k in ("id", "kind", "status", "current_step", "error", "server_id")}
    out["created_at"] = job["created_at"].isoformat().replace("+00:00", "Z")
    out["finished_at"] = job["finished_at"].isoformat().replace("+00:00", "Z") if job["finished_at"] else None
    out["steps"] = steps
    return out


# ---------------------------------------------------------------------------
# 一覧・詳細・操作（T13）
# ---------------------------------------------------------------------------


def _iso(v):
    return v.isoformat().replace("+00:00", "Z") if v else None


def _ref(row) -> ServerRef:
    return ServerRef(
        owner_id=row["owner_id"],
        status=row["status"],
        shares=row["shares"] or {},
        supporters=frozenset(row["supporters"] or ()),
    )


def _my_permission(p: Principal, row) -> str:
    if p.role == "admin":
        return "admin"
    if row["owner_id"] == p.user_id:
        return "owner"
    level = effective_level(p.actor, _ref(row))
    if level is None:
        return "none"
    # 共有の権限のほうが高ければ共有の権限を、そうでなければ「サポート」を返す
    return level if level == row["share_permission"] else "support"


def server_out(p: Principal, row) -> dict:
    slot_fqdn = f"{row['slot_host'] or row['name']}.{row['slot_domain']}" if row["slot_domain"] else None
    return {
        "id": row["id"],
        "name": row["name"],
        "owner": {"id": row["owner_id"], "username": row["owner_name"]},
        "game": row["game"],
        "plan": row["plan"],
        "status": row["status"],
        "address": row["custom_domain"] or row["fqdn"] or slot_fqdn,
        "slot": {"port": row["port"], "host": row["slot_host"], "domain": row["slot_domain"], "rule_id": row["rule_id"]}
        if row["port"]
        else None,
        "direct": f"{row['edge_host']}.{row['slot_domain']}:{row['port']}" if row["port"] else None,
        "node": row["node"],
        "is_dev_copy": row["is_dev_copy"],
        "proxy": {"id": row["proxy_id"], "name": row["proxy_name"]} if row["proxy_id"] else None,
        "auto_restart": row["auto_restart"],
        "public_status": row["public_status"],
        "expires_at": _iso(row["expires_at"]),
        "maintenance_until": _iso(row["maintenance_until"]),
        "suspend_reason": row["suspend_reason"],
        "trashed_at": _iso(row["trashed_at"]),
        "purge_after": _iso(row["purge_after"]),
        "my_permission": _my_permission(p, row),
        "job": row["job"],
    }


async def _load(conn, p: Principal, id_or_name: str, action: str):
    row = await servers_repo.find_view(conn, p.user_id, id_or_name)
    if row is None:
        raise AppError("not_found", "見つかりません。", 404)
    require(p.actor, action, _ref(row))
    return row


@router.get("/api/servers")
async def list_servers(
    owner: str | None = Query(default=None),
    status: str | None = Query(default=None),
    p: Principal = Depends(require_user),
) -> dict:
    if owner and p.role != "admin":
        raise AppError("forbidden", "他の人で絞り込めるのは管理者だけです。", 403)
    statuses = tuple(status.split(",")) if status else None
    async with db.transaction() as conn:
        rows = await servers_repo.visible(
            conn,
            p.user_id,
            p.role == "admin",
            is_supporter=p.role == "supporter",
            owner=owner,
            statuses=statuses,
        )
    return {"items": [server_out(p, r) for r in rows]}


@router.get("/api/servers/{key}")
async def get_server(key: str, p: Principal = Depends(require_user)) -> dict:
    async with db.transaction() as conn:
        return server_out(p, await _load(conn, p, key, "server.view"))


class ServerPatch(BaseModel):
    auto_restart: bool | None = None
    public_status: bool | None = None


@router.patch("/api/servers/{key}")
async def patch_server(key: str, body: ServerPatch, p: Principal = Depends(require_user)) -> dict:
    async with db.transaction() as conn:
        row = await _load(conn, p, key, "server.settings")
        changes = body.model_dump(exclude_none=True)
        if changes:
            cols = ", ".join(f"{k} = %s" for k in changes)
            await conn.execute(
                f"UPDATE servers SET {cols}, updated_at = now() WHERE id = %s", (*changes.values(), row["id"])
            )
            await audit.add(
                conn,
                action="設定を変更",
                via="web",
                actor_id=p.user_id,
                target=row["name"],
                server_id=row["id"],
                detail=changes,
            )
        return server_out(p, await servers_repo.find_view(conn, p.user_id, row["id"]))


def _identifier(row) -> str:
    if not row["panel_uuid"]:
        raise AppError("server_busy", "まだ作成が終わっていません。", 409)
    return row["panel_uuid"][:8]


def _upstream(e: Exception) -> AppError:
    if isinstance(e, TransientError):
        return AppError("upstream_timeout", "ゲームパネルが応答しません。少し待ってからもう一度試してください。", 504)
    return AppError("upstream_error", f"ゲームパネルがエラーを返しました：{getattr(e, 'message', e)}", 502)


class Power(BaseModel):
    signal: Literal["start", "stop", "restart"]


@router.post("/api/servers/{key}/power", status_code=204)
async def power(
    key: str, body: Power, p: Principal = Depends(require_user), panel: PanelAdapter = Depends(get_panel)
) -> Response:
    async with db.transaction() as conn:
        row = await _load(conn, p, key, "server.power")
    if row["status"] not in ("running", "stopped"):
        raise AppError("server_busy", "今はこの操作ができません（作成中・メンテナンス中など）。", 409)
    if row["job"]:
        raise AppError("server_busy", "実行中の処理が終わるまでお待ちください。", 409)
    try:
        await panel.power(_identifier(row), body.signal)
    except (TransientError, UpstreamError) as e:
        raise _upstream(e) from None
    new_status = "stopped" if body.signal == "stop" else "running"
    label = {"start": "起動", "stop": "停止", "restart": "再起動"}[body.signal]
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE servers SET status = %s, updated_at = now() WHERE id = %s AND status IN ('running','stopped')",
            (new_status, row["id"]),
        )
        await audit.add(conn, action=label, via="web", actor_id=p.user_id, target=row["name"], server_id=row["id"])
    return Response(status_code=204)


class Maintenance(BaseModel):
    enabled: bool
    hours: float = Field(default=2, gt=0, le=72)


@router.post("/api/servers/{key}/maintenance")
async def maintenance(key: str, body: Maintenance, p: Principal = Depends(require_user)) -> dict:
    async with db.transaction() as conn:
        row = await _load(conn, p, key, "server.settings")
        if body.enabled:
            if row["status"] not in ("running", "stopped", "maintenance"):
                raise AppError("server_busy", "今はメンテナンスを始められません。", 409)
            until = datetime.now(UTC) + timedelta(hours=body.hours)
            await conn.execute(
                "UPDATE servers SET status = 'maintenance', maintenance_until = %s WHERE id = %s", (until, row["id"])
            )
            action = "メンテナンスを開始"
        else:
            if row["status"] != "maintenance":
                raise AppError("validation", "メンテナンス中ではありません。", 409)
            await conn.execute(
                "UPDATE servers SET status = 'running', maintenance_until = NULL WHERE id = %s", (row["id"],)
            )
            action = "メンテナンスを終了"
        await audit.add(conn, action=action, via="web", actor_id=p.user_id, target=row["name"], server_id=row["id"])
        return server_out(p, await servers_repo.find_view(conn, p.user_id, row["id"]))


@router.get("/api/servers/{key}/resources")
async def resources(key: str, p: Principal = Depends(require_user), panel: PanelAdapter = Depends(get_panel)) -> dict:
    async with db.transaction() as conn:
        row = await _load(conn, p, key, "server.view")
        cur = await conn.execute("SELECT memory_mb, disk_mb FROM plans WHERE id = %s", (row["plan"],))
        plan = await cur.fetchone()
    try:
        r = await panel.resources(_identifier(row))
    except (TransientError, UpstreamError) as e:
        raise _upstream(e) from None
    return {
        "state": r.state,
        "cpu_percent": r.cpu_percent,
        "memory_bytes": r.memory_bytes,
        "memory_limit": plan["memory_mb"] * 1024 * 1024,
        "disk_bytes": r.disk_bytes,
        "disk_limit": plan["disk_mb"] * 1024 * 1024,
        "players": None,
    }


# ---------------------------------------------------------------------------
# ゴミ箱・元に戻す・完全に削除（T14）
# ---------------------------------------------------------------------------
async def _enqueue_lifecycle(
    p: Principal, key: str, kind: str, allowed: tuple[str, ...], busy_msg: str, params: dict | None = None, check=None
) -> dict:
    async with db.transaction() as conn:
        row = await _load(conn, p, key, "server.trash")
        if row["job"]:
            raise AppError("server_busy", "実行中の処理が終わるまでお待ちください。", 409)
        if row["status"] not in allowed:
            raise AppError("server_busy", busy_msg, 409)
        if check:
            check(row)
        job = await enqueue(conn, kind, via="web", params=params or {}, server_id=row["id"], requested_by=p.user_id)
    return {"job": {"id": job, "kind": kind, "status": "queued"}}


@router.delete("/api/servers/{key}", status_code=202)
async def trash_server(key: str, p: Principal = Depends(require_user)) -> dict:
    return await _enqueue_lifecycle(
        p, key, "trash", ("running", "stopped", "maintenance", "suspended"), "今はゴミ箱へ移動できません。"
    )


@router.post("/api/servers/{key}/restore", status_code=202)
async def restore_server(key: str, p: Principal = Depends(require_user)) -> dict:
    async with db.transaction() as conn:
        row = await servers_repo.find_view(conn, p.user_id, key)
    if row is not None and row["status"] == "trashed" and p.role != "admin":
        # 利用停止中にゴミ箱へ入れたサーバーは、利用者は戻せない
        cur_reason = row["suspend_reason"]
        if cur_reason:
            raise AppError("server_suspended", "管理者により利用停止中のため、元に戻せません。", 409)
    return await _enqueue_lifecycle(p, key, "restore", ("trashed",), "ゴミ箱にあるサーバーだけを元に戻せます。")


class Purge(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=40)


@router.post("/api/servers/{key}/purge", status_code=202)
async def purge_server(key: str, body: Purge, p: Principal = Depends(require_user)) -> dict:
    def check(row):
        if body.confirm_name != row["name"]:
            raise AppError("confirm_mismatch", "確認のための名前が一致しません。", 400)

    return await _enqueue_lifecycle(
        p, key, "purge", ("trashed", "purging"), "ゴミ箱にあるサーバーだけを完全に削除できます。", check=check
    )


# ---------------------------------------------------------------------------
# サポーターの割り当て（管理者のみ）
# ---------------------------------------------------------------------------
@router.get("/api/supporters")
async def list_supporters(p: Principal = Depends(require_admin)) -> dict:
    """割り当てられるサポーターの一覧。"""
    async with db.transaction() as conn:
        rows = await supporters_repo.list_supporter_users(conn)
    return {"items": [{"id": r["id"], "username": r["username"]} for r in rows]}


async def _admin_server(conn, p: Principal, key: str):
    row = await servers_repo.find_view(conn, p.user_id, key)
    if row is None:
        raise AppError("not_found", "見つかりません。", 404)
    return row


@router.get("/api/servers/{key}/supporters")
async def server_supporters(key: str, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        row = await _admin_server(conn, p, key)
        rows = await supporters_repo.of_server(conn, row["id"])
    return {
        "items": [
            {
                "id": r["id"],
                "username": r["username"],
                "active": r["role"] == "supporter",
                "assigned_at": _iso(r["created_at"]),
            }
            for r in rows
        ]
    }


@router.put("/api/servers/{key}/supporters/{user_id}", status_code=204)
async def assign_supporter(key: str, user_id: str, p: Principal = Depends(require_admin)) -> Response:
    async with db.transaction() as conn:
        row = await _admin_server(conn, p, key)
        cur = await conn.execute("SELECT role, status FROM users WHERE id::text = %s", (user_id,))
        user = await cur.fetchone()
        if user is None or user["status"] not in ("active", "invited"):
            raise AppError("not_found", "ユーザーが見つかりません。", 404)
        if user["role"] != "supporter":
            raise AppError(
                "not_supporter",
                "サポーターの権限を持つ人だけを割り当てられます。Discord でサポーターのロールを付けてください。",
                400,
            )
        if await supporters_repo.assign(conn, row["id"], user_id, p.user_id):
            await audit.add(
                conn,
                action="サポーターを割り当て",
                via="web",
                actor_id=p.user_id,
                target=row["name"],
                server_id=row["id"],
                detail={"user_id": user_id},
            )
    return Response(status_code=204)


@router.delete("/api/servers/{key}/supporters/{user_id}", status_code=204)
async def unassign_supporter(key: str, user_id: str, p: Principal = Depends(require_admin)) -> Response:
    async with db.transaction() as conn:
        row = await _admin_server(conn, p, key)
        if await supporters_repo.unassign(conn, row["id"], user_id):
            await audit.add(
                conn,
                action="サポーターの割り当てを解除",
                via="web",
                actor_id=p.user_id,
                target=row["name"],
                server_id=row["id"],
                detail={"user_id": user_id},
            )
    return Response(status_code=204)
