"""Linode のアカウント・ファイアウォール・edge の登録（管理者）。docs/API.md「Linode・edge・ゲームパネル」、T43。

トークンは暗号化して保存し、応答・ログ・操作ログには出さない。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from .. import db
from ..adapters.linode import LinodeAdapter, LinodeClient
from ..auth import crypto
from ..auth.session import Principal, require_admin
from ..config import get_core_settings
from ..domain.firewall import merge
from ..errors import AppError, TransientError, UpstreamError
from ..jobs import firewall as _fw  # noqa: F401 - ジョブの登録
from ..jobs.engine import enqueue
from ..repo import audit

router = APIRouter(prefix="/api/admin", tags=["infra"])
EDGE_ID_RE = r"^[a-z0-9][a-z0-9-]{0,30}$"


def get_linode_factory() -> Callable[[str], LinodeAdapter]:
    """トークン → アダプター。テストでは app.dependency_overrides でフェイクに差し替える。"""
    return LinodeClient


def _linode_error(e: Exception) -> AppError:
    if isinstance(e, TransientError):
        return AppError("upstream_timeout", "Linode が応答しません。少し待ってからもう一度試してください。", 504)
    status = ((getattr(e, "detail", None) or {}).get("status_code")) if isinstance(e, UpstreamError) else None
    if status in (401, 403):
        return AppError(
            "linode_token_invalid",
            "Linode の API トークンが使えません。"
            "Linodes の読み取りと Firewalls の読み書きの権限があるか確認してください。",
            400,
        )
    return e if isinstance(e, AppError) else AppError("upstream_error", "Linode がエラーを返しました。", 502)


async def _account(conn, account_id: int) -> dict:
    cur = await conn.execute("SELECT id, label, token_enc FROM linode_accounts WHERE id = %s", (account_id,))
    row = await cur.fetchone()
    if row is None:
        raise AppError("not_found", "Linode のアカウントが見つかりません。", 404)
    return row


async def _with_linode(account: dict, factory, fn):
    linode = factory(crypto.decrypt(account["token_enc"], "linode"))
    try:
        return await fn(linode)
    except (UpstreamError, TransientError) as e:
        raise _linode_error(e) from None
    finally:
        await linode.aclose()


async def _enqueue_sync(conn, p: Principal) -> int:
    return await enqueue(conn, "sync_firewall", via="web", params={}, requested_by=p.user_id)


# ---------------------------------------------------------------------------
# Linode のアカウント
# ---------------------------------------------------------------------------
class AccountIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    token: str = Field(min_length=20, max_length=200)


@router.get("/linode-accounts")
async def list_accounts(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT a.id, a.label, a.created_at,
                      (SELECT count(*) FROM edges e WHERE e.linode_account_id = a.id) AS edges,
                      (SELECT count(*) FROM firewalls f WHERE f.linode_account_id = a.id) AS firewalls
               FROM linode_accounts a ORDER BY a.label"""
        )
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.post("/linode-accounts", status_code=201)
async def add_account(
    body: AccountIn, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)
) -> dict:
    token = body.token.strip()
    linode = factory(token)
    try:
        await linode.check()
    except (UpstreamError, TransientError) as e:
        raise _linode_error(e) from None
    finally:
        await linode.aclose()
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT 1 FROM linode_accounts WHERE label = %s", (body.label,))
        if await cur.fetchone():
            raise AppError("already_exists", "同じ名前のアカウントがあります。別の名前にしてください。", 409)
        cur = await conn.execute(
            "INSERT INTO linode_accounts (label, token_enc) VALUES (%s, %s) RETURNING id",
            (body.label, crypto.encrypt(token, "linode")),
        )
        aid = (await cur.fetchone())["id"]
        await audit.add(conn, action="Linode のアカウントを追加", via="web", actor_id=p.user_id, target=body.label)
    return {"id": aid, "label": body.label}


