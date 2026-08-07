"""集中限速器：滑动窗口 8 RPM（D-001 裁决值，可配），跨请求串行节流。

设计：单进程内存滑动窗口 + 锁。acquire 在窗口满时阻塞等待空位，
防止并发请求一起打爆上游。429 指数退避在 llm.py 层做（此处只做发射节流）。
"""
from __future__ import annotations

import threading
import time
from collections import deque


class RateLimitExceeded(Exception):
    """等待超时仍无空位。"""


class SlidingWindowRateLimiter:
    def __init__(self, max_per_minute: int = 8):
        self.max_per_minute = max(1, int(max_per_minute))
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while True:
            with self._lock:
                now = time.time()
                # 滑窗：清理 60s 之前的记录
                while self._times and now - self._times[0] >= 60.0:
                    self._times.popleft()
                if len(self._times) < self.max_per_minute:
                    self._times.append(now)
                    return
            if time.time() >= deadline:
                raise RateLimitExceeded(
                    f"rate limit {self.max_per_minute}/min 等待超时"
                )
            time.sleep(0.5)

    def remaining_slots(self) -> int:
        with self._lock:
            now = time.time()
            while self._times and now - self._times[0] >= 60.0:
                self._times.popleft()
            return max(0, self.max_per_minute - len(self._times))

    def reset(self) -> None:
        with self._lock:
            self._times.clear()


# 全局实例（进程内唯一，所有请求共享）
_global_limiter: SlidingWindowRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter() -> SlidingWindowRateLimiter:
    global _global_limiter
    with _limiter_lock:
        if _global_limiter is None:
            # 延迟导入避免循环依赖
            from app.config import get_settings

            _global_limiter = SlidingWindowRateLimiter(get_settings().llm_rpm_limit)
        return _global_limiter
