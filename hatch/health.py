"""自己監視：各プロセスの生存報告と、オーケストレーター全体の健康状態の判定。

- 各プロセス（worker・scheduler・bot）は beat() を30秒ごとに呼ぶ
- /api/health       … 最小限（DB に繋がるか）。update コマンドと外部の死活確認用。認証なし
- /api/health/full  … 詳細。PD_HEALTH_TOKEN が必要。Uptime Kuma のキーワード監視に使う
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from . import __version__

# プロセスの報告がこの秒数より古ければ「止まっている」と判断する
STALE_SECONDS = 90
# 待ち行列の最古のジョブがこの秒数より古ければ「詰まっている」と判断する
QUEUE_LAG_WARN = 120
# edge の報告がこの秒数より古ければ警告
EDGE_STALE_SECONDS = 180
# 空き容量（割合）がこれを下回ったら警告・異常
DISK_WARN, DISK_CRIT = 0.15, 0.05

REQUIRED = ("worker", "scheduler")  # 止まっていたら error
OPTIONAL = ("bot",)  # 止まっていたら degraded


def _dsn() -> str:
    return os.environ["DATABASE_URL"]


def beat(component: str, detail: dict | None = None, *, conn: psycopg.Connection | None = None) -> None:
    """生存報告。失敗しても呼び出し元を止めない（監視のために本処理を落とさない）。"""
    sql = """
        INSERT INTO component_heartbeats (component, instance, version, beat_at, detail)
        VALUES (%s, %s, %s, now(), %s)
        ON CONFLICT (component) DO UPDATE
          SET instance = EXCLUDED.instance, version = EXCLUDED.version,
              beat_at = EXCLUDED.beat_at, detail = EXCLUDED.detail
    """
    args = (component, f"{socket.gethostname()}:{os.getpid()}", __version__, json.dumps(detail) if detail else None)
    try:
        if conn is not None:
            conn.execute(sql, args)
        else:
            with psycopg.connect(_dsn(), connect_timeout=3) as c:
                c.execute(sql, args)
    except Exception:
        pass


@dataclass
class Check:
    name: str
    level: str  # ok / degraded / error
    message: str  # 日本語。画面と Discord の通知にそのまま使う
    value: object = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def status(self) -> str:
        levels = {c.level for c in self.checks}
        return "error" if "error" in levels else "degraded" if "degraded" in levels else "ok"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "version": __version__,
            "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "checks": [c.__dict__ for c in self.checks],
        }


def _disk_check(name: str, path: str) -> Check:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return Check(name, "degraded", f"{path} の容量を確認できません")
    free = u.free / u.total
    gb = u.free / 2**30
    # 割合だけで判定すると大きなディスクで誤報になるため、残り容量（GB）も見る
    level = "error" if (free < DISK_CRIT and gb < 2) else "degraded" if (free < DISK_WARN and gb < 10) else "ok"
    return Check(name, level, f"空き {free:.0%}（{u.free // 2**30}GB）", round(free, 3))


def full_report(now: datetime | None = None) -> Report:
    """オーケストレーター全体の状態。DB に繋がらなければ、それだけを返す。"""
    rep = Report()
    try:
        conn = psycopg.connect(_dsn(), connect_timeout=3)
    except Exception as exc:
        rep.checks.append(Check("db", "error", f"データベースに接続できません（{exc.__class__.__name__}）"))
        return rep
    with conn:
        now = now or conn.execute("SELECT now()").fetchone()[0]
        rep.checks.append(Check("db", "ok", "接続できます"))

        beats = {
            r[0]: (r[1], r[2]) for r in conn.execute("SELECT component, beat_at, version FROM component_heartbeats")
        }
        for comp in REQUIRED + OPTIONAL:
            bad = "error" if comp in REQUIRED else "degraded"
            if comp not in beats:
                rep.checks.append(Check(comp, bad, "一度も報告がありません"))
                continue
            age = (now - beats[comp][0]).total_seconds()
            if age > STALE_SECONDS:
                rep.checks.append(Check(comp, bad, f"{int(age)}秒間報告がありません", int(age)))
            elif beats[comp][1] != __version__:
                rep.checks.append(
                    Check(comp, "degraded", f"バージョンが違います（{beats[comp][1]}）。再起動が必要です")
                )
            else:
                rep.checks.append(Check(comp, "ok", "動いています", int(age)))

        lag = conn.execute(
            "SELECT extract(epoch FROM now() - min(run_after)) FROM jobs WHERE status = 'queued' AND run_after <= now()"
        ).fetchone()[0]
        lag = int(lag or 0)
        rep.checks.append(
            Check(
                "queue",
                "degraded" if lag > QUEUE_LAG_WARN else "ok",
                f"最も古い待ちジョブ {lag}秒" if lag else "待ちジョブはありません",
                lag,
            )
        )

        failed = conn.execute(
            "SELECT count(*) FROM jobs WHERE status = 'failed' AND finished_at > now() - interval '24 hours'"
        ).fetchone()[0]
        rep.checks.append(
            Check(
                "jobs",
                "degraded" if failed else "ok",
                f"24時間以内に取り消せなかったジョブ {failed}件" if failed else "失敗したジョブはありません",
                failed,
            )
        )

        for name, last in conn.execute("SELECT id, last_seen_at FROM edges ORDER BY id"):
            if last is None:
                rep.checks.append(Check(f"edge:{name}", "degraded", "エージェントからの報告がありません"))
                continue
            age = int((now - last).total_seconds())
            rep.checks.append(
                Check(f"edge:{name}", "degraded" if age > EDGE_STALE_SECONDS else "ok", f"{age}秒前に報告", age)
            )

    rep.checks.append(_disk_check("disk", os.environ.get("PD_DATA_DIR", "/var/lib/hatch")))
    return rep
