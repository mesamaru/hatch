"""ジョブ実行基盤。

ジョブ = 手順（Step）の並び。各手順は「実行」と「取り消し」を対で持つ。

- 手順が失敗したら、失敗した手順（途中まで作ったものがあるかもしれないので saved=None で）と、
  完了済みの手順を逆順に取り消し、ジョブを rolled_back にする。取り消しにも失敗したら failed。
- ワーカーが途中で落ちても、locked_at が古くなったジョブを別のワーカーが引き継ぎ、
  「完了した手順の次」から再開する。そのため run() も undo() も **何度呼ばれても同じ結果** に書く。
- 同じサーバーのジョブは同時に1つだけ（セッション単位のアドバイザリーロック）。

手順の書き方:

    class CreateThing(Step):
        key = "create_thing"            # ジョブ内で一意。再開時の対応付けに使う（変えないこと）
        name = "◯◯を作成"               # 画面・Discord に出す日本語

        def skip(self, ctx):             # 飛ばす理由（なければ None）
            return "作成済み" if ctx.params.get("already") else None

        async def run(self, ctx):
            thing = await ctx.deps.panel.create(...)   # 既にあれば既存を使う（冪等）
            return {"thing_id": thing.id}                # 取り消しと後の手順に必要な情報

        async def undo(self, ctx, saved):
            # saved が None = run の途中で失敗した。外部側を探して、あれば消す
            ...

後の手順は ctx.results["create_thing"]["thing_id"] で前の手順の結果を読める。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .. import db
from ..errors import AppError, TransientError
from ..logging import bind, mask

log = logging.getLogger("hatch.jobs")

LOCK_NAMESPACE = 7_420_118  # サーバー単位のアドバイザリーロック（2引数形式の1つ目）
STALE_AFTER_SECONDS = 300  # これより古い locked_at の running ジョブは引き継ぐ
HEARTBEAT_SECONDS = 30
RETRY_DELAYS: Sequence[float] = (1, 4, 10)
BUSY_RETRY_SECONDS = 3  # サーバーが他のジョブで使用中のとき、この秒数後に再試行


@dataclass
class JobContext:
    job_id: int
    kind: str
    params: dict[str, Any]
    server_id: str | None
    requested_by: str | None
    via: str
    deps: Any = None
    results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def result(self, key: str) -> dict[str, Any]:
        return self.results.get(key) or {}


class Step:
    key: str = ""
    name: str = ""
    timeout: float = 300.0

    def skip(self, ctx: JobContext) -> str | None:
        return None

    async def run(self, ctx: JobContext) -> dict[str, Any] | None:
        raise NotImplementedError

    async def undo(self, ctx: JobContext, saved: dict[str, Any] | None) -> None:
        return None


StepFactory = Callable[[JobContext], list[Step]]
_REGISTRY: dict[str, StepFactory] = {}


def job_kind(kind: str) -> Callable[[StepFactory], StepFactory]:
    """ジョブの種類を登録する。手順の並びは params だけから決まるように書く（再開時に同じ並びになるように）。"""

    def deco(fn: StepFactory) -> StepFactory:
        if kind in _REGISTRY and _REGISTRY[kind] is not fn:
            raise ValueError(f"ジョブの種類 {kind} が二重に登録されています")
        _REGISTRY[kind] = fn
        return fn

    return deco


def registered_kinds() -> list[str]:
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# 登録
# ---------------------------------------------------------------------------
async def enqueue(
    conn: AsyncConnection,
    kind: str,
    *,
    via: str,
    params: dict[str, Any] | None = None,
    server_id: str | None = None,
    requested_by: str | None = None,
    idempotency_key: str | None = None,
    delay_seconds: float = 0,
) -> int:
    """ジョブを登録して ID を返す。同じ idempotency_key のジョブがあれば、そのジョブの ID を返す。"""
    if kind not in _REGISTRY:
        raise ValueError(f"未登録のジョブの種類: {kind}")
    cur = await conn.execute(
        """
        INSERT INTO jobs (kind, via, params, server_id, requested_by, idempotency_key, run_after)
        VALUES (%s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        (kind, via, Jsonb(params or {}), server_id, requested_by, idempotency_key, delay_seconds),
    )
    row = await cur.fetchone()
    if row:
        return _first(row)
    cur = await conn.execute("SELECT id FROM jobs WHERE idempotency_key = %s", (idempotency_key,))
    return _first(await cur.fetchone())


