"""初期設定画面の API。docs/API.md「初期設定」。

初期設定が終わるまでは、ログインの代わりに初期設定コード（X-Setup-Code ヘッダー）で操作を許す。
終わった後は status 以外 409 を返す。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from pydantic import BaseModel, Field

from .. import db
from .. import setup as st
from ..adapters import setup_checks
from ..config import LABELS, setup_problems
from ..errors import AppError
from ..repo import audit
from ..repo import setup as setup_repo

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status")
async def status() -> dict:
    async with db.transaction() as conn:
        needed = await st.needed(conn)
    if needed:
        st.ensure_code()
    return {"needed": needed, "restarting": st.restarting()}


async def _require_setup(code: str) -> None:
    async with db.transaction() as conn:
        if not await st.needed(conn):
            raise AppError("setup_done", "初期設定は完了しています。ログインしてから設定を変えてください。", 409)
    if not st.code_matches(code):
        raise AppError(
            "setup_code_invalid",
            "初期設定コードが違います。コンテナ内で hatch-setup を実行して、表示されたコードを入力してください。",
            403,
        )


async def require_code(x_setup_code: str = Header(default="")) -> None:
    await _require_setup(x_setup_code)


class Unlock(BaseModel):
    code: str = Field(max_length=40)


def _state() -> dict:
    return {
        "values": {k: st.current(k) for k in st.PUBLIC_KEYS},
        "secrets_set": [k for k in st.SECRET_KEYS if st.current(k)],
        "problems": setup_problems(),
    }


@router.post("/unlock")
async def unlock(body: Unlock) -> dict:
    await _require_setup(body.code)
    return _state()


class Values(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


def _clean(values: dict[str, str]) -> dict[str, str]:
    """画面から来た値を確かめる。空欄は「今の値のまま」を意味する。"""
    fields: dict[str, str] = {}
    out: dict[str, str] = {}
    for k, v in values.items():
        v = v.strip()
        if k not in st.SETUP_KEYS:
            fields[k] = "この項目は画面から設定できません"
        elif not st.SAFE_VALUE.match(v):
            fields[k] = "空白・引用符・$・# などの記号は使えません。コピーした値に余計な文字がないか確認してください"
        elif v:
            out[k] = v.rstrip("/") if k.endswith("_URL") else v
    if fields:
        raise AppError("validation", "入力内容を確認してください。", 400, {"fields": fields})
    return out


def _pick(values: dict[str, str], key: str) -> str:
    return values.get(key) or st.current(key)


@router.post("/check/{service}", dependencies=[Depends(require_code)])
async def check(service: Literal["panel", "cloudflare", "kuma", "discord"], body: Values) -> dict:
    v = _clean(body.values)
    if service == "panel":
        r = await setup_checks.check_panel(
            _pick(v, "PANEL_URL"), _pick(v, "PANEL_APP_KEY"), _pick(v, "PANEL_CLIENT_KEY")
        )
    elif service == "cloudflare":
        r = await setup_checks.check_cloudflare(_pick(v, "CF_API_TOKEN"))
    elif service == "kuma":
        r = await setup_checks.check_kuma(_pick(v, "KUMA_URL"), _pick(v, "KUMA_METRICS_KEY"))
    else:
        r = await setup_checks.check_discord(
            _pick(v, "DISCORD_CLIENT_ID"),
            _pick(v, "DISCORD_CLIENT_SECRET"),
            _pick(v, "DISCORD_BOT_TOKEN"),
            _pick(v, "DISCORD_GUILD_ID"),
        )
    return r.as_dict()


class RoleChoice(BaseModel):
    id: str = Field(pattern=r"^\d{5,25}$")
    name: str = Field(min_length=1, max_length=100)
    max_servers: int = Field(ge=0, le=100)


class Complete(Values):
    admin_role: RoleChoice
    user_roles: list[RoleChoice] = Field(default_factory=list, max_length=50)


@router.post("/complete", dependencies=[Depends(require_code)])
async def complete(body: Complete, background: BackgroundTasks) -> dict:
    v = _clean(body.values)
    problems = setup_problems(**v)
    if problems:
        fields = {k: f"{LABELS.get(k, k)}：{msg}" for k, msg in problems.items()}
        raise AppError("validation", "まだ入力されていない項目があります。", 400, {"fields": fields})
    if any(r.id == body.admin_role.id for r in body.user_roles):
        raise AppError("validation", "管理者のロールと利用者のロールには別のロールを選んでください。", 400)

    st.write_env_file(v)
    async with db.transaction() as conn:
        a = body.admin_role
        await setup_repo.upsert_role_rule(
            conn, role_id=a.id, label=a.name, grants_role="admin", max_servers=a.max_servers
        )
        for r in body.user_roles:
            await setup_repo.upsert_role_rule(
                conn, role_id=r.id, label=r.name, grants_role="user", max_servers=r.max_servers
            )
        await setup_repo.mark_completed(conn)
        await audit.add(
            conn,
            action="初期設定を保存",
            via="web",
            detail={"keys": sorted(v), "roles": 1 + len(body.user_roles)},
        )
    st.discard_code()
    background.add_task(st.request_restart)
    return {"restarting": True}
