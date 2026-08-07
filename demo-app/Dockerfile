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

# 再拷代码与资产（prompt 模板 git 版本化 + KB 种子）
COPY app ./app
COPY prompts ./prompts
COPY seeds ./seeds

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