@router.delete("/linode-accounts/{account_id}", status_code=204)
async def delete_account(account_id: int, p: Principal = Depends(require_admin)) -> Response:
    async with db.transaction() as conn:
        acc = await _account(conn, account_id)
        cur = await conn.execute(
            "SELECT (SELECT count(*) FROM edges WHERE linode_account_id = %s)"
            " + (SELECT count(*) FROM firewalls WHERE linode_account_id = %s) AS n",
            (account_id, account_id),
        )
        if (await cur.fetchone())["n"]:
            raise AppError("in_use", "このアカウントを使っている edge とファイアウォールを先に外してください。", 409)
        await conn.execute("DELETE FROM linode_accounts WHERE id = %s", (account_id,))
        await audit.add(conn, action="Linode のアカウントを削除", via="web", actor_id=p.user_id, target=acc["label"])
    return Response(status_code=204)


@router.get("/linode-accounts/{account_id}/linodes")
async def account_linodes(account_id: int, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)):
    async with db.transaction() as conn:
        acc = await _account(conn, account_id)
    items = await _with_linode(acc, factory, lambda li: li.linodes())
    return {"items": [{"id": x.id, "label": x.label, "region": x.region, "ipv4": list(x.ipv4)} for x in items]}


@router.get("/linode-accounts/{account_id}/firewalls")
async def account_firewalls(
    account_id: int, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)
):
    async with db.transaction() as conn:
        acc = await _account(conn, account_id)
        cur = await conn.execute(
            "SELECT linode_firewall_id, id FROM firewalls WHERE linode_account_id = %s", (account_id,)
        )
        managed = {r["linode_firewall_id"]: r["id"] for r in await cur.fetchall()}
    items = await _with_linode(acc, factory, lambda li: li.firewalls())
    return {
        "items": [{"id": x.id, "label": x.label, "status": x.status, "managed_id": managed.get(x.id)} for x in items]
    }


# ---------------------------------------------------------------------------
# ファイアウォール（Hatch が管理するもの）
# ---------------------------------------------------------------------------
class FirewallIn(BaseModel):
    linode_account_id: int
    linode_firewall_id: int


@router.get("/firewalls")
async def list_firewalls(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT f.id, f.label, f.linode_account_id, a.label AS account, f.linode_firewall_id,
                      f.synced_at, f.last_error,
                      COALESCE((SELECT array_agg(e.id ORDER BY e.id) FROM edges e WHERE e.firewall_id = f.id), '{}')
                        AS edges
               FROM firewalls f JOIN linode_accounts a ON a.id = f.linode_account_id ORDER BY a.label, f.label"""
        )
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.post("/firewalls", status_code=201)
async def add_firewall(
    body: FirewallIn, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)
) -> dict:
    async with db.transaction() as conn:
        acc = await _account(conn, body.linode_account_id)
    found = [f for f in await _with_linode(acc, factory, lambda li: li.firewalls()) if f.id == body.linode_firewall_id]
    if not found:
        raise AppError("not_found", "このアカウントに、そのファイアウォールはありません。", 404)
    async with db.transaction() as conn:
        cur = await conn.execute(
            "SELECT id FROM firewalls WHERE linode_account_id = %s AND linode_firewall_id = %s",
            (body.linode_account_id, body.linode_firewall_id),
        )
        if await cur.fetchone():
            raise AppError("already_exists", "このファイアウォールは登録済みです。", 409)
        cur = await conn.execute(
            "INSERT INTO firewalls (linode_account_id, linode_firewall_id, label) VALUES (%s,%s,%s) RETURNING id",
            (body.linode_account_id, body.linode_firewall_id, found[0].label),
        )
        fid = (await cur.fetchone())["id"]
        await audit.add(conn, action="ファイアウォールを登録", via="web", actor_id=p.user_id, target=found[0].label)
    return {"id": fid, "label": found[0].label}


@router.delete("/firewalls/{firewall_id}", status_code=204)
async def delete_firewall(
    firewall_id: int, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)
) -> Response:
    """管理をやめる。Linode 側の Hatch のルールは消す（手で作ったルールは残す）。"""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT f.label, f.linode_firewall_id, a.id AS account_id, a.token_enc,
                      EXISTS (SELECT 1 FROM edges e WHERE e.firewall_id = f.id) AS used
               FROM firewalls f JOIN linode_accounts a ON a.id = f.linode_account_id WHERE f.id = %s""",
            (firewall_id,),
        )
        fw = await cur.fetchone()
    if fw is None:
        raise AppError("not_found", "ファイアウォールが見つかりません。", 404)
    if fw["used"]:
        raise AppError("in_use", "このファイアウォールを使っている edge から先に外してください。", 409)
    instance = get_core_settings().PD_INSTANCE

    async def clear(li):
        current = await li.get_rules(fw["linode_firewall_id"])
        await li.put_rules(fw["linode_firewall_id"], merge(current, instance, {}))

    await _with_linode({"token_enc": fw["token_enc"]}, factory, clear)
    async with db.transaction() as conn:
        await conn.execute("DELETE FROM firewalls WHERE id = %s", (firewall_id,))
        await audit.add(
            conn, action="ファイアウォールの管理をやめる", via="web", actor_id=p.user_id, target=fw["label"]
        )
    return Response(status_code=204)


