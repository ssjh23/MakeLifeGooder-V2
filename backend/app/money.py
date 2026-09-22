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
    """


def exponent(currency: str) -> int:
    """Return the number of minor units for ``currency``

    Raises:
        UnknownCurrencyError: if the code is not in :data:`MINOR_UNITS`.
    """
    if currency not in MINOR_UNITS:
        raise UnknownCurrencyError(f"Unknown currency: {currency}")
    return MINOR_UNITS[currency]


def to_minor(amount: Decimal | str, currency: str = DEFAULT_CURRENCY) -> int:
    """Convert a decimal amount to signed minor units.

    ``"12.40"`` in SGD is ``1240``. A refund is negative.
    """
    if isinstance(amount, str):
        amount = Decimal(amount)
    exp = exponent(currency)
    if amount.as_tuple().exponent < -exp:
        raise ValueError(f"Amount has more precision than allowed for {currency}")
    return int(amount * (10 ** exp))


def to_decimal_string(amount_minor: int, currency: str = DEFAULT_CURRENCY) -> str:
    """Render signed minor units as the API's decimal string.

    ``1240`` in SGD is ``"12.40"``. Always fully padded, never in scientific
    notation, and never a float on the way through.
    """
    exp = exponent(currency)
    amount = Decimal(amount_minor) / (10 ** exp)
    return f"{amount:.{exp}f}"


def sum_minor(amounts: list[int]) -> int:
    """Total a list of minor-unit amounts.
    """
    return sum(amounts)
