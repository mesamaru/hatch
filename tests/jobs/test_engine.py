"""ジョブ実行基盤（docs/TASKS.md T08 の受け入れ条件）。"""

from __future__ import annotations

import asyncio
import uuid

import psycopg
import pytest

from hatch import db
from hatch.errors import AppError, TransientError
from hatch.jobs import engine as eng
from hatch.jobs.engine import Engine, JobContext, Step, enqueue, job_kind

CALLS: list[str] = []
BEHAVIOR: dict[str, object] = {}


class Rec(Step):
    def __init__(self, key: str):
        self.key = key
        self.name = f"手順{key}"

    def skip(self, ctx):
        return BEHAVIOR.get(f"skip:{self.key}")

    async def run(self, ctx: JobContext):
        CALLS.append(f"run:{self.key}")
        b = BEHAVIOR.get(f"run:{self.key}")
        if isinstance(b, list) and b:
            exc = b.pop(0)
            raise exc
        if isinstance(b, BaseException):
            raise b
        prev = {k: v.get("v") for k, v in ctx.results.items()}
        return {"v": self.key, "seen": prev}

    async def undo(self, ctx, saved):
        CALLS.append(f"undo:{self.key}:{'none' if saved is None else saved['v']}")
        b = BEHAVIOR.get(f"undo:{self.key}")
        if isinstance(b, BaseException):
            raise b


@job_kind("test_abc")
def _abc(ctx: JobContext):
    return [Rec("a"), Rec("b"), Rec("c"), Rec("d")]


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    BEHAVIOR.clear()


@pytest.fixture
async def pool(db_url):
    await db.open_pool(db_url, max_size=6)
    yield
    await db.close_pool()


def make_engine(**kw):
    return Engine(retry_delays=(0, 0, 0), heartbeat=3600, **kw)


async def new_job(server_id=None, key=None):
    async with db.transaction() as conn:
        return await enqueue(conn, "test_abc", via="system", params={"x": 1}, server_id=server_id, idempotency_key=key)


def q(db_url, sql, args=()):
    with psycopg.connect(db_url) as c:
        return c.execute(sql, args).fetchall()


async def test_all_steps_succeed(pool, db_url):
    jid = await new_job()
    assert await make_engine().run_once() == jid
    assert CALLS == ["run:a", "run:b", "run:c", "run:d"]
    assert q(db_url, "SELECT status, error FROM jobs WHERE id=%s", (jid,)) == [("succeeded", None)]
    steps = q(db_url, "SELECT seq, status, undo FROM job_steps WHERE job_id=%s ORDER BY seq", (jid,))
    assert [s[1] for s in steps] == ["done"] * 4
    # 後の手順は前の手順の結果を読める
    assert steps[2][2]["seen"] == {"a": "a", "b": "b"}


async def test_failure_rolls_back_in_reverse(pool, db_url):
    BEHAVIOR["run:c"] = AppError("x", "パネルが拒否しました")
    jid = await new_job()
    await make_engine().run_once()
    assert CALLS == ["run:a", "run:b", "run:c", "undo:c:none", "undo:b:b", "undo:a:a"]
    status, error = q(db_url, "SELECT status, error FROM jobs WHERE id=%s", (jid,))[0]
    assert status == "rolled_back"
    assert "手順c" in error and "パネルが拒否しました" in error
    st = [r[0] for r in q(db_url, "SELECT status FROM job_steps WHERE job_id=%s ORDER BY seq", (jid,))]
    assert st == ["undone", "undone", "failed", "pending"]


async def test_undo_failure_marks_failed(pool, db_url):
    BEHAVIOR["run:c"] = RuntimeError("boom")
    BEHAVIOR["undo:a"] = RuntimeError("cannot delete")
    jid = await new_job()
    await make_engine().run_once()
    status, error = q(db_url, "SELECT status, error FROM jobs WHERE id=%s", (jid,))[0]
    assert status == "failed"
    assert "取り消しにも失敗" in error and "cannot delete" in error
    # a の取り消しに失敗しても b の取り消しは実行されている
    assert "undo:b:b" in CALLS


async def test_transient_errors_are_retried(pool, db_url):
    BEHAVIOR["run:b"] = [TransientError("Cloudflare", "429"), TransientError("Cloudflare", "503")]
    jid = await new_job()
    await make_engine().run_once()
    assert CALLS.count("run:b") == 3
    assert q(db_url, "SELECT status FROM jobs WHERE id=%s", (jid,)) == [("succeeded",)]


async def test_transient_gives_up_after_retries(pool, db_url):
    BEHAVIOR["run:b"] = [TransientError("Cloudflare", "503")] * 5
    jid = await new_job()
    await make_engine().run_once()
    assert CALLS.count("run:b") == 4  # 1回 + 再試行3回
    assert q(db_url, "SELECT status FROM jobs WHERE id=%s", (jid,)) == [("rolled_back",)]


async def test_skip(pool, db_url):
    BEHAVIOR["skip:b"] = "作成済み"
    jid = await new_job()
    await make_engine().run_once()
    assert "run:b" not in CALLS
    assert q(db_url, "SELECT status, note FROM job_steps WHERE job_id=%s AND seq=2", (jid,)) == [
        ("skipped", "作成済み")
    ]


