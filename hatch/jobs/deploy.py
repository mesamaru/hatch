"""デプロイ（サーバーの作成と公開）。docs/SPEC.md 3章・docs/IMPLEMENTATION.md 5.3。

services/deploy.py が servers に pending の行を作り、このジョブを登録する。
params: {"slot_port": 25565}
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .. import db
from ..adapters.dns import DnsRecordSpec, slot_records
from ..adapters.panel import Cron, NewPanelServer, NewPanelUser
from ..domain.placement import NodeLoad, choose_node
from ..edge.publish import publish, wait_applied
from ..errors import AppError
from ..repo import audit
from ..repo import servers as servers_repo
from ..repo import slots as slots_repo
from .deps import Deps
from .engine import JobContext, Step, job_kind

BACKUP_SCHEDULE = "Hatch 自動バックアップ"


def deps(ctx: JobContext) -> Deps:
    return ctx.deps


async def load(ctx: JobContext) -> dict[str, Any]:
    """サーバー・プラン・所有者・スロットの情報をまとめて読む。"""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT s.*, s.id::text AS id, s.owner_id::text AS owner_id,
                      p.memory_mb, p.disk_mb, p.cpu_percent, p.backup_limit,
                      u.username, u.email, u.panel_user_id, u.role
               FROM servers s JOIN plans p ON p.id = s.plan_id JOIN users u ON u.id = s.owner_id
               WHERE s.id = %s""",
            (ctx.server_id,),
        )
        srv = await cur.fetchone()
        if srv is None:
            raise AppError("not_found", "サーバーの記録が見つかりません。", 404)
        slot = await slots_repo.get_with_rule(conn, ctx.params["slot_port"])
    return {"server": srv, "slot": slot}


# ---------------------------------------------------------------------------
class Validate(Step):
    key, name = "validate", "依頼の検証"

    async def run(self, ctx):
        info = await load(ctx)
        s = info["server"]
        if s["status"] not in ("pending", "provisioning"):
            raise AppError("server_busy", f"このサーバーは作成できる状態ではありません（{s['status']}）。", 409)
        if info["slot"] is None:
            raise AppError("validation", "選んだアドレスが見つかりません。", 400)
        if s["game"] not in deps(ctx).games:
            raise AppError("validation", "このゲームは設定から削除されています。", 400)
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, status="provisioning")
        return {}

    async def undo(self, ctx, saved):
        # 取り消しの最後に実行される。失敗したサーバーの名前とアドレスを再び使えるようにする
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, status="failed")
            s = await servers_repo.get(conn, ctx.server_id)
            await audit.add(
                conn,
                action="サーバーを作成",
                via=ctx.via,
                actor_id=ctx.requested_by,
                target=s["name"] if s else None,
                server_id=ctx.server_id,
                job_id=ctx.job_id,
                result="rolled_back",
            )


class ReserveSlot(Step):
    """依頼の時点で確保済み。ここでは確保が残っているかを確かめる（取り消しで解放する役目も持つ）。"""

    key = "reserve_slot"

    def __init__(self, port: int):
        self.port = port
        self.name = f"アドレスを確保（{port}）"

    async def run(self, ctx):
        async with db.transaction() as conn:
            if not await slots_repo.assign(conn, self.port, ctx.server_id):
                raise AppError(
                    "slot_taken", "このアドレスは別のサーバーに使われました。別のアドレスを選んでください。", 409
                )
        return {"port": self.port}

    async def undo(self, ctx, saved):
        async with db.transaction() as conn:
            await slots_repo.release(conn, ctx.server_id)


class EnsurePanelUser(Step):
    key, name = "panel_user", "ゲームパネルのユーザーを確認"

    async def run(self, ctx):
        s = (await load(ctx))["server"]
        panel = deps(ctx).panel
        if s["panel_user_id"]:
            return {"panel_user_id": s["panel_user_id"], "created": False}
        u = await panel.find_user(external_id=s["owner_id"]) or await panel.find_user(email=s["email"])
        created = False
        if u is None:
            u = await panel.create_user(
                NewPanelUser(external_id=s["owner_id"], username=s["username"], email=s["email"])
            )
            created = True
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE users SET panel_user_id = %s, panel_synced_at = now() WHERE id = %s", (u.id, s["owner_id"])
            )
        return {"panel_user_id": u.id, "created": created}

    async def undo(self, ctx, saved):
        # 新しく作ったユーザーだけを消す（既存のユーザーには触らない）
        if not saved or not saved.get("created"):
            return
        s = (await load(ctx))["server"]
        await deps(ctx).panel.delete_user(saved["panel_user_id"])
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE users SET panel_user_id = NULL WHERE id = %s AND panel_user_id = %s",
                (s["owner_id"], saved["panel_user_id"]),
            )


