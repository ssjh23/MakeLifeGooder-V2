"""The reconciler. GATE.

This is the single most important piece of behaviour in the product, and the
reason the ledger can be trusted at all.

It is a gate, not a report. Rows must sum to the total printed on the statement,
or the user records an explicit gap with a reason. There is no third outcome.
Nothing enters the ledger unreconciled.

Why that matters more than it sounds: the product's whole claim is that the
numbers are right. A categoriser that quietly drops a row still produces a
plausible dashboard, and nobody notices for months. Comparing against a figure
printed by the bank is the one check that cannot be fooled by our own bugs,
because it comes from outside the system.

Arithmetic is on integers throughout. Binary floating point cannot answer the
question this module asks, and would answer it wrongly at random.

===========================================================================
BUILD STEP 1.1   depends on: 0.1 money   blocks: 4.2 extract, 4.4 commit
Verify: uv run pytest tests/unit/extract/test_reconcile.py -m p0
===========================================================================
After this, every pure-logic P0 case in the suite should pass. Run
``uv run pytest -m p0`` to confirm before moving on to anything with a database
in it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.extract.base import ParsedStatement


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    extracted_total_minor: int
    printed_total_minor: int
    difference_minor: int
    reconciled: bool
    currency: str


class Reconciler:
    """Compares extracted rows against the printed total."""

    def check(self, parsed: ParsedStatement) -> ReconcileResult:
        """Sum the rows and compare against the printed total.

        TODO:
          1. Sum ``row.amount_minor`` with :func:`app.money.sum_minor`.
          2. Subtract from ``printed_total_minor`` and set ``reconciled`` when
             the difference is exactly zero.
          3. Handle **no printed total**. Some documents do not carry one, and a
             scan entered by hand has whatever total the user typed. Whatever
             you return, it must not be ``reconciled=True``: reconciling against
             nothing is not reconciling.
          4. Handle **mixed currencies**. A row in another currency cannot join
             the same sum. Multi-currency has no designed behaviour yet, so the
             only requirement now is that it does not silently add.
          5. Exclude skipped rows from the sum, so skipping one changes the
             difference (TC-REC-007).
          6. Check refunds. A negative row subtracts and the statement still has
             to tie (TC-REC-009).

        See TC-REC-004, TC-REC-008.
        """
        raise NotImplementedError

    def apply_gap(self, result: ReconcileResult, *, reason: str) -> ReconcileResult:
        """Record an accepted gap.

        The statement is then permanently marked unreconciled and the gap is
        carried in every view of that month. This is not a way of passing the
        gate: it is a way of being honest that the arithmetic does not close,
        in a form that stays visible instead of quietly becoming a rounding
        error somebody rediscovers next year (TC-REC-010).

        TODO:
          1. Return a copy with ``reconciled=False``, permanently.
          2. Keep the difference on the result. Every view of that month has to
             be able to show what is missing, not merely that something is.
          3. Require a non-empty ``reason``. An unexplained gap is
             indistinguishable from a bug six months later.
        """
        raise NotImplementedError

    def assert_committable(self, result: ReconcileResult, *, accept_gap: bool) -> None:
        """Raise unless this statement may be committed.

        TODO:
          1. Return silently when ``result.reconciled`` is true.
          2. Return silently when ``accept_gap`` is true and a gap was recorded.
          3. Otherwise raise ``NotReconciled`` from :mod:`app.api.errors`, with
             the difference and currency in ``details`` so screen 03b can show
             what is missing without another request.
          4. Import the error rather than inventing a local exception, so the
             API layer maps it to 422 without translating anything.

        Called by the service on the commit path. Enforced on the server, which
        is what TC-REC-003 checks by calling the endpoint directly with the
        frontend out of the picture.
        """
        raise NotImplementedError
