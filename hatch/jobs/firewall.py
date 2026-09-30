"""Linode Cloud Firewall の反映。docs/SPEC.md 6B、IMPLEMENTATION.md 7A.2。

開けるポート＝アドレスを使っているサーバー（使用中・ゴミ箱で予約中）のポート。
完全に削除する・作成を取り消すとアドレスが解放されるので、次の反映で締まる。
edge が使っているファイアウォールごとに、Hatch のルールだけを差し替える（手で作ったルールは残す）。
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg import AsyncConnection

from .. import db
from ..adapters.linode import LinodeAdapter, LinodeClient
from ..auth import crypto
from ..config import get_core_settings
from ..domain.firewall import FirewallTooLarge, merge, same
from ..errors import AppError, TransientError, UpstreamError
from ..games import Game
from .engine import JobContext, Step, enqueue, job_kind

log = logging.getLogger("hatch.firewall")


def linode_for(deps: Any, token: str) -> LinodeAdapter:
    factory = getattr(deps, "linode", None)
    return factory(token) if factory else LinodeClient(token)


async def open_ports(conn: AsyncConnection, games: dict[str, Game]) -> dict[str, set[int]]:
    cur = await conn.execute(
        """SELECT sl.port, s.game FROM slots sl JOIN servers s ON s.id = sl.server_id
           WHERE sl.status IN ('assigned','held') AND s.status NOT IN ('purged','failed')"""
    )
    out: dict[str, set[int]] = {"tcp": set(), "udp": set()}
    for r in await cur.fetchall():
        g = games.get(r["game"])
        out[g.protocol if g else "tcp"].add(r["port"])
    return out


async def managed_firewalls(conn: AsyncConnection) -> list[dict[str, Any]]:
    """edge が使っているファイアウォール（共有なら1つにまとまる）。"""
    cur = await conn.execute(
        """SELECT DISTINCT f.id, f.linode_firewall_id, f.label, a.token_enc
           FROM firewalls f JOIN linode_accounts a ON a.id = f.linode_account_id
           JOIN edges e ON e.firewall_id = f.id ORDER BY f.id"""
    )
    return list(await cur.fetchall())


async def sync_all(deps: Any) -> list[str]:
    """すべてのファイアウォールに反映する。変更したファイアウォールの名前を返す。"""
    async with db.transaction() as conn:
        ports = await open_ports(conn, deps.games)
        fws = await managed_firewalls(conn)
    instance = get_core_settings().PD_INSTANCE
    changed: list[str] = []
    first_error: Exception | None = None
    for fw in fws:
        linode = linode_for(deps, crypto.decrypt(fw["token_enc"], "linode"))
        error = None
        try:
            current = await linode.get_rules(fw["linode_firewall_id"])
            wanted = merge(current, instance, ports)
            if not same(current, wanted):
                await linode.put_rules(fw["linode_firewall_id"], wanted)
                changed.append(fw["label"])
        except FirewallTooLarge as e:
            error = str(e)
            first_error = first_error or AppError("firewall_full", f"ファイアウォール「{fw['label']}」：{e}", 409)
        except (UpstreamError, TransientError) as e:
            error = getattr(e, "message", str(e))
            first_error = first_error or e
        finally:
            await linode.aclose()
        async with db.transaction() as conn:
            if error:
                await conn.execute("UPDATE firewalls SET last_error = %s WHERE id = %s", (error, fw["id"]))
            else:
                await conn.execute(
                    "UPDATE firewalls SET last_error = NULL, synced_at = now() WHERE id = %s", (fw["id"],)
                )
        if error:
            log.warning("ファイアウォール「%s」に反映できませんでした：%s", fw["label"], error)
    if first_error:
        raise first_error
    return changed


class SyncFirewalls(Step):
    key, name = "firewall", "ファイアウォールに反映"
    timeout = 120

    async def run(self, ctx: JobContext):
        return {"changed": await sync_all(ctx.deps)}


class OpenFirewall(SyncFirewalls):
    """作成：edge に公開した直後に、そのサーバーのポートを開ける。"""

    key, name = "firewall", "ファイアウォールを開ける"

    async def undo(self, ctx: JobContext, saved):
        # 取り消しの途中ではまだアドレスが確保されたままなので、取り消しが終わった後に締める
        async with db.transaction() as conn:
            if await managed_firewalls(conn):  # 管理しているファイアウォールが無ければ何もしない
                await enqueue(conn, "sync_firewall", via="system", params={}, requested_by=None)


class CloseFirewall(SyncFirewalls):
    """完全に削除：アドレスを解放した後に、そのサーバーのポートを締める。"""

    key, name = "firewall", "ファイアウォールを締める"


@job_kind("sync_firewall")
def sync_steps(ctx: JobContext) -> list[Step]:
    return [SyncFirewalls()]
