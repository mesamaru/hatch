"""セットアップウィザード用のAPI。docs/API.md「セットアップウィザード」。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, IPvAnyAddress

from .. import db
from ..auth.session import Principal, require_admin
from ..errors import AppError
from ..repo import audit, edges as edges_repo

router = APIRouter(prefix="/api/admin", tags=["setup"])


# ---------------------------------------------------------------------------
# セットアップウィザード
# ---------------------------------------------------------------------------
@router.get("/getting-started")
async def get_setup_status(p: Principal = Depends(require_admin)) -> dict:
    """セットアップの進捗状態を取得"""
    async with db.transaction() as conn:
        # edges が登録されているか確認
        cur = await conn.execute("SELECT COUNT(*) as count FROM edges")
        edges_count = (await cur.fetchone())["count"]

        # firewalls は未実装（ダミーデータ）
        cur = await conn.execute("SELECT COUNT(*) as count FROM domains")
        domains_count = (await cur.fetchone())["count"]

    return {
        "edges_registered": edges_count > 0,
        "firewalls_configured": domains_count > 0,
        "linode_accounts_added": False,
    }


# ---------------------------------------------------------------------------
# Edge 管理
# ---------------------------------------------------------------------------
@router.get("/edges")
async def list_edges(p: Principal = Depends(require_admin)) -> dict:
    """登録済みの edge 一覧"""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """SELECT id, public_ip, tailscale_ip, is_active, last_seen_at, last_error
               FROM edges ORDER BY id"""
        )
        edges = [dict(r) for r in await cur.fetchall()]
    return {"items": edges}


class EdgeIn(BaseModel):
    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$")
    linode_account_id: int
    linode_instance_id: int
    firewall_id: int
    public_ip: IPvAnyAddress
    tailscale_ip: IPvAnyAddress


@router.post("/edges", status_code=201)
async def create_edge(body: EdgeIn, p: Principal = Depends(require_admin)) -> dict:
    """新しい edge を登録"""
    async with db.transaction() as conn:
        # 既に存在するか確認
        cur = await conn.execute("SELECT 1 FROM edges WHERE id = %s", (body.name,))
        if await cur.fetchone():
            raise AppError("already_exists", f"edge '{body.name}' は既に登録されています。", 409)

        # 新しい edge を作成（最初の edge は自動的に有効）
        cur = await conn.execute("SELECT COUNT(*) as count FROM edges")
        is_first = (await cur.fetchone())["count"] == 0

        cur = await conn.execute(
            """INSERT INTO edges (id, public_ip, tailscale_ip, is_active)
               VALUES (%s, %s, %s, %s)
               RETURNING id, public_ip, tailscale_ip, is_active""",
            (body.name, str(body.public_ip), str(body.tailscale_ip), is_first),
        )
        edge = dict(await cur.fetchone())

        # 操作ログを記録
        await audit.add(
            conn,
            action="edge を登録",
            via="web",
            actor_id=p.user_id,
            target=body.name,
        )

    return edge


# ---------------------------------------------------------------------------
# Firewall 管理（未実装）
# ---------------------------------------------------------------------------
@router.get("/firewalls")
async def list_firewalls(p: Principal = Depends(require_admin)) -> dict:
    """利用可能なファイアウォール一覧（ダミー）"""
    # TODO: Linode API から実際のファイアウォール一覧を取得
    return {
        "items": [
            {"id": 1, "label": "production-fw"},
            {"id": 2, "label": "staging-fw"},
        ]
    }


# ---------------------------------------------------------------------------
# Linode アカウント管理（未実装）
# ---------------------------------------------------------------------------
@router.get("/linode-accounts")
async def list_linode_accounts(p: Principal = Depends(require_admin)) -> dict:
    """登録済みの Linode アカウント一覧（ダミー）"""
    # TODO: DB から実際のアカウント一覧を取得
    return {
        "items": [
            {"id": 1, "label": "Linode (Main)"},
            {"id": 2, "label": "Linode (Backup)"},
        ]
    }
