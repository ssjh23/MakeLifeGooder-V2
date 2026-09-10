"""Dashboard and per-row override. Screens 06, 06b, 06c.

===========================================================================
BUILD STEP 7.2   depends on: 7.1 aggregate refresher
Verify: uv run pytest tests/integration/test_dashboard.py
===========================================================================
The milestone after this one is the whole product working end to end: import,
reconcile, review, classify, see the breakdown.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bands(self, *, range_: str, month: date | None) -> object:
        """The five bands.

        TODO:
          1. Read ``category_monthly_totals`` through ``DashboardRepository``.
             If you find yourself summing ``transactions`` here, the aggregate
             refresher is missing a trigger and this would paper over it.
          2. Build the bands: months stacked, categories with companies, by
             card, categories ranked, and recent transactions.
          3. Compute the unclassified total separately and set the banner.
          4. Lock the totals while anything is unclassified. A total that
             silently omits money is worse than a locked one, because the
             second is honest about what it does not know.
          5. Handle the empty case. A new user with no data must get a sensible
             response, not a divide by zero in a share calculation.

        The precomputed table is what lets this meet a latency budget, and it
        is also why Redis bought nothing at launch: caching an
        already-precomputed aggregate only adds an invalidation problem on
        every reclassification (ADR-010).
        """
        raise NotImplementedError

    async def category_detail(self, category_id: uuid.UUID) -> object:
        """TODO: six months of totals, companies ranked with their share and
        change, and which cards were used. Screen 06b answers *who*."""
        raise NotImplementedError

    async def transaction(self, transaction_id: uuid.UUID) -> object:
        """The only place that answers *why is this in this category*.

        TODO:
          1. Return the row with full provenance: statement, page, line,
             ``classified_by``, and the rule id or prompt version responsible.
          2. Include flags such as ``paired_with_duplicate``.
          3. Raise ``NotFound`` when absent, which also covers another tenant's
             row without having to check.

        Provenance is what makes a wrong category something a person can
        investigate rather than merely disagree with.
        """
        raise NotImplementedError

    async def override(self, transaction_id: uuid.UUID, *, category_id: uuid.UUID) -> object:
        """Recategorise one row.

        TODO:
          1. Set the category and ``classified_by='override'``, which is what
             protects the row from future rule runs.
          2. Refresh the affected monthly totals.
          3. Return **both** the category totals and the unchanged statement
             total, so the invariant is visible in the response rather than
             something the client is asked to trust (TC-DASH-009).

        Money moves between categories; the statement total does not change.
        """
        raise NotImplementedError

    async def source_page_url(self, statement_id: uuid.UUID, page: int) -> str:
        """A short-lived signed URL for the source PDF page.

        TODO:
          1. Fetch the statement so the policy confirms it is the caller's.
          2. Ask the store to presign a GET with a short expiry.
          3. Never log the URL. It is a bearer credential, and a logged one is
             a leaked file.
        """
        raise NotImplementedError
