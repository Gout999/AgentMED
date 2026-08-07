#!/bin/sh
# control-plane 容器入口：先跑迁移，再起服务。
# 幂等：DB 已在 head 时 alembic upgrade 为 no-op（restart 不炸）。
set -e

echo "[control-plane] alembic upgrade head ..."
alembic upgrade head

echo "[control-plane] starting uvicorn on 0.0.0.0:8090"
exec uvicorn app.main:app --host 0.0.0.0 --port 8090
