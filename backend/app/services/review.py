"""Review and classification. Screens 04, 04b, 04c, 05.

===========================================================================
BUILD STEPS 6.1, 6.2 and 6.3 live in this file.

  6.1 review board    depends on: 5.5 cascade
  6.2 duplicates      depends on: 3.2 transaction repository
  6.3 classify+finish depends on: 5.5, 6.1

Verify: uv run pytest tests/integration/test_review.py
===========================================================================
6.2 does not depend on 6.1, so if the cascade is not finished you can build the
duplicate pair handling first and come back.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


class ReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Step 6.1 ----------------------------------------------------------

    async def board(self, statement_id: uuid.UUID) -> object:
        """The review screen, grouped by merchant rather than by row.

        TODO:
          1. Load the statement's rows and group by ``descriptor_key``.
          2. Per group return the row count, the total, and every distinct
             ``description_raw`` as ``raw_descriptors``. Those are the evidence
             behind a grouping, and showing them is what makes the normaliser
             legible rather than magic: a person can see that two lines were
             treated as one merchant and disagree.
          3. Set each group's status: rule matched, overridden, or new.
          4. Build the footer so ``classified + unassigned`` equals the
             statement total, exactly. If it does not, something upstream is
             losing money and the footer is where you will notice.
          5. Count the four filters: needs category, new merchants, overridden,
             duplicates.

        Four McDonald's rows are one decision, not four. That grouping is the
        reason each month costs the user less than the last.
        """
        raise NotImplementedError

    # -- Step 6.2 ----------------------------------------------------------

    async def duplicates(self, statement_id: uuid.UUID) -> object:
        """Same merchant, same amount, within a day.

        TODO:
          1. Call ``TransactionRepository.find_duplicate_pairs``.
          2. Return each pair with the hours between them, so the user has the
             one fact that actually helps them decide.

        Flagged for a decision, never resolved automatically. A genuine repeat
        purchase looks identical to a double charge from the outside.
        """
        raise NotImplementedError

    async def resolve_duplicate(
        self,
        statement_id: uuid.UUID,
        pair_id: uuid.UUID,
        *,
        action: str,
        remove_row_id: uuid.UUID | None,
    ) -> object:
        """Settle one flagged pair.

        TODO:
          1. ``keep_both``: mark the pair resolved and leave both rows counted.
          2. ``remove``: require ``remove_row_id`` and set ``excluded_at`` on
             that row.
          3. Do **not** delete the row. It stays on the statement record,
             struck through, out of the counted total. The statement is a
             record of a document that exists in the world, and editing it away
             would make the ledger disagree with the paper (TC-TDUP-004).
          4. Refresh the affected monthly totals.
        """
        raise NotImplementedError

    async def undo_duplicate(self, statement_id: uuid.UUID, pair_id: uuid.UUID) -> object:
        """TODO: clear ``excluded_at``, refresh totals. Removal is reversible,
        and the UI promises that."""
        raise NotImplementedError

    # -- Step 6.3 ----------------------------------------------------------

    async def classify(
        self,
        descriptor_key: str,
        *,
        category_id: uuid.UUID | None,
        new_category_name: str | None,
        create_rule: bool,
        scope: str,
    ) -> object:
        """One decision for one merchant.

        TODO:
          1. Create the category first when ``new_category_name`` is given.
          2. Write a ``merchant_overrides`` row. This is the user's own ruling
             and it sits at the top of the cascade forever.
          3. Apply the category to this statement's matching rows, setting
             ``classified_by='override'``.
          4. When ``create_rule`` is set, check for a colliding rule **before**
             writing anything and raise ``RuleConflict`` (409) carrying the
             conflicting rule. Same payload shape as the rules endpoints, so one
             client component renders both and the two cannot drift apart.
          5. When ``scope`` is ``backfill``, restate history too, and make the
             count of affected rows match what the dialog stated before the user
             confirmed. A number that turns out to be wrong afterwards is worse
             than no number.
          6. With ``create_rule=False`` this is the "skip for now" path: rows
             are categorised, no rule is created, and next month asks again.
          7. Refresh the affected monthly totals.
        """
        raise NotImplementedError

    async def confirm_all(
        self, statement_id: uuid.UUID, descriptor_keys: list[str] | None
    ) -> object:
        """Bulk confirm rule-matched merchants only.

        TODO:
          1. Select merchants whose status is rule matched. When
             ``descriptor_keys`` is given, restrict to those.
          2. Never touch an unclassified merchant. Confirming a decision nobody
             made is how a bulk action stops being trusted (TC-REV-018).
        """
        raise NotImplementedError

    async def finish(self, statement_id: uuid.UUID) -> object:
        """Close review and return the import summary.

        TODO:
          1. Compute the unassigned total and raise ``ReviewIncomplete`` (422)
             while it is non-zero.
          2. Mark the review closed and enqueue the aggregate refresh.
          3. Return the summary: rows, duplicates removed, counted, classified
             by rule, classified by the user, unclassified.

        "Skip for now" during review is allowed and does not unlock the
        dashboard: a total that silently omits money is worse than a locked
        one, because the second is honest about what it does not know.
        """
        raise NotImplementedError

    async def summary(self, statement_id: uuid.UUID) -> object:
        """TODO: rebuild the same summary later. Screen 04c is a receipt, and a
        receipt you can only see once is not much of a receipt."""
        raise NotImplementedError
