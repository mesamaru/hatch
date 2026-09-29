"""データベース接続。

- 非同期（API・ワーカー）: `await open_pool()` の後、`async with transaction() as conn:`
- 同期（移行・自己監視など小さな処理）: psycopg.connect を直接使ってよい

ルール:
  - SQL は pterodeploy/repo/ に置き、関数は必ず接続を引数で受け取る（トランザクションの範囲を呼び出し側が決める）
  - 外部サービスの呼び出しをトランザクションの中で待たない（ロックを長く握らないため）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import get_settings

_pool: AsyncConnectionPool | None = None


async def open_pool(dsn: str | None = None, *, min_size: int = 1, max_size: int = 10) -> AsyncConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    dsn = dsn or get_settings().DATABASE_URL.get_secret_value()
    _pool = AsyncConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=False,
        name="pterodeploy",
    )
    await _pool.open(wait=True, timeout=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("DB の接続プールが開かれていません（open_pool() を先に呼んでください）")
    return _pool


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """1つのトランザクション。ブロックを抜けるとコミット、例外ならロールバック。"""
    async with pool().connection() as conn, conn.transaction():
        yield conn


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """トランザクションを自分で管理したいとき（アドバイザリーロックを長く持つなど）。autocommit で渡す。"""
    async with pool().connection() as conn:
        await conn.set_autocommit(True)
        try:
            yield conn
        finally:
            await conn.set_autocommit(False)
