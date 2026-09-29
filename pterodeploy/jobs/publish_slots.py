"""アドレス枠の DNS を事前作成する・消す。params: {"rule_id": 1}"""

from __future__ import annotations

from .. import db
from ..adapters.dns import slot_records
from ..repo import slot_rules as repo
from .engine import JobContext, Step, job_kind


async def _targets(rule_id: int, *, for_delete: bool):
    async with db.transaction() as conn:
        row = await repo.get_rule(conn, rule_id)
        if row is None:
            return None, []
        cur = await conn.execute(
            "SELECT port, host, status, dns_state FROM slots WHERE rule_id = %s AND host IS NOT NULL ORDER BY port",
            (rule_id,),
        )
        slots = list(await cur.fetchall())
    if for_delete:  # 使用中・ゴミ箱のスロットのレコードは残す（サーバーが使っているため）
        slots = [s for s in slots if s["status"] in ("free", "disabled")]
    return row, slots


class PublishAll(Step):
    key, name = "publish", "DNS を作成"
    timeout = 900

    async def run(self, ctx: JobContext):
        row, slots = await _targets(ctx.params["rule_id"], for_delete=False)
        if row is None:
            return {"ids": []}
        created: list[str] = []
        for s in slots:
            recs = slot_records(
                s["host"],
                row["domain"],
                s["port"],
                mode=row["record_mode"],
                edge_host=row["edge_host"],
                ip=row["ip"],
                srv=row["create_srv"],
            )
            for spec in recs:
                rec = await ctx.deps.dns.upsert(row["cf_zone_id"], spec)
                created.append(rec.id)
                async with db.transaction() as conn:
                    await conn.execute(
                        """INSERT INTO dns_records (cf_record_id, domain_id, slot_port, type, name, content)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (cf_record_id) DO NOTHING""",
                        (
                            rec.id,
                            row["domain_id"],
                            s["port"],
                            spec.type,
                            spec.name,
                            spec.content or f"{spec.srv.port} {spec.srv.target}",
                        ),
                    )
            async with db.transaction() as conn:
                await conn.execute(
                    "UPDATE slots SET dns_state = 'published', dns_error = NULL WHERE port = %s", (s["port"],)
                )
        return {"ids": created, "count": len(slots)}

    async def undo(self, ctx: JobContext, saved):
        # 途中で失敗したら、この実行で作ったものは残してよい（次の実行で同じものを使う）。状態だけ戻す
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE slots SET dns_state = 'error', dns_error = '作成の途中で失敗しました' "
                "WHERE rule_id = %s AND dns_state <> 'published'",
                (ctx.params["rule_id"],),
            )


class UnpublishAll(Step):
    key, name = "unpublish", "DNS を削除"
    timeout = 900

    async def run(self, ctx: JobContext):
        row, slots = await _targets(ctx.params["rule_id"], for_delete=True)
        if row is None:
            return {}
        ports = [s["port"] for s in slots]
        async with db.transaction() as conn:
            cur = await conn.execute(
                "SELECT cf_record_id FROM dns_records WHERE slot_port = ANY(%s) AND server_id IS NULL", (ports,)
            )
            ids = [r["cf_record_id"] for r in await cur.fetchall()]
        for rid in ids:
            await ctx.deps.dns.delete(row["cf_zone_id"], rid)
            async with db.transaction() as conn:
                await conn.execute("DELETE FROM dns_records WHERE cf_record_id = %s", (rid,))
        async with db.transaction() as conn:
            await conn.execute("UPDATE slots SET dns_state = 'none' WHERE port = ANY(%s)", (ports,))
        return {"deleted": len(ids)}


@job_kind("publish_slots")
def _publish(ctx: JobContext) -> list[Step]:
    return [PublishAll()]


@job_kind("unpublish_slots")
def _unpublish(ctx: JobContext) -> list[Step]:
    return [UnpublishAll()]