class CreatePanelServer(Step):
    key, name = "create_server", "ノードを選んでサーバーを作成"

    async def run(self, ctx):
        d = deps(ctx)
        info = await load(ctx)
        s = info["server"]
        game = d.games[s["game"]]
        existing = await d.panel.find_server(s["id"])  # 前回の途中で作れていたら使う
        async with db.transaction() as conn:
            loads = await servers_repo.node_loads(conn)
        if s["node_id"]:
            node = next((n for n in loads if n["id"] == s["node_id"]), None)
        else:
            picked = choose_node(
                [
                    NodeLoad(
                        n["id"],
                        n["memory_mb"],
                        n["disk_mb"],
                        n["overcommit_percent"],
                        n["accepting"],
                        n["used_memory_mb"],
                        n["used_disk_mb"],
                    )
                    for n in loads
                ],
                s["memory_mb"],
                s["disk_mb"],
            )
            if picked is None:
                raise AppError("no_capacity", "空きのあるノードがありません。管理者に連絡してください。", 409)
            node = next(n for n in loads if n["id"] == picked.id)
            async with db.transaction() as conn:
                await servers_repo.set_fields(conn, ctx.server_id, node_id=node["id"])
        if node is None:
            raise AppError("no_capacity", "割り当てたノードが見つかりません。", 409)
        port = ctx.params["slot_port"]
        if existing is None:
            alloc_id = await d.panel.create_allocation(node["panel_node_id"], node["ip"], port)
            async with db.transaction() as conn:
                await servers_repo.set_fields(conn, ctx.server_id, panel_allocation_id=alloc_id)
            egg = await d.panel.get_egg(game.nest, game.egg)
            env = {**egg.variables, **game.environment}
            existing = await d.panel.create_server(
                NewPanelServer(
                    external_id=s["id"],
                    name=s["name"],
                    user_id=ctx.result("panel_user")["panel_user_id"],
                    egg=game.egg,
                    docker_image=egg.docker_image,
                    startup=egg.startup,
                    environment=env,
                    memory_mb=s["memory_mb"],
                    disk_mb=s["disk_mb"],
                    cpu_percent=s["cpu_percent"],
                    backups=s["backup_limit"],
                    allocation_id=alloc_id,
                )
            )
        async with db.transaction() as conn:
            await servers_repo.set_fields(
                conn,
                ctx.server_id,
                panel_server_id=existing.id,
                panel_uuid=existing.uuid,
                panel_allocation_id=existing.allocation_id,
            )
        return {
            "node_id": node["id"],
            "panel_node_id": node["panel_node_id"],
            "panel_server_id": existing.id,
            "identifier": existing.identifier,
            "allocation_id": existing.allocation_id,
        }

    async def undo(self, ctx, saved):
        d = deps(ctx)
        s = (await load(ctx))["server"]
        ps = await d.panel.find_server(s["id"])
        if ps is not None:
            await d.panel.delete_server(ps.id)
        alloc = (saved or {}).get("allocation_id") or s["panel_allocation_id"]
        panel_node = (saved or {}).get("panel_node_id")
        if panel_node is None and s["node_id"]:
            async with db.transaction() as conn:
                cur = await conn.execute("SELECT panel_node_id FROM nodes WHERE id = %s", (s["node_id"],))
                row = await cur.fetchone()
                panel_node = row["panel_node_id"] if row else None
        if alloc and panel_node:
            await d.panel.delete_allocation(panel_node, alloc)


class WaitInstall(Step):
    key, name = "wait_install", "サーバーの準備を待つ"
    timeout = 900

    async def run(self, ctx):
        d = deps(ctx)
        ident = ctx.result("create_server")["identifier"]
        deadline = time.monotonic() + d.install_timeout
        while await d.panel.is_installing(ident):
            if time.monotonic() > deadline:
                raise AppError("install_timeout", "サーバーの準備が時間内に終わりませんでした。", 504)
            await asyncio.sleep(d.poll_interval)
        return {}


class PublishEdge(Step):
    key, name = "edge", "edge に転送設定を配布"
    timeout = 180

    async def run(self, ctx):
        d = deps(ctx)
        async with db.transaction() as conn:
            version = await publish(conn, d.games, d.limits)
        if not await wait_applied(version, within=d.edge_wait, interval=min(2.0, d.poll_interval)):
            raise AppError(
                "edge_timeout", "edge が新しい設定を適用しませんでした。edge の状態を確認してください。", 504
            )
        return {"version": version}

    async def undo(self, ctx, saved):
        # 状態を failed にしてから設定を作り直すと、このサーバーのポートが外れる
        d = deps(ctx)
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, status="failed")
            await publish(conn, d.games, d.limits)


