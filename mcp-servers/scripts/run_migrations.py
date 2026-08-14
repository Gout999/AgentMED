"""mcp-servers 自有表迁移（幂等）。

用法：
  .venv/bin/python scripts/run_migrations.py

- PG：按文件名顺序执行 migrations/*.sql（全部迁移必须幂等）。
- SQLite：走 ORM create_all（向量列跳过，仅单测用）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import get_settings
from common.db import create_all, get_engine


def run() -> None:
    settings = get_settings()
    url = settings.database_url
    engine = get_engine(url)
    if engine.dialect.name == "postgresql":
        migration_dir = Path(__file__).resolve().parent.parent / "migrations"
        paths = sorted(migration_dir.glob("*.sql"))
        for sql_path in paths:
            sql = sql_path.read_text(encoding="utf-8")
            with engine.begin() as conn:
                try:
                    conn.execute(__import__("sqlalchemy").text(sql))
                except Exception as exc:  # noqa: BLE001 - demo-tolerant vector skip
                    if sql_path.name == "002_casebase_vector.sql":
                        print(
                            "[migrate] warn: casebase vector skipped "
                            f"(pgvector extension unavailable): {exc}"
                        )
                        continue
                    raise
        print(f"[migrate] PG schema applied ({len(paths)} files from {migration_dir})")
    else:
        create_all(url)
        print(f"[migrate] SQLite tables created ({url})")

    # 通知日志库 + 案例库（独立 URL 时同样建表）
    notify_url = settings.resolved_notification_url
    if notify_url != url:
        create_all(notify_url)
        print(f"[migrate] notification log tables created ({notify_url})")
    cb_url = settings.resolved_casebase_url
    if cb_url != url:
        create_all(cb_url)
        print(f"[migrate] casebase tables created ({cb_url})")


if __name__ == "__main__":
    run()
