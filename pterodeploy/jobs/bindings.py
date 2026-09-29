"""IP の紐付け（A レコード）の作成・更新・削除。params: {"binding_id": 1}"""

from __future__ import annotations

from .. import db
from ..adapters.dns import DnsRecordSpec
from ..domain.names import fqdn
from ..errors import AppError
from .engine import JobContext, Step, job_kind


async def _load(binding_id: int):
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT b.id, b.host, b.follow_active_edge, b.cf_record_id, host(ip.address) AS ip,
                      d.id AS domain_id, d.name AS domain, d.cf_zone_id
               FROM ip_bindings b JOIN domains d ON d.id = b.domain_id LEFT JOIN ip_addresses ip ON ip.id = b.ip_id
               WHERE b.id = %s""",
            (binding_id,),
        )
        row = await cur.fetchone()
        if row and row["follow_active_edge"]:
            cur = await conn.execute("SELECT host(public_ip) AS ip FROM edges WHERE is_active")
            edge = await cur.fetchone()
            row = {**row, "ip": edge["ip"] if edge else None}
    return row


class SyncBinding(Step):
    key, name = "sync_binding", "A レコードを作成・更新"

    async def run(self, ctx: JobContext):
        b = await _load(ctx.params["binding_id"])
        if b is None:
            return {"skipped": "紐付けが削除されています"}
        if not b["ip"]:
            raise AppError(
                "no_active_edge", "使用中の edge が登録されていません。「ノードと edge」で登録してください。", 409
            )
        rec = await ctx.deps.dns.upsert(
            b["cf_zone_id"], DnsRecordSpec("A", fqdn(b["host"], b["domain"]), f"binding={b['id']}", content=b["ip"])
        )
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE ip_bindings SET cf_record_id = %s, synced_at = now() WHERE id = %s", (rec.id, b["id"])
            )
        return {"record_id": rec.id, "ip": b["ip"]}


class DeleteBinding(Step):
    key, name = "delete_binding", "A レコードを削除"

    async def run(self, ctx: JobContext):
        b = await _load(ctx.params["binding_id"])
        if b is None:
            return {}
        dns = ctx.deps.dns
        ids = [b["cf_record_id"]] if b["cf_record_id"] else []
        if not ids:  # 作成の途中だった場合は名前で探す
            ids = [
                r.id
                for r in await dns.find(b["cf_zone_id"], fqdn(b["host"], b["domain"]))
                if dns.is_ours(r) and r.type == "A"
            ]
        for rid in ids:
            await dns.delete(b["cf_zone_id"], rid)
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM ip_bindings WHERE id = %s", (b["id"],))
        return {"deleted": len(ids)}


@job_kind("sync_binding")
def _sync(ctx: JobContext) -> list[Step]:
    return [SyncBinding()]


@job_kind("delete_binding")
def _delete(ctx: JobContext) -> list[Step]:
    return [DeleteBinding()]
