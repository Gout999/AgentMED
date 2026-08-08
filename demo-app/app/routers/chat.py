"""POST /chat —— 客服对话（RAG + 真实 StepFun 调用）。

- 检索：live KB 全文+元数据过滤 top-k。
- prompt：live config 的 prompt（P0 基线；B1/B4 注入后切 P1/P4）+ 检索上下文。
- LLM：live model params（基线 temperature=0；B3 注入后 1.2）。
- 每次请求落 /logs（digest 绑定 versionset），供治理层归因。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.chat_service import execute_chat
from app.db import get_db
from app.live_config import resolve_live_config
from app.schemas import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
):
    return execute_chat(payload, db, resolve_live_config(db), span_name="chat.request")
