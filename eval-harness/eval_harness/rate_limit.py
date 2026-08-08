"""集中限速（D-001：8 RPM 上限，留 2 余量给 AgentTeams worker）+ 429 指数退避。

- RateLimiter：**滑动窗口**（60s 内最多 rpm 次，deque 时间戳 + 锁），acquire() 阻塞到有空位。
  与 demo-app 的滑动窗口口径一致，避免令牌桶突发打爆上游（StepFun RPM=10）。
- 429 退避：服务端限流时按指数退避（1s/2s/4s/…，默认 ≤4 次）重试。
"""
from __future__ import annotations

import itertools
import random
import threading
import time
from collections import deque

DEFAULT_BACKOFF_BASE_S = 1.0
DEFAULT_MAX_RETRIES = 4
DEFAULT_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
WINDOW_S = 60.0


class RateLimiter:
    def __init__(self, rpm: int):
        if rpm < 1:
            raise ValueError("rpm 必须 ≥1")
        self.max_per_minute = int(rpm)
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> None:
        """阻塞直到滑动窗口出现空位。超时抛 RuntimeError。"""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= WINDOW_S:
                    self._times.popleft()
                if len(self._times) < self.max_per_minute:
                    self._times.append(now)
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"rate limit {self.max_per_minute}/min 等待超时")
            time.sleep(0.5)

    @property
    def rpm(self) -> int:
        return self.max_per_minute


class RateLimitError(RuntimeError):
    """重试耗尽后仍被限流。"""


def retry_with_backoff(
    fn,
    *,
    retries: int = DEFAULT_MAX_RETRIES,
    base: float = DEFAULT_BACKOFF_BASE_S,
    retryable_status: set[int] = DEFAULT_RETRYABLE_STATUS,
    deadline_monotonic: float | None = None,
):
    """对返回 requests.Response 的调用做指数退避重试。

    fn 应为无参 callable（用 partial/closble 绑定参数）。429/5xx 按指数退避重试；
    耗尽后抛 RateLimitError。非 retryable 状态码直接返回响应。
    """
    for attempt in itertools.count():
        _remaining(deadline_monotonic)
        resp = fn()
        if resp.status_code not in retryable_status or attempt >= retries:
            return resp
        sleep_s = base * (2 ** attempt) + random.uniform(0, 0.25)
        time.sleep(min(sleep_s, _remaining(deadline_monotonic)))
    # unreachable


def _remaining(deadline_monotonic: float | None) -> float:
    if deadline_monotonic is None:
        return 60.0
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("HTTP retry deadline exceeded")
    return remaining