def _first(row: Any) -> Any:
    return row["id"] if isinstance(row, dict) else row[0]


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
class Engine:
    def __init__(
        self,
        deps: Any = None,
        *,
        worker_id: str | None = None,
        retry_delays: Sequence[float] = RETRY_DELAYS,
        stale_after: float = STALE_AFTER_SECONDS,
        heartbeat: float = HEARTBEAT_SECONDS,
        on_progress: Callable[[int], Awaitable[None]] | None = None,
    ):
        self.deps = deps
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.retry_delays = tuple(retry_delays)
        self.stale_after = stale_after
        self.heartbeat = heartbeat
        self.on_progress = on_progress

    # ---- 取り出し ----
    async def claim(self) -> dict[str, Any] | None:
        async with db.transaction() as conn:
            cur = await conn.execute(
                """
                UPDATE jobs SET status = 'running', locked_by = %s, locked_at = now(), attempts = attempts + 1
                WHERE id = (
                  SELECT id FROM jobs
                  WHERE (status = 'queued' AND run_after <= now())
                     OR (status = 'running' AND locked_at < now() - make_interval(secs => %s))
                  ORDER BY id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
                )
                RETURNING id, kind, params, server_id::text, requested_by::text, via, attempts
                """,
                (self.worker_id, self.stale_after),
            )
            return await cur.fetchone()

    async def run_once(self) -> int | None:
        """1件取り出して最後まで実行する。取り出せなければ None。"""
        job = await self.claim()
        if job is None:
            return None
        await self.execute(job)
        return job["id"]

    async def run_forever(self, stop: asyncio.Event, *, concurrency: int = 4, idle_sleep: float = 1.0) -> None:
        sem = asyncio.Semaphore(concurrency)
        tasks: set[asyncio.Task[None]] = set()

        async def one(job: dict[str, Any]) -> None:
            try:
                await self.execute(job)
            finally:
                sem.release()

        while not stop.is_set():
            await sem.acquire()
            try:
                job = await self.claim()
            except Exception:
                sem.release()
                log.exception("ジョブの取り出しに失敗")
                await asyncio.sleep(idle_sleep)
                continue
            if job is None:
                sem.release()
                try:
                    await asyncio.wait_for(stop.wait(), idle_sleep)
                except TimeoutError:
                    pass
                continue
            t = asyncio.create_task(one(job))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 1件の実行 ----
    async def execute(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        async with db.pool().connection() as lock_conn:
            await lock_conn.set_autocommit(True)
            if job["server_id"] and not await self._try_server_lock(lock_conn, job["server_id"]):
                await self._requeue_busy(job_id)
                return
            hb = asyncio.create_task(self._heartbeat(job_id))
            try:
                await self._run_steps(job)
            except Exception:  # 想定外（DB 障害など）。ジョブは locked_at が古くなれば再開される
                log.exception("ジョブの実行中に想定外のエラー", extra=bind(job_id=job_id))
            finally:
                hb.cancel()
                if job["server_id"]:
                    await lock_conn.execute(
                        "SELECT pg_advisory_unlock(%s, hashtext(%s))", (LOCK_NAMESPACE, job["server_id"])
                    )
                await lock_conn.set_autocommit(False)

    async def _try_server_lock(self, conn: AsyncConnection, server_id: str) -> bool:
        cur = await conn.execute("SELECT pg_try_advisory_lock(%s, hashtext(%s))", (LOCK_NAMESPACE, server_id))
        row = await cur.fetchone()
        return bool(row["pg_try_advisory_lock"] if isinstance(row, dict) else row[0])

    async def _requeue_busy(self, job_id: int) -> None:
        async with db.transaction() as conn:
            await conn.execute(
                """UPDATE jobs SET status = 'queued', locked_by = NULL, locked_at = NULL,
                          run_after = now() + make_interval(secs => %s), attempts = attempts - 1
                   WHERE id = %s AND locked_by = %s""",
                (BUSY_RETRY_SECONDS, job_id, self.worker_id),
            )

    async def _heartbeat(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(self.heartbeat)
            try:
                async with db.transaction() as conn:
                    await conn.execute(
                        "UPDATE jobs SET locked_at = now() WHERE id = %s AND locked_by = %s", (job_id, self.worker_id)
                    )
            except Exception:
                log.warning("ハートビートの更新に失敗", extra=bind(job_id=job_id))

    async def _run_steps(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        ctx = JobContext(
            job_id=job_id,
            kind=job["kind"],
            params=job["params"] or {},
            server_id=job["server_id"],
            requested_by=job["requested_by"],
            via=job["via"],
            deps=self.deps,
        )
        factory = _REGISTRY.get(job["kind"])
        if factory is None:
            await self._finish(job_id, "failed", f"未登録のジョブの種類です（{job['kind']}）")
            return
        steps = factory(ctx)
        keys = [s.key for s in steps]
        if len(set(keys)) != len(keys) or not all(keys):
            await self._finish(job_id, "failed", "手順の key が重複しているか空です（実装の誤り）")
            return

        state = await self._load_steps(job_id, steps)
        # 前回の実行で取り消しの途中だった場合は、取り消しから続ける
        if any(r["status"] in ("failed", "undone") for r in state.values()):
            failed_seq = min((seq for seq, r in state.items() if r["status"] == "failed"), default=None)
            await self._rollback(ctx, steps, state, failed_seq, "前回の実行で失敗したため、取り消しを続けます")
            return

        for seq, step in enumerate(steps, start=1):
            row = state[seq]
            if row["status"] in ("done", "skipped"):
                if row["status"] == "done":
                    ctx.results[step.key] = row["undo"] or {}
                continue
            reason = step.skip(ctx)
            if reason:
                await self._set_step(job_id, seq, "skipped", note=reason, current=step.name)
                state[seq]["status"] = "skipped"
                continue
            await self._set_step(job_id, seq, "running", current=step.name)
            log.info("手順を開始: %s", step.name, extra=bind(job_id=job_id, step=step.key))
            try:
                saved = await self._with_retry(lambda s=step: s.run(ctx), step.timeout)
            except Exception as exc:
                msg = describe(step, exc)
                log.warning("手順が失敗: %s", msg, extra=bind(job_id=job_id, step=step.key))
                await self._set_step(job_id, seq, "failed", error=msg)
                state[seq]["status"] = "failed"
                await self._rollback(ctx, steps, state, seq, msg)
                return
            saved = saved or {}
            ctx.results[step.key] = saved
            await self._set_step(job_id, seq, "done", undo=saved)
            state[seq].update(status="done", undo=saved)
        await self._finish(job_id, "succeeded", None)

    async def _rollback(
        self, ctx: JobContext, steps: list[Step], state: dict[int, dict[str, Any]], failed_seq: int | None, reason: str
    ) -> None:
        job_id = ctx.job_id
        undo_errors: list[str] = []
        order = sorted(state, reverse=True)
        for seq in order:
            row = state[seq]
            step = steps[seq - 1]
            if row["status"] == "done":
                saved: dict[str, Any] | None = row["undo"] or {}
            elif row["status"] == "failed" and seq == failed_seq:
                saved = None  # 途中まで作ったものがあるかもしれない
            else:
                continue
            await self._set_step(job_id, seq, row["status"], current=f"取り消し：{step.name}")
            try:
                await self._with_retry(lambda s=step, sv=saved: s.undo(ctx, sv), step.timeout)
            except Exception as exc:
                msg = describe(step, exc, undo=True)
                undo_errors.append(msg)
                log.error("取り消しに失敗: %s", msg, extra=bind(job_id=job_id, step=step.key))
                if row["status"] == "done":
                    await self._set_step(job_id, seq, "done", error=msg)
                continue
            if row["status"] == "done":
                await self._set_step(job_id, seq, "undone")
                row["status"] = "undone"
        if undo_errors:
            await self._finish(job_id, "failed", f"{reason}。取り消しにも失敗しました：" + " / ".join(undo_errors))
        else:
            await self._finish(job_id, "rolled_back", reason)

    async def _with_retry(self, fn: Callable[[], Awaitable[Any]], limit: float) -> Any:
        attempt = 0
        while True:
            try:
                return await asyncio.wait_for(fn(), limit)
            except (TransientError, TimeoutError):
                if attempt >= len(self.retry_delays):
                    raise
                await asyncio.sleep(self.retry_delays[attempt])
                attempt += 1

    # ---- DB への記録 ----
    async def _load_steps(self, job_id: int, steps: list[Step]) -> dict[int, dict[str, Any]]:
        async with db.transaction() as conn:
            for seq, s in enumerate(steps, start=1):
                await conn.execute(
                    "INSERT INTO job_steps (job_id, seq, name, status) VALUES (%s, %s, %s, 'pending') "
                    "ON CONFLICT (job_id, seq) DO NOTHING",
                    (job_id, seq, s.name),
                )
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT seq, status, undo FROM job_steps WHERE job_id = %s ORDER BY seq", (job_id,))
            return {r["seq"]: dict(r) for r in await cur.fetchall()}

    async def _set_step(
        self,
        job_id: int,
        seq: int,
        status: str,
        *,
        undo: dict[str, Any] | None = None,
        error: str | None = None,
        note: str | None = None,
        current: str | None = None,
    ) -> None:
        async with db.transaction() as conn:
            await conn.execute(
                """
                UPDATE job_steps SET status = %s,
                  undo = COALESCE(%s, undo),
                  error = COALESCE(%s, error),
                  note = COALESCE(%s, note),
                  started_at = CASE WHEN %s = 'running' THEN now() ELSE started_at END,
                  finished_at = CASE WHEN %s IN ('done','failed','skipped','undone') THEN now() ELSE finished_at END
                WHERE job_id = %s AND seq = %s
                """,
                (status, Jsonb(undo) if undo is not None else None, error, note, status, status, job_id, seq),
            )
            if current is not None:
                await conn.execute(
                    "UPDATE jobs SET current_step = %s, locked_at = now() WHERE id = %s", (current, job_id)
                )
        if self.on_progress:
            try:
                await self.on_progress(job_id)
            except Exception:
                log.warning("進捗の通知に失敗", extra=bind(job_id=job_id))

    async def _finish(self, job_id: int, status: str, error: str | None) -> None:
        async with db.transaction() as conn:
            await conn.execute(
                """UPDATE jobs SET status = %s, error = %s, finished_at = now(), current_step = NULL,
                          locked_by = NULL, locked_at = NULL WHERE id = %s""",
                (status, error, job_id),
            )
        log.info("ジョブ終了: %s", status, extra=bind(job_id=job_id))
        if self.on_progress:
            try:
                await self.on_progress(job_id)
            except Exception:
                pass


def describe(step: Step, exc: BaseException, *, undo: bool = False) -> str:
    """利用者に見せる失敗の説明（日本語・秘密はマスク・長さを制限）。"""
    what = f"「{step.name}」の取り消し" if undo else f"「{step.name}」"
    if isinstance(exc, AppError):
        detail = exc.message
    elif isinstance(exc, TimeoutError):
        detail = "時間内に終わりませんでした"
    elif isinstance(exc, TransientError):
        detail = f"{exc.service} が一時的に応答しません"
    else:
        detail = f"{type(exc).__name__}: {exc}"
    return mask(f"{what}で失敗しました（{detail}）")[:500]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
