"""LLM 调用封装（StepFun，OpenAI 兼容）：temperature=0、集中限速、429 退避、记录模型 digest。

纪律（T1 / D-001）：
- 演示确定性靠 temperature=0 + 冻结探针集；
- 所有 LLM 调用走共享 RateLimiter（默认 8 RPM）；
- 429 必须指数退避，不把限流当功能失败；
- 每次调用记录 model_digest = sha256(JCS({provider, model, params}))。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from openai import APIConnectionError, APITimeoutError, APIError, OpenAI, RateLimitError

from .config import Settings
from .digests import sha256_digest
from .rate_limit import RateLimiter

MAX_RETRIES = 4
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 16.0


@dataclass
class LLMResponse:
    content: str
    model: str
    model_digest: str
    usage: dict


class LLMClient:
    """StepFun 直连客户端（供裁判轨 / 变异巡检等 eval-harness 自有 LLM 调用）。

    与 demo-app 的 /chat 不同：这里直接构造 system+user 消息调用 LLM，不走 RAG。
    """

    def __init__(self, settings: Settings, limiter: RateLimiter | None = None):
        self.settings = settings
        self.limiter = limiter or RateLimiter(settings.llm_rpm_limit)
        self._client = OpenAI(
            api_key=settings.stepfun_api_key,
            base_url=settings.stepfun_base_url,
            timeout=settings.provider_timeout_seconds,
            max_retries=0,
        )

    def model_digest_for(self, model: str | None = None, params: dict | None = None) -> str:
        m = model or self.settings.stepfun_model
        p = dict(params or {"temperature": 0.0, "max_tokens": 1024})
        return sha256_digest({"provider": "stepfun", "model": m, "params": p})

    def chat(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        params: dict | None = None,
        deadline_monotonic: float | None = None,
    ) -> LLMResponse:
        """单次 chat 调用（temperature=0，集中限速 + 429 指数退避）。"""
        m = model or self.settings.stepfun_model
        p = dict(params or {"temperature": 0.0, "max_tokens": 1024})
        p.setdefault("temperature", 0.0)

        kwargs = dict(
            model=m,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if "temperature" in p:
            kwargs["temperature"] = float(p["temperature"])
        if "max_tokens" in p:
            kwargs["max_tokens"] = int(p["max_tokens"])
        if "top_p" in p and p.get("top_p") is not None:
            kwargs["top_p"] = float(p["top_p"])

        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES + 1):
            remaining = self._remaining(deadline_monotonic)
            self.limiter.acquire(timeout=min(60.0, remaining))
            try:
                client = self._client.with_options(
                    timeout=min(float(self.settings.provider_timeout_seconds), self._remaining(deadline_monotonic))
                )
                resp = client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                return LLMResponse(
                    content=content,
                    model=m,
                    model_digest=self.model_digest_for(m, p),
                    usage={
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    },
                )
            except Exception as exc:  # noqa: BLE001 —— 分类可重试/不可重试
                last_exc = exc
                if not self._retryable(exc):
                    raise
                backoff = min(BACKOFF_BASE_S * (2**attempt), BACKOFF_MAX_S)
                time.sleep(min(backoff, self._remaining(deadline_monotonic)))
        raise RuntimeError(f"LLM 重试 {MAX_RETRIES} 次仍失败: {last_exc}")

    @staticmethod
    def _remaining(deadline_monotonic: float | None) -> float:
        if deadline_monotonic is None:
            return 60.0
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("gate evaluator deadline exceeded during judge-provider call")
        return remaining

    @staticmethod
    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        if isinstance(exc, APIError):
            return bool(exc.status_code and exc.status_code >= 500)
        return False
