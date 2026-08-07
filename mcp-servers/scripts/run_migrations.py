"""mcp-servers 自有表迁移（幂等）。

用法：
  .venv/bin/python scripts/run_migrations.py

- PG：执行 migrations/001_init.sql（IF NOT EXISTS + 向量列 ALTER）。
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
        sql_path = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"
        with engine.begin() as conn:
            conn.execute(__import__("sqlalchemy").text(sql_path.read_text(encoding="utf-8")))
        print(f"[migrate] PG schema applied from {sql_path}")
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
