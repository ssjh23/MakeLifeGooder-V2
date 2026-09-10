"""The reconciler. RED until app/extract/reconcile.py is written.

The highest-value suite in the repository. Every case defends one invariant:
nothing enters the ledger unless the arithmetic ties, or the gap is explicitly
recorded and then carried everywhere.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.extract.base import ParsedRow, ParsedStatement
from app.extract.reconcile import Reconciler

pytestmark = pytest.mark.unwritten


def _statement(rows: list[int], printed_total: int | None, currency: str = "SGD") -> ParsedStatement:
    return ParsedStatement(
        rows=[
            ParsedRow(
                posted_on=date(2026, 7, 22),
                description_raw=f"MERCHANT {i}",
                amount_minor=amount,
                currency=currency,
            )
            for i, amount in enumerate(rows)
        ],
        printed_total_minor=printed_total,
        currency=currency,
        period_start=date(2026, 7, 15),
        period_end=date(2026, 8, 14),
        detected_last4="4429",
        has_text_layer=True,
        parser="test",
    )


class TestCheck:
    @pytest.mark.p0
    def test_TC_REC_001_rows_that_tie_reconcile(self) -> None:
        result = Reconciler().check(_statement([1240, 4810], 6050))
        assert result.reconciled is True
        assert result.difference_minor == 0

    @pytest.mark.p0
    def test_TC_REC_004_difference_is_computed_correctly(self) -> None:
        """Rows total 179.50, the statement says 187.20, so 7.70 is missing."""
        result = Reconciler().check(_statement([17950], 18720))
        assert result.difference_minor == 770
        assert result.reconciled is False

    @pytest.mark.p0
    def test_TC_REC_009_refunds_subtract(self) -> None:
        result = Reconciler().check(_statement([10000, -3000], 7000))
        assert result.reconciled is True

    @pytest.mark.p0
    def test_TC_REC_008_many_small_amounts_stay_exact(self) -> None:
        """Where floating point would drift. Integers do not."""
        result = Reconciler().check(_statement([10, 20], 30))
        assert result.reconciled is True

    def test_empty_statement_with_zero_total_ties(self) -> None:
        assert Reconciler().check(_statement([], 0)).reconciled is True

    def test_missing_printed_total_does_not_silently_pass(self) -> None:
        """Reconciling against nothing is not reconciling.

        A document with no printed total, or a scan whose total the user typed,
        must not produce ``reconciled=True`` by default. Whatever this returns,
        it cannot be a quiet pass.
        """
        result = Reconciler().check(_statement([1240], None))
        assert result.reconciled is False

    def test_mixed_currency_rows_do_not_silently_add(self) -> None:
        """Multi-currency has no designed behaviour yet, and adding SGD to USD
        would be wrong in a way nothing downstream could detect."""
        statement = _statement([1000], 1000)
        foreign = ParsedRow(
            posted_on=date(2026, 7, 23),
            description_raw="OVERSEAS PURCHASE",
            amount_minor=5000,
            currency="USD",
        )
        mixed = ParsedStatement(**{**statement.__dict__, "rows": [*statement.rows, foreign]})
        result = Reconciler().check(mixed)
        assert result.reconciled is False


class TestGate:
    @pytest.mark.p0
    def test_TC_REC_002_unreconciled_cannot_commit(self) -> None:
        result = Reconciler().check(_statement([17950], 18720))
        with pytest.raises(Exception):  # noqa: B017 - the service maps this to 422
            Reconciler().assert_committable(result, accept_gap=False)

    @pytest.mark.p0
    def test_TC_REC_010_accepted_gap_commits_and_stays_flagged(self) -> None:
        """An accepted gap is not a way past the gate.

        It is a way of being honest that the arithmetic does not close, in a
        form that stays visible rather than becoming a rounding error somebody
        rediscovers next year.
        """
        result = Reconciler().apply_gap(
            Reconciler().check(_statement([17950], 18720)), reason="Missing page 3"
        )
        Reconciler().assert_committable(result, accept_gap=True)
        assert result.reconciled is False

    @pytest.mark.p0
    def test_reconciled_statement_commits(self) -> None:
        result = Reconciler().check(_statement([1240, 4810], 6050))
        Reconciler().assert_committable(result, accept_gap=False)
