"""Quality API 客户端：/chat（客服对话）+ /admin（故障注入/复位）。

- /chat 无鉴权（demo-app 对治理层读面开放；如需 Bearer 走 quality:read）。
- /admin/inject、/admin/reset 需 quality:write 令牌。
- 全部请求走集中限速；429/5xx 指数退避。
- chat 响应含三个 digest（prompt/kb/model），实验执行器据此对账版本。
"""
from __future__ import annotations

from dataclasses import dataclass

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


class QualityAPIClient:
    def __init__(self, settings: Settings, limiter: RateLimiter | None = None):
        self.settings = settings
        self.base = settings.quality_api_base_url.rstrip("/")
        self.limiter = limiter or RateLimiter(settings.llm_rpm_limit)
        # trust_env=False：本机内部调用禁止走系统/环境代理
        # （requests 默认 trust_env=True 经 urllib.getproxies() 读 macOS 系统代理，
        #  本地代理对 loopback 回 502——与 mcp-servers/common/http.py 同根因，S0-007）
        self._read_session = requests.Session()
        self._read_session.trust_env = False
        self._read_session.headers.update({"Authorization": f"Bearer {settings.read_token}"})
        self._write_session = requests.Session()
        self._write_session.trust_env = False
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
        return ChatResult(
            request_id=data.get("request_id", ""),
            answer=data.get("answer", ""),
            versionset_id=data.get("versionset_id"),
            prompt_digest=data.get("prompt_digest"),
            kb_manifest_digest=data.get("kb_manifest_digest"),
            model_digest=data.get("model_digest"),
            retrieval=data.get("retrieval", []),
            raw=data,
        )

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
    def _post(self, path: str, body: dict | None, session: requests.Session) -> requests.Response:
        self.limiter.acquire()
        return session.post(self.base + path, json=body)

    def _get(self, path: str, session: requests.Session, **params) -> requests.Response:
        self.limiter.acquire()
        return session.get(self.base + path, params=params)
