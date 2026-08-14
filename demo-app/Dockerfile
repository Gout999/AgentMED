# demo-app「小智客服」容器镜像
# 真实调用 StepFun LLM（无 mock）；Quality API v2 + B1–B4 注入端点
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 再拷代码、迁移与资产（prompt 模板 git 版本化 + KB 种子）
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY scripts/verify_schema_adoption.py ./scripts/verify_schema_adoption.py
COPY prompts ./prompts
COPY seeds ./seeds
RUN chmod +x /srv/docker-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/srv/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
