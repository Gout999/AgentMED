"""POST /chat —— 客服对话（RAG + 真实 StepFun 调用）。

- 检索：live KB 全文+元数据过滤 top-k。
- prompt：live config 的 prompt（P0 基线；B1/B4 注入后切 P1/P4）+ 检索上下文。
- LLM：live model params（基线 temperature=0；B3 注入后 1.2）。
- 每次请求落 /logs（digest 绑定 versionset），供治理层归因。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.ids import new_request_id
from app.llm import LLMError, chat_completion
from app.live_config import resolve_live_config
from app.models import ChatLog
from app.retrieval import search_kb
from app.schemas import ChatRequest
from app.tracing import current_trace_id, get_tracer

router = APIRouter(tags=["chat"])


def _format_kb_context(hits) -> str:
    lines = ["## 知识库资料"]
    for i, h in enumerate(hits, start=1):
        lines.append(f"[{i}] {h.title}（{h.kb_id}/{h.entry_id}）")
        lines.append(h.content)
    return "\n".join(lines)


@router.post("/chat")
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    tracer = get_tracer()
    request_id = new_request_id()
    trace_id = current_trace_id()

    with tracer.start_as_current_span("chat.request") as span:
        live = resolve_live_config(db)
        span.set_attribute("app.versionset_id", live.versionset_id)
        span.set_attribute("app.prompt_digest", live.prompt.digest)
        span.set_attribute("app.kb_manifest_digest", live.kb_manifest_digest)
        span.set_attribute("app.model_digest", live.model.digest)

        # 检索（Phase 1：全文+元数据过滤；向量检索 Phase 2）
        with tracer.start_as_current_span("kb.retrieval") as rspan:
            result = search_kb(live.entries, payload.message, top_k=settings.retrieval_top_k)
            rspan.set_attribute("kb.hits", len(result.hits))
            rspan.set_attribute("kb.filter", str(result.filter_applied))

        # 组装 RAG prompt
        context = _format_kb_context(result.hits)
        system_prompt = live.prompt.content + "\n\n" + context

        latency_start = time.monotonic()
        status = "ok"
        usage: dict = {}
        answer: str
        try:
            with tracer.start_as_current_span("llm.stepfun.chat") as lspan:
                lspan.set_attribute("llm.model", live.model.model)
                lspan.set_attribute("llm.temperature", float(live.model.params.get("temperature", 0.0)))
                resp = chat_completion(
                    system_prompt,
                    payload.message,
                    live.model.params,
                    model=live.model.model,
                )
                lspan.set_attribute("llm.status", "ok")
                answer = resp["content"]
                usage = resp["usage"]
        except LLMError as exc:
            status = "provider_error"
            answer = "抱歉，服务暂时不可用，请稍后再试或联系人工客服。"
            span.set_attribute("app.error", str(exc))

        latency_ms = int((time.monotonic() - latency_start) * 1000)

        # 落 /logs（digest 绑定）
        db.add(
            ChatLog(
                request_id=request_id,
                versionset_id=live.versionset_id or None,
                prompt_digest=live.prompt.digest,
                kb_manifest_digest=live.kb_manifest_digest,
                model_digest=live.model.digest,
                status=status,
                latency_ms=latency_ms,
                usage=usage or None,
                trace_id=trace_id,
            )
        )
        db.commit()

        return {
            "request_id": request_id,
            "answer": answer,
            "versionset_id": live.versionset_id,
            "prompt_digest": live.prompt.digest,
            "kb_manifest_digest": live.kb_manifest_digest,
            "model_digest": live.model.digest,
            "retrieval": [
                {"entry_id": h.entry_id, "kb_id": h.kb_id, "title": h.title, "score": h.score}
                for h in result.hits
            ],
        }
