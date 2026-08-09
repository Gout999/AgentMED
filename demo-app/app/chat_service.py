"""Shared real RAG/StepFun execution for live chat and exact-candidate evaluation."""
from __future__ import annotations

import hashlib
import time

from sqlalchemy.orm import Session

from app.config import get_settings
from app.ids import new_request_id
from app.llm import LLMError, chat_completion
from app.live_config import LiveConfig
from app.models import ChatLog
from app.retrieval import search_kb
from app.schemas import ChatRequest
from app.tracing import current_trace_id, get_tracer


def _format_kb_context(hits) -> str:
    lines = ["## 知识库资料"]
    for i, hit in enumerate(hits, start=1):
        lines.append(f"[{i}] {hit.title}（{hit.kb_id}/{hit.entry_id}）")
        lines.append(hit.content)
    return "\n".join(lines)


def execute_chat(payload: ChatRequest, db: Session, config: LiveConfig, *, span_name: str) -> dict:
    """Execute one provider-backed answer and persist its exact VersionSet digests."""

    settings = get_settings()
    tracer = get_tracer()
    request_id = new_request_id()
    trace_id = current_trace_id()

    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("app.versionset_id", config.versionset_id)
        span.set_attribute("app.prompt_digest", config.prompt.digest)
        span.set_attribute("app.kb_manifest_digest", config.kb_manifest_digest)
        span.set_attribute("app.model_digest", config.model.digest)

        with tracer.start_as_current_span("kb.retrieval") as retrieval_span:
            result = search_kb(config.entries, payload.message, top_k=settings.retrieval_top_k)
            retrieval_span.set_attribute("kb.hits", len(result.hits))
            retrieval_span.set_attribute("kb.filter", str(result.filter_applied))

        context = _format_kb_context(result.hits)
        system_prompt = config.prompt.content + "\n\n" + context

        latency_start = time.monotonic()
        status = "ok"
        usage: dict = {}
        try:
            with tracer.start_as_current_span("llm.stepfun.chat") as llm_span:
                llm_span.set_attribute("llm.model", config.model.model)
                llm_span.set_attribute(
                    "llm.temperature", float(config.model.params.get("temperature", 0.0))
                )
                response = chat_completion(
                    system_prompt,
                    payload.message,
                    config.model.params,
                    model=config.model.model,
                )
                llm_span.set_attribute("llm.status", "ok")
                answer = response["content"]
                usage = response["usage"]
        except LLMError as exc:
            status = "provider_error"
            answer = "抱歉，服务暂时不可用，请稍后再试或联系人工客服。"
            span.set_attribute("app.error", str(exc))

        latency_ms = int((time.monotonic() - latency_start) * 1000)
        answer_digest = "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest()
        provider_origin = settings.stepfun_base_url.rstrip("/")
        persisted_usage = dict(usage or {})
        # ChatLog intentionally keeps its existing schema.  The internal key is
        # exposed as a first-class immutable provenance field by log_entry_dict.
        persisted_usage["_answer_digest"] = answer_digest
        persisted_usage["_provider_origin"] = provider_origin
        db.add(
            ChatLog(
                request_id=request_id,
                versionset_id=config.versionset_id or None,
                prompt_digest=config.prompt.digest,
                kb_manifest_digest=config.kb_manifest_digest,
                model_digest=config.model.digest,
                status=status,
                latency_ms=latency_ms,
                usage=persisted_usage,
                trace_id=trace_id,
            )
        )
        db.commit()

        return {
            "request_id": request_id,
            "answer": answer,
            "answer_digest": answer_digest,
            "provider_origin": provider_origin,
            "status": status,
            "versionset_id": config.versionset_id,
            "prompt_digest": config.prompt.digest,
            "kb_manifest_digest": config.kb_manifest_digest,
            "model_digest": config.model.digest,
            "trace_id": trace_id,
            "retrieval": [
                {
                    "entry_id": hit.entry_id,
                    "kb_id": hit.kb_id,
                    "title": hit.title,
                    "score": hit.score,
                }
                for hit in result.hits
            ],
        }
