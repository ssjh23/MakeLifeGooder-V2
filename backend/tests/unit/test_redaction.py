"""Redaction. GREEN - this is written, not stubbed.

A redactor that is absent, partial or permissive is a privacy defect that looks
finished, and unlike the business logic there is no version of it that is
instructive to get wrong. These tests exist to keep it correct while the
allow-list grows.
"""

from __future__ import annotations

import pytest

from app.telemetry.redact import ALLOWED, DENIED_WITH_REASON, RedactionProcessor, hash_descriptor

SALT = "test-salt"


@pytest.fixture
def processor() -> RedactionProcessor:
    return RedactionProcessor(salt=SALT)


@pytest.mark.p0
@pytest.mark.security
def test_TC_TEL_011_unrecognised_fields_are_dropped(processor: RedactionProcessor) -> None:
    """Fail closed.

    A deny-list would let a newly added field carrying a merchant name ship to
    the log backend, and nobody would notice. An allow-list makes the same
    mistake show up as a missing field in a dashboard, which somebody chases.
    """
    event = processor(None, "info", {"event": "x", "something_new": "a person's name"})
    assert "something_new" not in event
    assert event["event"] == "x"


@pytest.mark.p0
@pytest.mark.security
@pytest.mark.parametrize("field", sorted(DENIED_WITH_REASON))
def test_denied_fields_never_survive(processor: RedactionProcessor, field: str) -> None:
    event = processor(None, "info", {"event": "x", field: "SENSITIVE-VALUE"})
    assert field not in event
    assert "SENSITIVE-VALUE" not in str(event)


@pytest.mark.p0
@pytest.mark.security
def test_TC_TEL_006_raw_descriptor_becomes_a_hash(processor: RedactionProcessor) -> None:
    """The descriptor is replaced, not merely omitted.

    A descriptor can be an individual's name, so the raw string must not appear.
    The hash still correlates one merchant across log lines, which is what the
    field was for.
    """
    event = processor(None, "info", {"event": "x", "descriptor": "PAYNOW TO TAN AH KOW"})
    assert "descriptor" not in event
    assert "TAN AH KOW" not in str(event)
    assert event["descriptor_key_hash"] == hash_descriptor("PAYNOW TO TAN AH KOW", salt=SALT)


@pytest.mark.p0
def test_TC_TEL_007_same_merchant_hashes_consistently() -> None:
    a = hash_descriptor("mcdonalds", salt=SALT)
    b = hash_descriptor("mcdonalds", salt=SALT)
    assert a == b
    assert a != hash_descriptor("fairprice", salt=SALT)


@pytest.mark.p0
@pytest.mark.security
def test_hash_is_salted() -> None:
    """Keyed, not plain.

    An unsalted hash of a short, low-entropy string is reversible by anyone
    willing to hash a list of Singapore merchants, which would defeat the point
    of not logging the descriptor.
    """
    assert hash_descriptor("mcdonalds", salt="a") != hash_descriptor("mcdonalds", salt="b")


@pytest.mark.p0
def test_correlation_fields_survive(processor: RedactionProcessor) -> None:
    """The allow-list has to let the useful things through, or the logs answer
    none of the three questions ADR-014 requires."""
    event = processor(
        None,
        "info",
        {
            "event": "statement.extract.succeeded",
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            "request_id": "req_01HXY",
            "stage": "extract",
            "row_count": 47,
            "reconciled": True,
            "difference_nonzero": False,
            "duration_ms": 3820,
        },
    )
    assert event["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert event["row_count"] == 47
    assert event["reconciled"] is True


@pytest.mark.p0
@pytest.mark.security
def test_TC_TEL_008_amounts_are_booleans_not_values(processor: RedactionProcessor) -> None:
    """Financial data is identifying in combination with a user id, so the
    reconciler logs whether a difference exists, never what it is."""
    event = processor(
        None, "info", {"event": "x", "difference": "12.40", "difference_nonzero": True}
    )
    assert "difference" not in event
    assert event["difference_nonzero"] is True


def test_allow_and_deny_lists_do_not_overlap() -> None:
    """A field in both would mean the allow-list silently wins and the recorded
    reason is a lie."""
    assert not (ALLOWED & set(DENIED_WITH_REASON))
