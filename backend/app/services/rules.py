"""Rules. Screens 07 to 07e.

===========================================================================
BUILD STEPS 8.1 and 8.2 live in this file.

  8.1 preview, create, conflicts   depends on: 6.3 review classify
  8.2 reapply                      depends on: 7.1 aggregates, 8.1

Verify: uv run pytest tests/integration/test_rules.py
===========================================================================
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


class RuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- Step 8.1 ----------------------------------------------------------

    async def list_rules(self) -> object:
        """TODO: the tenant's rules with the category name and how many rows
        each currently matches."""
        raise NotImplementedError

    async def preview(self, *, pattern: str, match_type: str, options: object) -> object:
        """Dry-run a pattern while the user types.

        TODO:
          1. Count matching rows without changing any.
          2. Count how many are already in the target category, so the user can
             see the rule is mostly a no-op when it is.
          3. Count ``would_relabel_manual``: rows where ``classified_by`` is
             ``override``. That is the number that stops somebody silently
             destroying their own work, and it is computed before anything is
             saved rather than reported afterwards.
          4. Run the conflict check and return any overlaps.
          5. Keep it cheap. This runs on every keystroke.
        """
        raise NotImplementedError

    async def create(
        self,
        *,
        pattern: str,
        match_type: str,
        category_id: uuid.UUID,
        scope: str,
        options: object,
    ) -> object:
        """Create a rule.

        TODO:
          1. Check conflicts first and raise ``RuleConflict`` (409) with the
             colliding rule and a suggested narrowing. A conflict is never
             settled silently: two rules disagreeing about the same rows is a
             question only the user can answer.
          2. Insert the rule.
          3. Apply it to current rows, and to history when ``scope`` is
             ``backfill``.
          4. Never overwrite a row whose ``classified_by`` is ``override``.
          5. Refresh the affected monthly totals.
          6. Reject a pattern that could cause catastrophic backtracking, or
             bound its execution time (TC-SEC-011).
        """
        raise NotImplementedError

    async def update(self, rule_id: uuid.UUID, **changes: object) -> object:
        """TODO: apply the change, re-run the conflict check, and state the
        affected rows before and after. Screen 07d shows that comparison."""
        raise NotImplementedError

    async def delete(self, rule_id: uuid.UUID) -> None:
        """TODO: delete the rule. Decide whether rows it classified revert to
        unclassified or keep their category, and make it explicit. Both are
        defensible; silently doing one is not."""
        raise NotImplementedError

    async def conflicts(self) -> object:
        """TODO: every flagged overlap, with the overlapping rows in full.
        Screen 07b shows them because a count is not enough to choose."""
        raise NotImplementedError

    async def resolve_conflict(
        self, conflict_id: uuid.UUID, *, action: str, apply_to_history: bool
    ) -> object:
        """TODO: apply ``specific_wins``, ``narrow_broader`` or
        ``delete_specific``, and state the effect on category totals **before**
        committing."""
        raise NotImplementedError

    # -- Step 8.2 ----------------------------------------------------------

    async def reapply_preview(self, months: list[str] | None) -> object:
        """Per-month preview.

        TODO:
          1. Dry-run every rule across the requested months, or all imported
             months when omitted.
          2. Per month return rows retested, rows changing, and the effect on
             totals.
          3. Count ``overrides_at_risk`` across the whole run. That is the
             number the user needs before choosing.
          4. Change nothing. This is a preview, and the separation from
             :meth:`reapply` is the point: screen 07e is a preview with a rerun
             button, not a button that reruns.
        """
        raise NotImplementedError

    async def reapply(self, *, keep_overrides: bool, months: list[str] | None) -> object:
        """Re-run every rule across history.

        TODO:
          1. Enqueue the ``rules.reapply`` job rather than doing it inline. It
             can touch every row a person has.
          2. With ``keep_overrides=True``, exclude rows where ``classified_by``
             is ``override``. Write the test for this first: it is the promise
             most likely to be broken by a later refactor (TC-RULE-009).
          3. With ``keep_overrides=False``, discard them. Irrecoverable, so it
             is never the default and the preview stated the count.
          4. Refresh the aggregates when the job finishes.
          5. Log which branch ran and how many overrides were affected.

        Category totals move. Statement totals never do: reclassification
        relabels money, it does not create or destroy it.
        """
        raise NotImplementedError
