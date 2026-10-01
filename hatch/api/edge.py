"""edge エージェント用の API。docs/API.md「edge エージェント」。"""

from __future__ import annotations

import hmac
import ipaddress

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..errors import AppError
from ..logging import mask
from ..repo import edges as repo

router = APIRouter(prefix="/api/edge", tags=["edge"])


def require_edge_token(authorization: str | None = Header(default=None)) -> None:
    want = get_settings().EDGE_AGENT_TOKEN.get_secret_value()
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not given or not hmac.compare_digest(given.encode(), want.encode()):
        raise AppError("unauthenticated", "edge エージェントのトークンが正しくありません。", 401)


@router.get("/config", dependencies=[Depends(require_edge_token)])
async def get_config(edge: str = Query(pattern=r"^[a-z0-9-]{1,32}$"), have: int = Query(default=0, ge=0)):
    async with db.transaction() as conn:
        if await repo.get_edge(conn, edge) is None:
            raise AppError(
                "edge_unknown", f"{edge} は登録されていません。管理画面の「ノードと edge」で登録してください。", 404
            )
        await repo.touch(conn, edge)
        latest = await repo.latest_version(conn)
    if latest is None or latest["version"] <= have:
        return Response(status_code=204)
    return {"version": latest["version"], "haproxy_cfg": latest["haproxy_cfg"], "nft_rules": latest["nft_rules"]}


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def _tailscale_ip(host: str | None) -> str | None:
    """edge は Tailscale 経由で報告するので、送信元が Tailscale の IP ならそれを edge の Tailscale の IP とする。"""
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return None
    return str(ip) if ip.version == 4 and ip in TAILSCALE_NET else None


class Report(BaseModel):
    edge_id: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    applied_version: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=4000)


@router.post("/report", status_code=204, dependencies=[Depends(require_edge_token)])
async def post_report(body: Report, request: Request) -> Response:
    async with db.transaction() as conn:
        if await repo.get_edge(conn, body.edge_id) is None:
            raise AppError("edge_unknown", f"{body.edge_id} は登録されていません。", 404)
        await repo.report(
            conn,
            body.edge_id,
            body.applied_version,
            mask(body.error)[:2000] if body.error else None,
            _tailscale_ip(request.client.host if request.client else None),
        )
    return Response(status_code=204)
