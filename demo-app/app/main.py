"""小智客服 —— 被治理的演示应用（FastAPI RAG）。

- POST /chat：客服对话（真实 StepFun，无 mock）。
- /v2/*：Quality API v2 全端点。
- /admin/*：B1–B4 故障注入（x-internal）。
- /feedback：用户反馈。

错误响应统一为契约 Error 结构：{"error": {code, message, details?, trace_id}}。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.routers import admin, chat, feedback, quality
from app.seeding import init_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app()  # 建表 + 种子 prompt/KB + 基线 VersionSet（幂等）
    yield


app = FastAPI(
    title="小智客服 Quality API",
    version="2.0.0",
    summary="CaseLoop 被治理演示应用：3C 数码电商客服（FastAPI RAG + Quality API v2 + B1–B4 注入）",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException 的 detail 直接作为响应体（契约 Error 结构，不外包 detail 键）。"""
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


app.include_router(quality.oauth_router)
app.include_router(quality.router)
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(admin.router)


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "service": "xiaozhi-customer-service"}
