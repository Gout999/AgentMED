"""Quality API 客户端：/chat（客服对话）+ /admin（故障注入/复位）。

- /chat 无鉴权（demo-app 对治理层读面开放；如需 Bearer 走 quality:read）。
- /admin/inject、/admin/reset 需 quality:write 令牌。
- 全部请求走集中限速；429/5xx 指数退避。
- chat 响应含三个 digest（prompt/kb/model），实验执行器据此对账版本。
"""
from __future__ import annotations

from dataclasses import dataclass
import time

import requests

from .config import Settings
from .rate_limit import RateLimiter, retry_with_backoff


@dataclass
class ChatResult:
    request_id: str
    answer: str
    versionset_id: str | None
    prompt_digest: str | None
    kb_manifest_digest: str | None
    model_digest: str | None
    retrieval: list
    raw: dict
    status: str = "ok"
    trace_id: str | None = None


class QualityAPIClient:
    def __init__(self, settings: Settings, limiter: RateLimiter | None = None):
        self.settings = settings
        self.base = settings.quality_api_base_url.rstrip("/")
        self.limiter = limiter or RateLimiter(settings.llm_rpm_limit)
        self._read_session = requests.Session()
        self._read_session.headers.update({"Authorization": f"Bearer {settings.read_token}"})
        self._write_session = requests.Session()
        self._write_session.headers.update({"Authorization": f"Bearer {settings.write_token}"})

    # ---- 读：/chat ----
    def chat(self, message: str, session_id: str | None = None, user_ref: str | None = None) -> ChatResult:
        body = {"message": message}
        if session_id:
            body["session_id"] = session_id
        if user_ref:
            body["user_ref"] = user_ref

        resp = retry_with_backoff(
            lambda: self._post("/chat", body, self._read_session),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"/chat 失败 HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return self._chat_result(data)

    def evaluate_versionset(
        self,
        versionset_id: str,
        message: str,
        *,
        session_id: str | None = None,
        user_ref: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ChatResult:
        """Execute one probe against the exact immutable candidate VersionSet."""

        body = {"message": message}
        if session_id:
            body["session_id"] = session_id
        if user_ref:
            body["user_ref"] = user_ref
        path = f"/v2/versionsets/{versionset_id}/evaluate"
        timeout = float(timeout_seconds or self.settings.quality_api_timeout_seconds)
        if timeout <= 0:
            raise ValueError("HTTP timeout must be positive")
        deadline = time.monotonic() + timeout
        resp = retry_with_backoff(
            lambda: self._post(
                path,
                body,
                self._read_session,
                timeout_seconds=self._remaining(deadline),
            ),
            deadline_monotonic=deadline,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"{path} failed HTTP {resp.status_code}: {resp.text[:300]}")
        return self._chat_result(resp.json())

    @staticmethod
    def _chat_result(data: dict) -> ChatResult:
        return ChatResult(
            request_id=data.get("request_id", ""),
            answer=data.get("answer", ""),
            versionset_id=data.get("versionset_id"),
            prompt_digest=data.get("prompt_digest"),
            kb_manifest_digest=data.get("kb_manifest_digest"),
            model_digest=data.get("model_digest"),
            retrieval=data.get("retrieval", []),
            raw=data,
            status=data.get("status", "unknown"),
            trace_id=data.get("trace_id"),
        )

    def get_versionset(self, versionset_id: str, *, timeout_seconds: float | None = None) -> dict:
        """Read the exact candidate VersionSet without mutating lifecycle state."""

        timeout = float(timeout_seconds or self.settings.quality_api_timeout_seconds)
        if timeout <= 0:
            raise ValueError("HTTP timeout must be positive")
        deadline = time.monotonic() + timeout
        resp = retry_with_backoff(
            lambda: self._get(
                f"/v2/versionsets/{versionset_id}",
                self._read_session,
                timeout_seconds=self._remaining(deadline),
            ),
            deadline_monotonic=deadline,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"GET /v2/versionsets/{versionset_id} failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    def list_versionsets(self, *, status: str | None = None, limit: int = 50) -> dict:
        params = {"limit": limit}
        if status:
            params["status"] = status
        resp = retry_with_backoff(
            lambda: self._get("/v2/versionsets", self._read_session, **params),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GET /v2/versionsets failed HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ---- 写：/admin（注入/复位，扮演 Release Controller 演示身份）----
    def inject_fault(self, fault_id: str) -> dict:
        resp = retry_with_backoff(
            lambda: self._post(f"/admin/inject/{fault_id}", None, self._write_session),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"注入 {fault_id} 失败 HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def reset_faults(self) -> dict:
        resp = retry_with_backoff(
            lambda: self._post("/admin/reset", None, self._write_session),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"复位失败 HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ---- 底层 ----
    def _post(
        self,
        path: str,
        body: dict | None,
        session: requests.Session,
        *,
        timeout_seconds: float | None = None,
    ) -> requests.Response:
        timeout = float(timeout_seconds or self.settings.quality_api_timeout_seconds)
        if timeout <= 0:
            raise ValueError("HTTP timeout must be positive")
        self.limiter.acquire(timeout=min(60.0, timeout))
        return session.post(self.base + path, json=body, timeout=timeout)

    def _get(
        self,
        path: str,
        session: requests.Session,
        *,
        timeout_seconds: float | None = None,
        **params,
    ) -> requests.Response:
        timeout = float(timeout_seconds or self.settings.quality_api_timeout_seconds)
        if timeout <= 0:
            raise ValueError("HTTP timeout must be positive")
        self.limiter.acquire(timeout=min(60.0, timeout))
        return session.get(self.base + path, params=params, timeout=timeout)

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Quality API request deadline exceeded")
        return remaining
