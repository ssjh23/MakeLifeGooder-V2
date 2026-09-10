"""Refreshing the precomputed monthly totals.

``category_monthly_totals`` is what the dashboard reads. No request aggregates
transactions, which is why the dashboard has a latency budget it can meet, and
also why Redis bought nothing at launch: caching a table that is already a
precomputed aggregate adds an invalidation problem without removing a query.

The cost of precomputing is that it can be wrong. Every path that moves money
between categories has to refresh, and one that forgets produces a dashboard
that disagrees with the transactions behind it, which is the exact failure the
product exists to prevent.

===========================================================================
BUILD STEP 7.1   depends on: 6.3 review finish   blocks: 7.2 dashboard, 8.2 reapply
Verify: uv run pytest tests/integration/test_aggregates.py
===========================================================================
Write the invariant test with it: recategorise a row, then assert the category
totals moved and the statement total did not. That one assertion covers the
product's central promise, and it belongs here rather than in the dashboard.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession


class AggregateRefresher:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def refresh_statement(self, statement_id: uuid.UUID) -> None:
        """Recompute the months a statement touches.

        TODO:
          1. Derive the affected months from the rows' ``posted_on``, not from
             the statement period. A billing period usually straddles two
             calendar months, and a late-posting row can fall outside it
             entirely.
          2. Delegate to :meth:`refresh_months`.
        """
        raise NotImplementedError

    async def refresh_months(self, months: list[date]) -> None:
        """Recompute specific months, truncated to the first of the month.

        TODO:
          1. Delete and re-insert the rows for those months, or upsert. Either
             works; do not accumulate.
          2. Group by user, card, category and month, which is the table's
             grain.
          3. Exclude rows with ``excluded_at`` set. A removed duplicate leaves
             the counted total.
          4. Exclude rows with no category, and make sure the dashboard's
             unclassified banner is computed separately. Folding unclassified
             money into a category would hide it.
          5. Make it idempotent. It runs from a retryable job stage, so running
             twice must give the same answer as running once.
        """
        raise NotImplementedError

    async def refresh_all(self) -> None:
        """Full rebuild.

        TODO:
          1. Recompute every month for the tenant.
          2. Used after a rules reapply across history, and as the repair tool
             when the aggregates are suspected of drifting. Worth having even
             before you need it, because the first time you need it will be the
             worst time to write it.
        """
        raise NotImplementedError


#: Every path that changes which category a row belongs to. Adding a sixth and
#: not refreshing is how the dashboard starts lying.
#:
#:   1. review finishing
#:   2. a per-transaction override
#:   3. a rule created with backfill scope
#:   4. a rules reapply
#:   5. a duplicate removed or restored
#:
#: Statement totals never change through any of them. Reclassification moves
#: money between categories; it does not create or destroy any.
REFRESH_TRIGGERS = (
    "review.finished",
    "transaction.overridden",
    "rule.backfilled",
    "rules.reapplied",
    "duplicate.resolved",
)
