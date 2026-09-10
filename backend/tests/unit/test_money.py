"""Money. RED until app/money.py is written.

Reconciliation asks whether a sum of rows equals a total printed on paper. In
binary floating point that question has no reliable answer, and the wrong answer
is silent. These tests pin the representation before anything depends on it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import UnknownCurrencyError, exponent, sum_minor, to_decimal_string, to_minor

pytestmark = pytest.mark.unwritten


class TestToMinor:
    def test_TC_REC_008_converts_decimal_string_to_minor_units(self) -> None:
        assert to_minor("12.40", "SGD") == 1240

    def test_TC_REC_009_refund_is_negative(self) -> None:
        assert to_minor("-48.10", "SGD") == -4810

    def test_zero_currency_has_no_minor_units(self) -> None:
        # JPY has no minor unit. Assuming two everywhere would misstate every
        # yen amount by a factor of a hundred.
        assert to_minor("1200", "JPY") == 1200

    def test_rejects_excess_precision_rather_than_rounding(self) -> None:
        # Silently dropping a third decimal place makes a statement fail to
        # reconcile for a reason nobody can see on screen.
        with pytest.raises(ValueError):
            to_minor("12.404", "SGD")

    def test_unknown_currency_is_loud(self) -> None:
        with pytest.raises(UnknownCurrencyError):
            to_minor("10.00", "XYZ")


class TestToDecimalString:
    @pytest.mark.parametrize(
        ("minor", "expected"),
        [(1240, "12.40"), (-4810, "-48.10"), (0, "0.00"), (5, "0.05"), (100000000, "1000000.00")],
    )
    def test_renders_padded_decimal(self, minor: int, expected: str) -> None:
        assert to_decimal_string(minor, "SGD") == expected

    def test_large_amount_is_not_scientific_notation(self) -> None:
        assert "e" not in to_decimal_string(10**12, "SGD").lower()

    def test_round_trips(self) -> None:
        for value in ("0.01", "12.40", "-48.10", "9999.99"):
            assert to_decimal_string(to_minor(value, "SGD"), "SGD") == value


class TestSumMinor:
    def test_TC_REC_008_the_canonical_float_failure(self) -> None:
        """0.1 + 0.2 must reconcile exactly against 0.30.

        The canonical demonstration that floats cannot do this job: in binary
        floating point that sum is 0.30000000000000004, and a statement would
        fail to balance by a fraction of a cent with no visible cause.
        """
        rows = [to_minor("0.10", "SGD"), to_minor("0.20", "SGD")]
        assert sum_minor(rows) == to_minor("0.30", "SGD")

    def test_many_small_amounts_stay_exact(self) -> None:
        rows = [to_minor("0.07", "SGD")] * 1000
        assert sum_minor(rows) == to_minor("70.00", "SGD")

    def test_refunds_subtract(self) -> None:
        rows = [to_minor("100.00", "SGD"), to_minor("-30.00", "SGD")]
        assert sum_minor(rows) == to_minor("70.00", "SGD")

    def test_empty_is_zero(self) -> None:
        assert sum_minor([]) == 0


class TestExponent:
    def test_sgd_has_two(self) -> None:
        assert exponent("SGD") == 2

    def test_accepts_decimal_input(self) -> None:
        assert to_minor(Decimal("12.40"), "SGD") == 1240
