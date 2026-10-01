"""はじめの設定の進み具合（管理者）。docs/SPEC.md 8「はじめの設定」、docs/API.md GettingStarted、T48。

状態は登録済みのデータから毎回計算する（別の表には保存しない）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db
from ..auth.session import Principal, require_admin
from ..domain.names import fqdn

router = APIRouter(prefix="/api/admin", tags=["getting-started"])

ACTIVE = ("queued", "running")


def _step(key: str, required: bool, state: str, detail=None, problem=None, retry=None) -> dict:
    return {"key": key, "required": required, "state": state, "detail": detail, "problem": problem, "retry": retry}


async def _load(conn) -> dict:
    async def rows(sql: str, args=()):
        cur = await conn.execute(sql, args)
        return await cur.fetchall()

    return {
        "accounts": await rows("SELECT label FROM linode_accounts ORDER BY id"),
        "firewalls": await rows("SELECT label FROM firewalls ORDER BY id"),
        "edges": await rows("SELECT id, host(public_ip) AS ip, is_active FROM edges ORDER BY id"),
        "domains": await rows("SELECT name FROM domains ORDER BY name"),
        # 「使用中の edge に追従」の紐付けと、それぞれの最後の反映ジョブ
        "bindings": await rows(
            """SELECT b.id, b.host, d.name AS domain, b.synced_at, j.status AS job_status, j.error AS job_error
               FROM ip_bindings b JOIN domains d ON d.id = b.domain_id
               LEFT JOIN LATERAL (
                 SELECT status, error FROM jobs
                 WHERE kind = 'sync_binding' AND params->>'binding_id' = b.id::text ORDER BY id DESC LIMIT 1
               ) j ON true
               WHERE b.follow_active_edge ORDER BY d.name, b.host"""
        ),
        # 枠ごとの DNS の状態と、最後の事前作成ジョブ
        "rules": await rows(
            """SELECT r.id, r.name, r.prepublish, r.host_template,
                      count(sl.port) FILTER (WHERE sl.dns_state <> 'published') AS unpublished,
                      count(sl.port) FILTER (WHERE sl.dns_state = 'error') AS errors,
                      min(sl.dns_error) FILTER (WHERE sl.dns_state = 'error') AS dns_error,
                      j.status AS job_status, j.error AS job_error
               FROM slot_rules r LEFT JOIN slots sl ON sl.rule_id = r.id
               LEFT JOIN LATERAL (
                 SELECT status, error FROM jobs
                 WHERE kind = 'publish_slots' AND params->>'rule_id' = r.id::text ORDER BY id DESC LIMIT 1
               ) j ON true
               GROUP BY r.id, j.status, j.error ORDER BY r.id"""
        ),
    }


def compute(d: dict) -> dict:
    steps: list[dict] = []
    has_edge = bool(d["edges"])
    active = next((e for e in d["edges"] if e["is_active"]), None)

    # 1. Linode のアカウント（任意）
    if d["accounts"]:
        steps.append(_step("linode", False, "done", "・".join(a["label"] for a in d["accounts"])))
    else:
        steps.append(_step("linode", False, "skipped" if has_edge else "todo"))

    # 2. ファイアウォール（任意）
    if d["firewalls"]:
        steps.append(_step("firewall", False, "done", "・".join(f["label"] for f in d["firewalls"])))
    elif not d["accounts"]:
        steps.append(_step("firewall", False, "skipped" if has_edge else "locked"))
    else:
        steps.append(_step("firewall", False, "skipped" if has_edge else "todo"))

    # 3. edge
    if active:
        steps.append(_step("edge", True, "done", f"{active['id']}（{active['ip']}）"))
    else:
        steps.append(_step("edge", True, "todo"))

    # 4. ドメイン（edge.<ドメイン> の A レコードまで）
    if not d["domains"]:
        steps.append(_step("domain", True, "todo" if active else "locked"))
    else:
        detail = "・".join(x["name"] for x in d["domains"])
        pending = [b for b in d["bindings"] if b["synced_at"] is None]
        if not pending:
            steps.append(_step("domain", True, "done", detail))
        elif any(b["job_status"] in ACTIVE for b in pending):
            steps.append(_step("domain", True, "working", detail))
        else:
            names = "・".join(fqdn(b["host"], b["domain"]) for b in pending)
            if not active:
                problem = (
                    f"{names} の A レコードを作れていません。使用中の edge がまだ無いためです。"
                    "先に edge を登録すると、自動で作り直します。"
                )
            else:
                reason = next((b["job_error"] for b in pending if b["job_error"]), None)
                problem = f"{names} の A レコードを作れていません。" + (f"（{reason}）" if reason else "")
            steps.append(_step("domain", True, "error", detail, problem, {"bindings": [b["id"] for b in pending]}))

    # 5. アドレス枠
    if d["rules"]:
        steps.append(_step("rule", True, "done", "・".join(r["name"] for r in d["rules"])))
    else:
        steps.append(_step("rule", True, "todo" if d["domains"] else "locked"))

    # 6. アドレス枠の DNS（固定のホスト名で「事前に作成」の枠だけ）
    targets = [r for r in d["rules"] if r["prepublish"] and "{server}" not in r["host_template"]]
    if not d["rules"]:
        steps.append(_step("dns", True, "locked"))
    else:
        waiting = [r for r in targets if r["unpublished"]]
        if not waiting:
            steps.append(_step("dns", True, "done", None if targets else "サーバーを作るときに作成します"))
        elif any(r["job_status"] in ACTIVE for r in waiting):
            steps.append(_step("dns", True, "working"))
        else:
            failed = [r for r in waiting if r["errors"] or r["job_status"] in ("failed", "rolled_back")]
            retry = {"rules": [r["id"] for r in waiting]}
            names = "・".join(r["name"] for r in waiting)
            if failed:
                reason = next((x for r in failed if (x := r["job_error"] or r["dns_error"])), None)
                problem = f"「{names}」の DNS を作れていません。" + (f"（{reason}）" if reason else "")
                steps.append(_step("dns", True, "error", names, problem, retry))
            else:
                steps.append(_step("dns", True, "todo", names, None, retry))

    required = [s for s in steps if s["required"]]
    done = sum(s["state"] == "done" for s in required)
    return {"done": done, "total": len(required), "complete": done == len(required), "steps": steps}


@router.get("/getting-started")
async def getting_started(p: Principal = Depends(require_admin)) -> dict:
    async with db.transaction() as conn:
        return compute(await _load(conn))
