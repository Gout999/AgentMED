# AgentMED control-plane 容器镜像
# 确定性控制面（Case/Release Controller）：alembic 迁移 + uvicorn 服务。
# 仅供 compose 内网部署（宿主映射 18090 -> 8090）；非 root 运行。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv

WORKDIR /srv

# 先装钉版本依赖（利用层缓存）
COPY control-plane/requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# 再拷代码与 alembic 迁移（script_location 相对 WORKDIR）
COPY control-plane/app ./app
COPY control-plane/alembic ./alembic
COPY control-plane/alembic.ini .
COPY contracts ./contracts

# 非 root：appuser 拥有运行目录（审计导出物 var/ 可写）
RUN useradd --system --uid 10001 appuser \
    && mkdir -p /srv/var \
    && chown -R appuser:appuser /srv

# 入口：先 alembic upgrade head（幂等），再 exec uvicorn
COPY control-plane/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER appuser

EXPOSE 8090

ENTRYPOINT ["docker-entrypoint.sh"]