async def test_resume_after_worker_crash(pool, db_url):
    jid = await new_job()
    # 別のワーカーが a・b を終え、c の実行中に落ちた状態を作る
    with psycopg.connect(db_url) as c:
        c.execute(
            "UPDATE jobs SET status='running', locked_by='dead:1', locked_at=now()-interval '10 minutes', "
            "attempts=1 WHERE id=%s",
            (jid,),
        )
        for seq, name, st, undo in [
            (1, "手順a", "done", '{"v":"a"}'),
            (2, "手順b", "done", '{"v":"b"}'),
            (3, "手順c", "running", None),
            (4, "手順d", "pending", None),
        ]:
            c.execute(
                "INSERT INTO job_steps (job_id, seq, name, status, undo) VALUES (%s,%s,%s,%s,%s)",
                (jid, seq, name, st, undo),
            )
    assert await make_engine().run_once() == jid
    assert CALLS == ["run:c", "run:d"]
    assert q(db_url, "SELECT status, attempts FROM jobs WHERE id=%s", (jid,)) == [("succeeded", 2)]


async def test_fresh_running_job_is_not_stolen(pool, db_url):
    jid = await new_job()
    with psycopg.connect(db_url) as c:
        c.execute("UPDATE jobs SET status='running', locked_by='alive:1', locked_at=now() WHERE id=%s", (jid,))
    assert await make_engine().run_once() is None


async def test_resume_continues_interrupted_rollback(pool, db_url):
    jid = await new_job()
    with psycopg.connect(db_url) as c:
        c.execute(
            "UPDATE jobs SET status='running', locked_by='dead:1', locked_at=now()-interval '10 minutes' WHERE id=%s",
            (jid,),
        )
        for seq, st, undo in [
            (1, "done", '{"v":"a"}'),
            (2, "undone", '{"v":"b"}'),
            (3, "failed", None),
            (4, "pending", None),
        ]:
            c.execute(
                "INSERT INTO job_steps (job_id, seq, name, status, undo) VALUES (%s,%s,'x',%s,%s)", (jid, seq, st, undo)
            )
    await make_engine().run_once()
    assert CALLS == ["undo:c:none", "undo:a:a"]
    assert q(db_url, "SELECT status FROM jobs WHERE id=%s", (jid,)) == [("rolled_back",)]


async def test_same_server_runs_one_at_a_time(pool, db_url):
    # サーバーの行を用意（外部キー）
    sid = str(uuid.uuid4())
    with psycopg.connect(db_url) as c:
        uid = c.execute("INSERT INTO users (username, email) VALUES ('tanaka','t@x') RETURNING id").fetchone()[0]
        c.execute(
            "INSERT INTO servers (id, name, owner_id, plan_id, game, expires_at) "
            "VALUES (%s,'storia',%s,'light','paper', now()+interval '30 days')",
            (sid, uid),
        )
    j1 = await new_job(server_id=sid)
    j2 = await new_job(server_id=sid)
    started = asyncio.Event()
    release = asyncio.Event()

    class Slow(Step):
        key, name = "slow", "ゆっくり"

        async def run(self, ctx):
            started.set()
            await release.wait()
            return {}

    @job_kind("test_slow")
    def _slow(ctx):
        return [Slow()]

    with psycopg.connect(db_url) as c:
        c.execute("UPDATE jobs SET kind='test_slow' WHERE id=%s", (j1,))
    e1, e2 = make_engine(worker_id="w1"), make_engine(worker_id="w2")
    t1 = asyncio.create_task(e1.run_once())
    await asyncio.wait_for(started.wait(), 5)
    # j1 がロックを持っているので、j2 は取り出されても実行されずに待ちへ戻る
    assert await e2.run_once() == j2
    assert CALLS == []
    status, run_after_future = q(db_url, "SELECT status, run_after > now() FROM jobs WHERE id=%s", (j2,))[0]
    assert (status, run_after_future) == ("queued", True)
    release.set()
    await t1
    with psycopg.connect(db_url) as c:
        c.execute("UPDATE jobs SET run_after = now() WHERE id=%s", (j2,))
    assert await e2.run_once() == j2
    assert q(db_url, "SELECT status FROM jobs WHERE id=%s", (j2,)) == [("succeeded",)]


async def test_idempotency_key(pool, db_url):
    a = await new_job(key="discord:123")
    b = await new_job(key="discord:123")
    assert a == b
    assert q(db_url, "SELECT count(*) FROM jobs") == [(1,)]


async def test_unknown_kind_rejected(pool):
    async with db.transaction() as conn:
        with pytest.raises(ValueError):
            await enqueue(conn, "nope", via="system")


async def test_describe_masks_secrets():
    s = Rec("z")
    msg = eng.describe(s, RuntimeError("Authorization: Bearer abcdefghijklmnop"))
    assert "abcdefghijklmnop" not in msg


async def test_run_forever_stops(pool, db_url):
    await new_job()
    stop = asyncio.Event()
    e = make_engine()
    task = asyncio.create_task(e.run_forever(stop, idle_sleep=0.05))
    for _ in range(100):
        if q(db_url, "SELECT status FROM jobs")[0][0] == "succeeded":
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert q(db_url, "SELECT status FROM jobs") == [("succeeded",)]