@router.post("/firewalls/{firewall_id}/sync", status_code=202)
async def sync_firewall(firewall_id: int, p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT label FROM firewalls WHERE id = %s", (firewall_id,))
        fw = await cur.fetchone()
        if fw is None:
            raise AppError("not_found", "ファイアウォールが見つかりません。", 404)
        job = await _enqueue_sync(conn, p)
    return {"job": {"id": job, "kind": "sync_firewall", "status": "queued"}}


# ---------------------------------------------------------------------------
# edge
# ---------------------------------------------------------------------------
class EdgeIn(BaseModel):
    id: str = Field(pattern=EDGE_ID_RE)
    public_ip: str
    tailscale_ip: str
    linode_account_id: int | None = None
    linode_id: int | None = None
    firewall_id: int | None = None


class EdgePatch(BaseModel):
    public_ip: str | None = None
    tailscale_ip: str | None = None
    linode_account_id: int | None = None
    linode_id: int | None = None
    firewall_id: int | None = None
    clear_firewall: bool = False  # ファイアウォールを外す


def _ip(value: str, field: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        raise AppError(
            "validation", "IP アドレスの形式が正しくありません。", 400, {"fields": {field: "形式が正しくありません"}}
        ) from None


async def _check_links(conn, account_id: int | None, linode_id: int | None, firewall_id: int | None) -> dict | None:
    """edge の Linode とファイアウォールの組み合わせを確かめ、ファイアウォールの行を返す。"""
    if (linode_id is None) != (account_id is None):
        raise AppError("validation", "Linode のアカウントと Linode を両方選んでください。", 400)
    if firewall_id is None:
        return None
    if account_id is None:
        raise AppError("validation", "ファイアウォールを使うには、Linode のアカウントと Linode を選んでください。", 400)
    cur = await conn.execute(
        "SELECT id, linode_account_id, linode_firewall_id FROM firewalls WHERE id = %s", (firewall_id,)
    )
    fw = await cur.fetchone()
    if fw is None:
        raise AppError("not_found", "ファイアウォールが見つかりません。", 404)
    if fw["linode_account_id"] != account_id:
        raise AppError("validation", "edge と同じ Linode のアカウントのファイアウォールを選んでください。", 400)
    return fw


async def _attach(conn, account_id: int, linode_id: int, fw: dict, factory) -> None:
    """edge の Linode がファイアウォールに付いていなければ付ける。"""
    acc = await _account(conn, account_id)

    async def run(li):
        if linode_id not in await li.device_linode_ids(fw["linode_firewall_id"]):
            await li.attach_linode(fw["linode_firewall_id"], linode_id)

    await _with_linode(acc, factory, run)


@router.get("/edges")
async def list_edges(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT e.id, host(e.public_ip) AS public_ip, host(e.tailscale_ip) AS tailscale_ip, e.is_active,
                      e.applied_version, e.last_seen_at, e.last_error, e.linode_account_id, a.label AS account,
                      e.linode_id, e.firewall_id, f.label AS firewall
               FROM edges e LEFT JOIN linode_accounts a ON a.id = e.linode_account_id
               LEFT JOIN firewalls f ON f.id = e.firewall_id ORDER BY e.id"""
        )
        return {"items": [dict(r) for r in await cur.fetchall()]}


@router.post("/edges", status_code=201)
async def add_edge(body: EdgeIn, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)) -> dict:
    public_ip, ts_ip = _ip(body.public_ip, "public_ip"), _ip(body.tailscale_ip, "tailscale_ip")
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT 1 FROM edges WHERE id = %s", (body.id,))
        if await cur.fetchone():
            raise AppError("already_exists", "同じ名前の edge があります。", 409)
        fw = await _check_links(conn, body.linode_account_id, body.linode_id, body.firewall_id)
        if fw:
            await _attach(conn, body.linode_account_id, body.linode_id, fw, factory)
        cur = await conn.execute("SELECT NOT EXISTS (SELECT 1 FROM edges) AS first")
        first = (await cur.fetchone())["first"]
        await conn.execute(
            """INSERT INTO edges (id, public_ip, tailscale_ip, is_active, linode_account_id, linode_id, firewall_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (body.id, public_ip, ts_ip, first, body.linode_account_id, body.linode_id, body.firewall_id),
        )
        job = await _enqueue_sync(conn, p) if fw else None
        await audit.add(conn, action="edge を登録", via="web", actor_id=p.user_id, target=body.id, job_id=job)
    return {"id": body.id, "is_active": first, "job": {"id": job} if job else None}


@router.patch("/edges/{edge_id}")
async def patch_edge(
    edge_id: str, body: EdgePatch, p: Principal = Depends(require_admin), factory=Depends(get_linode_factory)
) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT * FROM edges WHERE id = %s FOR UPDATE", (edge_id,))
        e = await cur.fetchone()
        if e is None:
            raise AppError("not_found", "edge が見つかりません。", 404)
        account_id = body.linode_account_id if body.linode_account_id is not None else e["linode_account_id"]
        linode_id = body.linode_id if body.linode_id is not None else e["linode_id"]
        firewall_id = None if body.clear_firewall else (body.firewall_id or e["firewall_id"])
        fw = await _check_links(conn, account_id, linode_id, firewall_id)
        if fw:
            await _attach(conn, account_id, linode_id, fw, factory)
        await conn.execute(
            """UPDATE edges SET public_ip = %s, tailscale_ip = %s, linode_account_id = %s, linode_id = %s,
                                firewall_id = %s WHERE id = %s""",
            (
                _ip(body.public_ip, "public_ip") if body.public_ip else e["public_ip"],
                _ip(body.tailscale_ip, "tailscale_ip") if body.tailscale_ip else e["tailscale_ip"],
                account_id,
                linode_id,
                firewall_id,
                edge_id,
            ),
        )
        job = await _enqueue_sync(conn, p)
        await audit.add(conn, action="edge を変更", via="web", actor_id=p.user_id, target=edge_id, job_id=job)
    return {"id": edge_id, "firewall_id": firewall_id, "job": {"id": job}}


@router.delete("/edges/{edge_id}", status_code=204)
async def delete_edge(edge_id: str, p: Principal = Depends(require_admin)) -> Response:
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT is_active FROM edges WHERE id = %s", (edge_id,))
        e = await cur.fetchone()
        if e is None:
            raise AppError("not_found", "edge が見つかりません。", 404)
        if e["is_active"]:
            raise AppError("in_use", "使用中の edge は削除できません。先に別の edge に切り替えてください。", 409)
        await conn.execute("UPDATE ip_addresses SET edge_id = NULL WHERE edge_id = %s", (edge_id,))
        await conn.execute("DELETE FROM edges WHERE id = %s", (edge_id,))
        await audit.add(conn, action="edge を削除", via="web", actor_id=p.user_id, target=edge_id)
    return Response(status_code=204)
