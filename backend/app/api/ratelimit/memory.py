"""In-process fixed-window rate limiter.

Correct for one container, and that is the whole of its remit.

The known limitation is deliberate and documented rather than hidden: counters
live in this process, so two containers give twice the intended limit. That is
acceptable before public exposure and not after, which is precisely the trigger
ADR-010 names for introducing Redis. TC-RATE-007 asserts the doubling, so the
gap stays visible in the test output instead of living only in a document.

A fixed window rather than a sliding one, for the same reason: it is the
simplest thing that enforces the limit, and the burst it permits at a window
boundary does not matter at this scale.
"""

from __future__ import annotations

import asyncio
import time

from app.api.ratelimit.base import RateLimitVerdict


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._counters: dict[str, tuple[int, int]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window_seconds: int) -> RateLimitVerdict:
        now = int(time.time())
        window_start = now - (now % window_seconds)
        reset = window_start + window_seconds

        async with self._lock:
            recorded_window, count = self._counters.get(key, (window_start, 0))
            if recorded_window != window_start:
                count = 0
            count += 1
            self._counters[key] = (window_start, count)

            # Bounded cleanup. Without it, one key per user per window is a
            # slow leak in a long-running process.
            if len(self._counters) > 10_000:
                self._counters = {
                    k: v for k, v in self._counters.items() if v[0] == window_start
                }

        return RateLimitVerdict(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            reset_epoch=reset,
        )

    async def reset(self) -> None:
        """Clear all counters. Tests only."""
        async with self._lock:
            self._counters.clear()
