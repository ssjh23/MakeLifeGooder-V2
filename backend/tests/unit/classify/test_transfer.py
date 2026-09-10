"""The transfer filter. RED until app/classify/transfer.py is written.

The privacy gate. These are P0 because the thing being prevented cannot be
undone once it happens: a name sent to a model provider and written into a log
is out, and no subsequent fix retrieves it.
"""

from __future__ import annotations

import pytest

from app.classify.transfer import is_person_transfer

pytestmark = pytest.mark.unwritten

PERSON_TRANSFERS = [
    "PAYNOW TRANSFER TO TAN AH KOW",
    "PAYNOW-MOBILE 91234567",
    "FAST TRANSFER TO MUHAMMAD BIN ABDULLAH",
    "IBG GIRO TO PRIYA RAMACHANDRAN",
    "PAYLAH! TRANSFER JOHN LIM",
    "I-BANK TFR TO WONG MEI LING",
]

MERCHANTS = [
    "MCDONALDS-JUNCTION8",
    "NTUC FAIRPRICE FINEST NEX",
    "GRAB *TRANSPORT SG",
    "SINGTEL BILL PAYMENT 8842",
    "SHELL SERVICE STATION 12",
]


@pytest.mark.p0
@pytest.mark.security
@pytest.mark.parametrize("descriptor", PERSON_TRANSFERS)
def test_TC_REV_011_person_transfers_are_excluded(descriptor: str) -> None:
    """A transfer to an individual never becomes a merchant.

    The person named here is not a user of this system. They did not agree to
    anything. Their name appeared on somebody else's bank statement.
    """
    assert is_person_transfer(descriptor) is True


@pytest.mark.p0
@pytest.mark.parametrize("descriptor", MERCHANTS)
def test_merchants_are_not_excluded(descriptor: str) -> None:
    """The filter has to be usable.

    Excluding everything would satisfy the privacy rule and destroy the product,
    so the cost of a false positive is real even though it is much smaller than
    the cost of a false negative.
    """
    assert is_person_transfer(descriptor) is False


@pytest.mark.p0
@pytest.mark.security
def test_ambiguous_rails_err_towards_exclusion() -> None:
    """PayNow is used by hawker stalls as well as by people.

    When a descriptor could be either, exclude it. One uncategorised row the
    user can fix in a click, against a third party's name leaving the system
    permanently: the two mistakes are not close to equal.
    """
    assert is_person_transfer("PAYNOW TRANSFER 91234567") is True


def test_handles_empty_input() -> None:
    assert is_person_transfer("") is False
