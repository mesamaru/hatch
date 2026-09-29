"""db/migrations/*.sql を番号順に適用する。

ルール:
  - 適用済みのファイルは絶対に書き換えない（新しい番号のファイルを追加する）
  - 1つ前のバージョンのコードでも動く「追加のみ」の変更にする（列の削除・改名は2リリースに分ける）
    → update --rollback で DB を戻さずにコードだけ戻せるようにするため
"""

import os
import sys

import psycopg

from . import BASE_DIR

LOCK_ID = 7_420_117  # 同時実行を防ぐアドバイザリーロック


def main(quiet: bool = False) -> int:
    mig_dir = BASE_DIR / "db" / "migrations"
    files = sorted(p for p in mig_dir.glob("*.sql"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for f in files:
                if f.stem in done:
                    continue
                if not quiet:
                    print(f"  適用: {f.name}")
                with conn.transaction():
                    conn.execute(f.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (f.stem,))
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return 0


if __name__ == "__main__":
    sys.exit(main())
