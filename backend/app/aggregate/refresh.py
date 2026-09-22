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

from sqlalchemy import Date as SQLDate
from sqlalchemy import cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CategoryMonthlyTotal, Transaction
from app.db.session import current_tenant_id


class AggregateRefresher:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def refresh_statement(self, statement_id: uuid.UUID) -> None:
        """Recompute the months a statement touches.

        Derived from the rows' own ``posted_on``, not the statement period: a
        billing period usually straddles two calendar months, and a
        late-posting row can fall outside it entirely.
        """
        result = await self._session.execute(
            select(Transaction.posted_on)
            .where(Transaction.statement_id == statement_id)
            .distinct()
        )
        months = [posted_on.replace(day=1) for posted_on in result.scalars().all()]
        await self.refresh_months(months)

    async def refresh_months(self, months: list[date]) -> None:
        """Recompute specific months, truncated to the first of the month.

        Delete-and-reinsert rather than upsert: idempotent by construction
        (running twice gives the same answer as running once), and simpler
        than reconciling a diff against whatever the previous refresh left
        behind.
        """
        truncated = sorted({month.replace(day=1) for month in months})
        if not truncated:
            return

        user_id = await current_tenant_id(self._session)

        await self._session.execute(
            delete(CategoryMonthlyTotal).where(
                CategoryMonthlyTotal.user_id == user_id,
                CategoryMonthlyTotal.month.in_(truncated),
            )
        )

        month_expr = cast(func.date_trunc("month", Transaction.posted_on), SQLDate)
        result = await self._session.execute(
            select(
                Transaction.card_id,
                Transaction.category_id,
                month_expr.label("month"),
                func.sum(Transaction.amount_minor).label("total_minor"),
                func.count().label("txn_count"),
            )
            .where(
                month_expr.in_(truncated),
                # A removed duplicate leaves the counted total.
                Transaction.excluded_at.is_(None),
                # Unclassified money is never folded into a category; the
                # dashboard's unclassified banner accounts for it separately
                # (BUILD STEP 7.2).
                Transaction.category_id.is_not(None),
                # The table's grain requires a card. A row with none cannot
                # appear on any dashboard band, the same rule the commit gate
                # already enforces before a statement can reach this stage.
                Transaction.card_id.is_not(None),
            )
            .group_by(Transaction.card_id, Transaction.category_id, month_expr)
        )

        rows = result.all()
        if rows:
            self._session.add_all(
                CategoryMonthlyTotal(
                    user_id=user_id,
                    card_id=row.card_id,
                    category_id=row.category_id,
                    month=row.month,
                    total_minor=row.total_minor,
                    txn_count=row.txn_count,
                )
                for row in rows
            )
        await self._session.flush()

    async def refresh_all(self) -> None:
        """Full rebuild: every month the tenant has a transaction in.

        Used after a rules reapply across history, and as the repair tool
        when the aggregates are suspected of drifting.
        """
        result = await self._session.execute(select(Transaction.posted_on).distinct())
        months = [posted_on.replace(day=1) for posted_on in result.scalars().all()]
        await self.refresh_months(months)


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
