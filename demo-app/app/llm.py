"""StepFun LLM 客户端：真实调用（无 mock），集中限速 8RPM + 429 指数退避。

- 限速：每次发射前 acquire 全局滑动窗口（app/rate_limit）。
- 退避：429/超时/连接错误/5xx 按 base*2^n 退避重试，上限可配。
- 确定性：temperature 来自 live model params（基线 0.0；B3 注入后 1.2）。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    APIError,
    OpenAI,
    RateLimitError,
)

from app.config import get_settings
from app.rate_limit import get_limiter


class LLMError(Exception):
    def __init__(self, message: str, retries: int = 0, last_status: Optional[int] = None):
        self.retries = retries
        self.last_status = last_status
        super().__init__(message)


_client: Optional[OpenAI] = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        settings = get_settings()
        _client = OpenAI(
            base_url=settings.stepfun_base_url,
            api_key=settings.stepfun_api_key or "missing-stepfun-key",
            timeout=90.0,
            max_retries=0,  # 退避由本模块统一控制
        )
    return _client


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIError):
        # 5xx 服务端错误可重试；4xx 除 429 外不重试
        return bool(exc.status_code and exc.status_code >= 500)
    return False


def chat_completion(
    system_prompt: str,
    user_query: str,
    model_params: dict[str, Any],
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """返回 {content, usage, model}。model_params 由 live config 提供（B3 会改）。"""
    settings = get_settings()
    limiter = get_limiter()
    client = _openai()
    model_name = model or settings.stepfun_model

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
    }
    if "temperature" in model_params:
        kwargs["temperature"] = float(model_params["temperature"])
    if "max_tokens" in model_params:
        kwargs["max_tokens"] = int(model_params["max_tokens"])
    if "top_p" in model_params and model_params.get("top_p") is not None:
        kwargs["top_p"] = float(model_params["top_p"])
    if "seed" in model_params and model_params.get("seed") is not None:
        kwargs["seed"] = int(model_params["seed"])

    last_exc: Optional[BaseException] = None
    for attempt in range(settings.llm_retry_max_attempts):
        limiter.acquire()  # 集中限速：窗口满则阻塞等待空位
        try:
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            return {
                "content": content,
                "model": model_name,
                "usage": {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
                },
            }
        except Exception as exc:  # noqa: BLE001 —— 分类为可重试/不可重试
            last_exc = exc
            if not _retryable(exc):
                status = exc.status_code if isinstance(exc, APIError) else None
                raise LLMError(f"LLM 不可重试错误: {exc}", retries=attempt, last_status=status) from exc
            backoff = min(
                settings.llm_retry_backoff_base_seconds * (2**attempt),
                settings.llm_retry_backoff_max_seconds,
            )
            time.sleep(backoff)

    status = last_exc.status_code if isinstance(last_exc, APIError) else None
    raise LLMError(
        f"LLM 重试 {settings.llm_retry_max_attempts} 次仍失败: {last_exc}",
        retries=settings.llm_retry_max_attempts,
        last_status=status,
    )
