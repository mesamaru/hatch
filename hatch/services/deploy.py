"""サーバー作成の依頼（Web の POST /api/servers と Discord の /deploy が共通で使う）。

ここでは「作ってよいか」を確かめて、サーバーの行（pending）とジョブを登録するだけ。
外部サービスへの作成はジョブ（jobs/deploy.py）が行う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from ..domain.names import server_name_problem
from ..domain.permissions import Actor, require
from ..errors import AppError
from ..games import Game
from ..jobs.engine import enqueue
from ..repo import audit
from ..repo import servers as servers_repo
from ..repo import slots as slots_repo


@dataclass(frozen=True)
class DeployRequest:
    name: str
    game: str
    plan: str
    slot_port: int
    owner_id: str
    via: str  # web / discord
    idempotency_key: str | None = None


@dataclass(frozen=True)
class DeployAccepted:
    server_id: str
    job_id: int
    fqdn: str


async def _reserved_names(conn: AsyncConnection) -> set[str]:
    cur = await conn.execute("SELECT name FROM reserved_names")
    return {r["name"] for r in await cur.fetchall()}


async def request_deploy(
    conn: AsyncConnection, actor: Actor, req: DeployRequest, games: dict[str, Game]
) -> DeployAccepted:
    # 同じ依頼（Discord の二重送信など）なら、前回の結果を返す
    if req.idempotency_key:
        cur = await conn.execute(
            "SELECT j.id, j.server_id::text AS server_id, s.fqdn FROM jobs j JOIN servers s ON s.id = j.server_id "
            "WHERE j.idempotency_key = %s",
            (req.idempotency_key,),
        )
        row = await cur.fetchone()
        if row:
            return DeployAccepted(row["server_id"], row["id"], row["fqdn"])

    require(actor, "server.create")
    if req.owner_id != actor.id and not actor.is_admin:
        raise AppError("forbidden", "他の人のサーバーは作成できません。", 403)

    cur = await conn.execute("SELECT id::text, role, status, max_servers FROM users WHERE id = %s", (req.owner_id,))
    owner = await cur.fetchone()
    if owner is None or owner["status"] != "active":
        raise AppError("user_not_found", "所有者のアカウントが見つからないか、利用できない状態です。", 404)
    tos = await _tos_required(conn, req.owner_id)
    if tos:
        raise AppError("tos_required", "利用規約への同意が必要です。設定から利用規約を確認してください。", 403)

    game = games.get(req.game)
    if game is None:
        raise AppError("validation", "選べないゲームです。", 400, {"fields": {"game": "選べない値です"}})
    cur = await conn.execute("SELECT id, period_days FROM plans WHERE id = %s AND is_active", (req.plan,))
    plan = await cur.fetchone()
    if plan is None:
        raise AppError("validation", "選べないプランです。", 400, {"fields": {"plan": "選べない値です"}})

    prob = server_name_problem(req.name, await _reserved_names(conn))
    if prob:
        raise AppError("validation", prob, 400, {"fields": {"name": prob}})
    if await servers_repo.name_in_use(conn, req.name):
        raise AppError(
            "name_taken", "同じ名前のサーバーがあります（ゴミ箱の中も含みます）。別の名前にしてください。", 409
        )

    if owner["role"] != "admin":
        used = await servers_repo.count_owned(conn, req.owner_id)
        if used >= owner["max_servers"]:
            raise AppError("limit_reached", f"作成できる台数の上限（{owner['max_servers']}台）に達しています。", 409)

    slot = await slots_repo.get_with_rule(conn, req.slot_port, lock=True)
    if slot is None:
        raise AppError("validation", "選べないアドレスです。", 400, {"fields": {"slot_port": "選べない値です"}})
    if slot["assign_to"] == "user" and slot["rule_user_id"] != req.owner_id:
        raise AppError("slot_not_allowed", "このアドレスは使えません。", 403)
    if slot["status"] != "free":
        raise AppError("slot_taken", "このアドレスは使用中です。別のアドレスを選んでください。", 409)
    host = slot["host"] or req.name
    fqdn = f"{host}.{slot['domain']}"

    server_id = await servers_repo.insert_pending(
        conn,
        name=req.name,
        owner_id=req.owner_id,
        plan_id=plan["id"],
        game=game.id,
        fqdn=fqdn,
        period_days=plan["period_days"],
    )
    # アドレスは依頼の時点で確保する（同時に同じアドレスを選んだ2人のうち、後の人にすぐ知らせるため）
    if not await slots_repo.assign(conn, req.slot_port, server_id):
        raise AppError("slot_taken", "このアドレスは使用中です。別のアドレスを選んでください。", 409)
    job_id = await enqueue(
        conn,
        "deploy",
        via=req.via,
        params={"slot_port": req.slot_port},
        server_id=server_id,
        requested_by=actor.id,
        idempotency_key=req.idempotency_key,
    )
    await audit.add(
        conn,
        action="サーバーの作成を依頼",
        via=req.via,
        actor_id=actor.id,
        target=req.name,
        server_id=server_id,
        job_id=job_id,
        detail={"port": req.slot_port, "game": game.id, "plan": plan["id"]},
    )
    return DeployAccepted(server_id, job_id, fqdn)


async def _tos_required(conn: AsyncConnection, user_id: str) -> bool:
    cur = await conn.execute("SELECT value FROM app_settings WHERE key = 'tos_current_version'")
    row = await cur.fetchone()
    version: Any = row["value"] if row else None
    if not version:
        return False
    cur = await conn.execute(
        "SELECT 1 FROM tos_acceptances WHERE user_id = %s AND tos_version = %s", (user_id, str(version))
    )
    return await cur.fetchone() is None