class DnsStep(Step):
    """スロットの CNAME（または A）と SRV。事前作成済みの枠では確認だけにする。"""

    def __init__(self, kind: str):
        self.kind = kind  # "main"（CNAME/A）/ "srv"
        self.key = f"dns_{kind}"
        self.name = "DNS：CNAME" if kind == "main" else "DNS：SRV"

    def _specs(self, info: dict[str, Any], game_is_mc: bool) -> list[DnsRecordSpec]:
        sl, s = info["slot"], info["server"]
        recs = slot_records(
            sl["host"] or s["name"],
            sl["domain"],
            sl["port"],
            mode=sl["record_mode"],
            edge_host=sl["edge_host"],
            ip=sl["rule_ip"],
            srv=bool(sl["create_srv"] and game_is_mc),
        )
        return [r for r in recs if (r.type == "SRV") == (self.kind == "srv")]

    def skip(self, ctx):
        return None  # 判定には DB が必要なので run の中で行う（飛ばした扱いは saved で表す）

    async def run(self, ctx):
        d = deps(ctx)
        info = await load(ctx)
        sl = info["slot"]
        specs = self._specs(info, d.games[info["server"]["game"]].is_minecraft)
        if not specs:
            return {"skipped": "対象外"}
        if sl["prepublish"] and sl["host"] and sl["dns_state"] == "published":
            # 事前作成済み：念のため内容を確認（違っていれば直す）するが、取り消しでは消さない
            for spec in specs:
                await d.dns.upsert(sl["cf_zone_id"], spec)
            return {"prepublished": True, "ids": []}
        ids = []
        for spec in specs:
            rec = await d.dns.upsert(sl["cf_zone_id"], spec)
            ids.append(rec.id)
            async with db.transaction() as conn:
                await conn.execute(
                    """INSERT INTO dns_records (cf_record_id, domain_id, server_id, slot_port, type, name, content)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (cf_record_id) DO NOTHING""",
                    (
                        rec.id,
                        sl["domain_id"],
                        ctx.server_id,
                        sl["port"],
                        spec.type,
                        spec.name,
                        spec.content or f"{spec.srv.port} {spec.srv.target}",
                    ),
                )
        return {"ids": ids}

    async def undo(self, ctx, saved):
        if saved and (saved.get("prepublished") or saved.get("skipped")):
            return
        d = deps(ctx)
        info = await load(ctx)
        sl = info["slot"]
        ids = list((saved or {}).get("ids") or [])
        if saved is None:  # 途中で失敗：名前で探して自分のものを消す
            for spec in self._specs(info, d.games[info["server"]["game"]].is_minecraft):
                ids += [
                    r.id
                    for r in await d.dns.find(sl["cf_zone_id"], spec.name)
                    if d.dns.is_ours(r) and r.type == spec.type
                ]
        for rid in ids:
            await d.dns.delete(sl["cf_zone_id"], rid)
        async with db.transaction() as conn:
            await conn.execute(
                "DELETE FROM dns_records WHERE server_id = %s AND slot_port = %s", (ctx.server_id, sl["port"])
            )


class StartServer(Step):
    key, name = "start", "起動して確認"
    timeout = 300

    async def run(self, ctx):
        d = deps(ctx)
        ident = ctx.result("create_server")["identifier"]
        await d.panel.power(ident, "start")
        deadline = time.monotonic() + d.start_timeout
        while (await d.panel.resources(ident)).state != "running":
            if time.monotonic() > deadline:
                raise AppError(
                    "start_timeout",
                    "サーバーが時間内に起動しませんでした。ゲームパネルのコンソールを確認してください。",
                    504,
                )
            await asyncio.sleep(d.poll_interval)
        return {}

    async def undo(self, ctx, saved):
        ident = ctx.result("create_server").get("identifier")
        if ident:
            await deps(ctx).panel.power(ident, "kill")


class Schedule(Step):
    key, name = "schedule", "バックアップと期限を設定"

    async def run(self, ctx):
        ident = ctx.result("create_server")["identifier"]
        sid = await deps(ctx).panel.ensure_schedule(ident, BACKUP_SCHEDULE, Cron(minute="0", hour="4"))
        return {"schedule_id": sid}


class AddMonitor(Step):
    key, name = "monitor", "監視を追加"

    def skip(self, ctx):
        return "監視の連携は準備中" if deps(ctx).monitor is None else None

    async def run(self, ctx):  # T17 で実装
        raise NotImplementedError


class Finish(Step):
    key, name = "finish", "公開"

    async def run(self, ctx):
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, status="running")
            s = await servers_repo.get(conn, ctx.server_id)
            await audit.add(
                conn,
                action="サーバーを作成",
                via=ctx.via,
                actor_id=ctx.requested_by,
                target=s["name"],
                server_id=ctx.server_id,
                job_id=ctx.job_id,
                detail={"fqdn": s["fqdn"]},
            )
        return {}


@job_kind("deploy")
def deploy_steps(ctx: JobContext) -> list[Step]:
    return [
        Validate(),
        ReserveSlot(ctx.params["slot_port"]),
        EnsurePanelUser(),
        CreatePanelServer(),
        WaitInstall(),
        PublishEdge(),
        DnsStep("main"),
        DnsStep("srv"),
        StartServer(),
        Schedule(),
        AddMonitor(),
        Finish(),
    ]
