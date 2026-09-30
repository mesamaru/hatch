"""ゴミ箱へ移動・元に戻す・完全に削除。docs/SPEC.md 3章「削除」、docs/IMPLEMENTATION.md 5.3。

- ゴミ箱の間は名前とスロットを予約したまま（スロットは held）。事前作成した DNS は残す
- 元に戻すと、同じアドレスで再び公開する
- 完全に削除すると、パネルからも消し、スロットと名前を解放する（事前作成した DNS は枠に残す）
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..adapters.dns import slot_records
from ..edge.publish import publish, wait_applied
from ..errors import AppError
from ..repo import audit
from ..repo import servers as servers_repo
from ..repo import slots as slots_repo
from .engine import JobContext, Step, job_kind
from .firewall import CloseFirewall


async def _info(ctx: JobContext) -> dict[str, Any]:
    async with db.transaction() as conn:
        s = await servers_repo.get(conn, ctx.server_id)
        if s is None:
            raise AppError("not_found", "サーバーが見つかりません。", 404)
        port = await slots_repo.of_server(conn, ctx.server_id)
        slot = await slots_repo.get_with_rule(conn, port) if port else None
        cur = await conn.execute("SELECT value FROM app_settings WHERE key = 'trash_hours'")
        row = await cur.fetchone()
    ident = str(s["panel_uuid"])[:8] if s["panel_uuid"] else None
    return {"server": s, "slot": slot, "ident": ident, "trash_hours": int(row["value"]) if row else 72}


def _prepublished(slot: dict[str, Any] | None) -> bool:
    return bool(slot and slot["prepublish"] and slot["host"])


async def _republish(ctx: JobContext, *, wait: bool) -> None:
    d = ctx.deps
    async with db.transaction() as conn:
        version = await publish(conn, d.games, d.limits)
    if wait and not await wait_applied(version, within=d.edge_wait, interval=min(2.0, d.poll_interval)):
        raise AppError("edge_timeout", "edge が新しい設定を適用しませんでした。", 504)


# ---------------------------------------------------------------------------
class Stop(Step):
    key, name = "stop", "停止"

    async def run(self, ctx):
        info = await _info(ctx)
        if info["ident"]:
            await ctx.deps.panel.power(info["ident"], "stop")
        return {"prev_status": info["server"]["status"]}

    async def undo(self, ctx, saved):
        info = await _info(ctx)
        if saved and saved.get("prev_status") == "running" and info["ident"]:
            await ctx.deps.panel.power(info["ident"], "start")


class Unexpose(Step):
    key, name = "unexpose", "edge から外す"
    timeout = 180

    async def run(self, ctx):
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, exposed=False)
        await _republish(ctx, wait=True)
        return {}

    async def undo(self, ctx, saved):
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, exposed=True)
        await _republish(ctx, wait=False)


class RemoveDns(Step):
    """サーバーのために作った DNS（{server} の枠など）を消す。事前作成した枠のレコードは残す。"""

    key, name = "remove_dns", "DNS を外す"

    async def run(self, ctx):
        info = await _info(ctx)
        if _prepublished(info["slot"]) or info["slot"] is None:
            return {"kept": True}
        async with db.transaction() as conn:
            cur = await conn.execute("SELECT cf_record_id FROM dns_records WHERE server_id = %s", (ctx.server_id,))
            ids = [r["cf_record_id"] for r in await cur.fetchall()]
        for rid in ids:
            await ctx.deps.dns.delete(info["slot"]["cf_zone_id"], rid)
            async with db.transaction() as conn:
                await conn.execute("DELETE FROM dns_records WHERE cf_record_id = %s", (rid,))
        return {"deleted": len(ids)}

    async def undo(self, ctx, saved):
        if saved and saved.get("kept"):
            return
        await _create_dns(ctx)


async def _create_dns(ctx: JobContext) -> None:
    info = await _info(ctx)
    sl, s = info["slot"], info["server"]
    if sl is None or _prepublished(sl):
        return
    game = ctx.deps.games.get(s["game"])
    for spec in slot_records(
        sl["host"] or s["name"],
        sl["domain"],
        sl["port"],
        mode=sl["record_mode"],
        edge_host=sl["edge_host"],
        ip=sl["rule_ip"],
        srv=bool(sl["create_srv"] and game and game.is_minecraft),
    ):
        rec = await ctx.deps.dns.upsert(sl["cf_zone_id"], spec)
        async with db.transaction() as conn:
            await conn.execute(
                """INSERT INTO dns_records (cf_record_id, domain_id, server_id, slot_port, type, name, content)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (cf_record_id) DO NOTHING""",
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


class HoldSlot(Step):
    key, name = "hold_slot", "アドレスを予約"

    async def run(self, ctx):
        async with db.transaction() as conn:
            await conn.execute("UPDATE slots SET status = 'held' WHERE server_id = %s", (ctx.server_id,))
        return {}

    async def undo(self, ctx, saved):
        async with db.transaction() as conn:
            await conn.execute("UPDATE slots SET status = 'assigned' WHERE server_id = %s", (ctx.server_id,))


class MarkTrashed(Step):
    key, name = "mark_trashed", "ゴミ箱へ移動"

    async def run(self, ctx):
        info = await _info(ctx)
        async with db.transaction() as conn:
            await conn.execute(
                """UPDATE servers SET status = 'trashed', trashed_at = now(),
                          purge_after = now() + make_interval(hours => %s), updated_at = now() WHERE id = %s""",
                (info["trash_hours"], ctx.server_id),
            )
            await audit.add(
                conn,
                action="ゴミ箱へ移動",
                via=ctx.via,
                actor_id=ctx.requested_by,
                target=info["server"]["name"],
                server_id=ctx.server_id,
                job_id=ctx.job_id,
            )
        return {}


@job_kind("trash")
def trash_steps(ctx: JobContext) -> list[Step]:
    return [Stop(), Unexpose(), RemoveDns(), HoldSlot(), MarkTrashed()]


# ---------------------------------------------------------------------------
class Unhold(Step):
    key, name = "unhold", "アドレスを戻す"

    async def run(self, ctx):
        async with db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE slots SET status = 'assigned' "
                "WHERE server_id = %s AND status IN ('held','assigned') RETURNING port",
                (ctx.server_id,),
            )
            if await cur.fetchone() is None:
                raise AppError("slot_taken", "元のアドレスが見つかりません。管理者に連絡してください。", 409)
        return {}

    async def undo(self, ctx, saved):
        async with db.transaction() as conn:
            await conn.execute("UPDATE slots SET status = 'held' WHERE server_id = %s", (ctx.server_id,))


class RestoreDns(Step):
    key, name = "restore_dns", "DNS を戻す"

    async def run(self, ctx):
        await _create_dns(ctx)
        return {}


class Expose(Step):
    key, name = "expose", "edge に公開"
    timeout = 180

    async def run(self, ctx):
        async with db.transaction() as conn:
            await conn.execute("UPDATE servers SET exposed = true, status = 'stopped' WHERE id = %s", (ctx.server_id,))
        await _republish(ctx, wait=True)
        return {}

    async def undo(self, ctx, saved):
        async with db.transaction() as conn:
            await conn.execute("UPDATE servers SET exposed = false, status = 'trashed' WHERE id = %s", (ctx.server_id,))
        await _republish(ctx, wait=False)


class StartAgain(Step):
    key, name = "start", "起動"

    async def run(self, ctx):
        info = await _info(ctx)
        if info["ident"]:
            await ctx.deps.panel.power(info["ident"], "start")
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE servers SET status = 'running', trashed_at = NULL, purge_after = NULL WHERE id = %s",
                (ctx.server_id,),
            )
            await audit.add(
                conn,
                action="ゴミ箱から戻す",
                via=ctx.via,
                actor_id=ctx.requested_by,
                target=info["server"]["name"],
                server_id=ctx.server_id,
                job_id=ctx.job_id,
            )
        return {}


@job_kind("restore")
def restore_steps(ctx: JobContext) -> list[Step]:
    return [Unhold(), RestoreDns(), Expose(), StartAgain()]


# ---------------------------------------------------------------------------
# 完全に削除：途中で失敗したら取り消さず（消したものは戻せない）、状態を purging のまま残して再実行できるようにする
class MarkPurging(Step):
    key, name = "mark_purging", "削除の準備"

    async def run(self, ctx):
        async with db.transaction() as conn:
            await servers_repo.set_fields(conn, ctx.server_id, status="purging", exposed=False)
        return {}


class FinalBackup(Step):
    key, name = "final_backup", "最終バックアップ"

    def skip(self, ctx):
        if ctx.params.get("no_final_backup"):
            return "退会のため作成しない"
        return None if getattr(ctx.deps, "storage", None) else "保管先の連携は準備中（T23）"

    async def run(self, ctx):  # T23 で実装（Object Storage へ複製して30日保管）
        raise NotImplementedError


class DeletePanelServer(Step):
    key, name = "delete_panel", "ゲームパネルから削除"

    async def run(self, ctx):
        info = await _info(ctx)
        panel = ctx.deps.panel
        s = info["server"]
        ps = await panel.find_server(str(s["id"]))
        if ps is not None:
            await panel.delete_server(ps.id)
        if s["panel_allocation_id"] and s["node_id"]:
            async with db.transaction() as conn:
                cur = await conn.execute("SELECT panel_node_id FROM nodes WHERE id = %s", (s["node_id"],))
                node = await cur.fetchone()
            if node:
                await panel.delete_allocation(node["panel_node_id"], s["panel_allocation_id"])
        return {}


class ReleaseAll(Step):
    key, name = "release", "名前とアドレスを解放"

    async def run(self, ctx):
        info = await _info(ctx)
        async with db.transaction() as conn:
            await slots_repo.release(conn, ctx.server_id)
            await conn.execute("DELETE FROM server_shares WHERE server_id = %s", (ctx.server_id,))
            await conn.execute("DELETE FROM custom_domains WHERE server_id = %s", (ctx.server_id,))
            await conn.execute("UPDATE dns_records SET server_id = NULL WHERE server_id = %s", (ctx.server_id,))
            await conn.execute(
                "UPDATE servers SET status = 'purged', panel_server_id = NULL, panel_allocation_id = NULL, "
                "panel_uuid = NULL, updated_at = now() WHERE id = %s",
                (ctx.server_id,),
            )
            await audit.add(
                conn,
                action="完全に削除",
                via=ctx.via,
                actor_id=ctx.requested_by,
                target=info["server"]["name"],
                server_id=ctx.server_id,
                job_id=ctx.job_id,
            )
        return {}


class RemoveDnsForever(RemoveDns):
    async def undo(self, ctx, saved):  # 完全削除では戻さない
        return None


@job_kind("purge")
def purge_steps(ctx: JobContext) -> list[Step]:
    return [MarkPurging(), FinalBackup(), RemoveDnsForever(), DeletePanelServer(), ReleaseAll(), CloseFirewall()]
