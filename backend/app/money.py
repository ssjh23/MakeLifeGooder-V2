"""Money.

Amounts are stored as ``amount_minor``: a signed integer count of minor units,
never a float. The API carries them as decimal strings paired with a currency.
This module is the only place the two representations meet.

Why this is not a detail: reconciliation compares a sum of rows against a total
printed on a bank statement, and asks whether they are *equal*. Binary floating
point makes that question unanswerable, and the failure is silent. TC-REC-008
pins it down with the canonical example.

===========================================================================
BUILD STEP 0.1   depends on: nothing   blocks: 1.1 reconciler, 2.2 parsers
Verify: uv run pytest tests/unit/test_money.py
===========================================================================
The first thing to write. No database, no network, nothing else needed. Run the
test before touching anything and watch it fail.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: Minor units per major unit, by ISO 4217 code. Most currencies use two, and
#: the ones that do not are the reason this is a lookup rather than a constant.
#: SGD is the only one in scope today; the others are here so that adding a
#: currency is a data change.
MINOR_UNITS: Final[dict[str, int]] = {
    "SGD": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "MYR": 2,
    "AUD": 2,
    "JPY": 0,
    "KRW": 0,
}

DEFAULT_CURRENCY: Final[str] = "SGD"


class UnknownCurrencyError(ValueError):
    """Raised for a currency with no known minor-unit exponent.

    Deliberately loud. Guessing two decimal places for an unknown currency
    would silently misstate every amount in it by a factor of a hundred.
    """


def exponent(currency: str) -> int:
    """Return the number of minor units for ``currency``.

    TODO:
      1. Look ``currency`` up in :data:`MINOR_UNITS`.
      2. Raise :class:`UnknownCurrencyError` when it is absent. Do not fall back
         to two: a wrong exponent is a hundredfold error that nothing
         downstream can detect.

    Raises:
        UnknownCurrencyError: if the code is not in :data:`MINOR_UNITS`.
    """
    raise NotImplementedError


def to_minor(amount: Decimal | str, currency: str = DEFAULT_CURRENCY) -> int:
    """Convert a decimal amount to signed minor units.

    ``"12.40"`` in SGD is ``1240``. A refund is negative.

    TODO:
      1. Accept a ``str`` or a ``Decimal``. Build a ``Decimal`` from the string
         directly, never via ``float``, or the error you are avoiding is
         reintroduced at the boundary.
      2. Read the exponent with :func:`exponent`.
      3. Reject an amount carrying more precision than the currency allows,
         rather than rounding it. Silently dropping a third decimal place makes
         a statement fail to reconcile for a reason nobody can see on screen.
      4. Shift by the exponent and return an ``int``.
      5. Check the zero-exponent case works: JPY has no minor unit at all.

    See TC-REC-008, TC-REC-009.
    """
    raise NotImplementedError


def to_decimal_string(amount_minor: int, currency: str = DEFAULT_CURRENCY) -> str:
    """Render signed minor units as the API's decimal string.

    ``1240`` in SGD is ``"12.40"``. Always fully padded, never in scientific
    notation, and never a float on the way through.

    TODO:
      1. Divide by the currency's scale using ``Decimal``, not ``/``.
      2. Quantise to the exponent so ``5`` renders as ``"0.05"`` rather than
         ``"0.05000"`` or ``"0.5"``.
      3. Format with an explicit format spec. ``str(Decimal)`` will use
         scientific notation for large values, and a total of ``1E+12`` on a
         dashboard is a bug report.
      4. Confirm it round-trips: ``to_decimal_string(to_minor(x)) == x``.
    """
    raise NotImplementedError


def sum_minor(amounts: list[int]) -> int:
    """Total a list of minor-unit amounts.

    Trivial by construction, which is the point: integers make the reconciler's
    central comparison exact. Present as a named function so the call site reads
    as an intention rather than a builtin.

    TODO:
      1. Sum the list. An empty list is ``0``, not an error.
    """
    raise NotImplementedError
