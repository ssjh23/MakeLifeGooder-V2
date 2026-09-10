"""Rate limiting behind an interface.

ADR-010 declined Redis at launch. Two of its three stated uses evaporated once
the other decisions were made: procrastinate put the queue in Postgres, and
signed cookies put sessions nowhere. The third, dashboard caching, would cache
an aggregate that is already precomputed while adding an invalidation problem
on every reclassification.

Rate limiting is the one case that does not have a Postgres answer. It is
per-request counter writes on a hot row, which is the workload Postgres handles
worst, and no existing table structure solves it.

So: an interface now, an in-process backend now, Redis when the app is exposed
publicly. The method is async from the first line even though the in-memory
implementation never awaits, because that is what makes the swap touch no call
sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitVerdict:
    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window_seconds: int) -> RateLimitVerdict:
        """Count this request against ``key`` and say whether it may proceed.

        Called before any side effect. A rejected request must leave no row
        written and no job enqueued (TC-RATE-006).
        """
        ...
