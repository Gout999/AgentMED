"""限速器单元测试：滑动窗口在 RPM 内放行，超出阻塞至超时。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from app.rate_limit import RateLimitExceeded, SlidingWindowRateLimiter


def test_within_limit():
    limiter = SlidingWindowRateLimiter(max_per_minute=3)
    for _ in range(3):
        limiter.acquire(timeout=1.0)  # 不应抛
    assert limiter.remaining_slots() == 0


def test_exceed_blocks_then_timeout():
    limiter = SlidingWindowRateLimiter(max_per_minute=2)
    limiter.acquire(timeout=1.0)
    limiter.acquire(timeout=1.0)
    with pytest.raises(RateLimitExceeded):
        limiter.acquire(timeout=0.5)


def test_reset():
    limiter = SlidingWindowRateLimiter(max_per_minute=1)
    limiter.acquire(timeout=1.0)
    limiter.reset()
    limiter.acquire(timeout=1.0)  # 重置后应放行
