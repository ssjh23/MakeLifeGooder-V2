"""The descriptor normaliser. RED until app/classify/normalise.py is written.

Driven by the fixture corpus, so adding a case is editing a table rather than
writing a test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.classify.normalise import normalise, strip_reference_suffix

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "descriptors.toml"

pytestmark = pytest.mark.unwritten


def _cases() -> list[tuple[str, str, str]]:
    data = tomllib.loads(FIXTURES.read_text(encoding="utf-8"))
    return [(c["raw"], c["key"], c["why"]) for c in data["case"] if c["key"]]


@pytest.mark.p0
@pytest.mark.parametrize(("raw", "expected", "why"), _cases(), ids=lambda v: str(v)[:40])
def test_TC_REV_003_corpus(raw: str, expected: str, why: str) -> None:
    """Every descriptor in the corpus normalises to its recorded key."""
    assert normalise(raw) == expected, why


@pytest.mark.p0
def test_TC_REV_003_outlet_variants_collapse() -> None:
    """The stated pain point of the project, asserted directly."""
    assert normalise("MCDONALDS (CCP)") == normalise("MCDONALDS-JUNCTION8")


def test_TC_REV_004_is_pure() -> None:
    """Same input, same output, every time.

    Purity is what lets the whole corpus run without a database or a PDF, and
    what lets improved rules be re-run against stored descriptors instead of
    reprocessing every statement ever uploaded.
    """
    for raw, _, _ in _cases():
        assert normalise(raw) == normalise(raw)


def test_distinct_merchants_stay_distinct() -> None:
    """The failure mode in the other direction.

    Over-merging is worse than under-merging: a user seeing spending attributed
    to a shop they have never visited stops trusting the whole dashboard, while
    an extra question during review is a mild annoyance.
    """
    assert normalise("GRAB *TRANSPORT SG") != normalise("GRABFOOD")
    assert normalise("SHELL SERVICE STATION 12") != normalise("SHELLY'S CAFE")


def test_output_is_a_key_not_a_display_name() -> None:
    """Lowercase, no leading or trailing noise. What a person sees comes from
    ``merchants.canonical_name``, so this is free to be ugly."""
    for raw, _, _ in _cases():
        key = normalise(raw)
        assert key == key.strip().lower()


def test_empty_and_whitespace_do_not_raise() -> None:
    """A blank descriptor is a data problem, not a crash. It has to survive the
    pipeline as an unclassified row rather than failing an import."""
    assert normalise("") == ""
    assert normalise("   ") == ""


class TestStripReferenceSuffix:
    """The narrow cut of the pipeline split_descriptor() reuses -- see its
    own docstring for why it can't just call normalise() instead."""

    def test_removes_singapore_ref_no_and_everything_after_it(self) -> None:
        assert (
            strip_reference_suffix("SMP*GOMGOM Singapore Ref No. : 24575436044612140700973")
            == "SMP*GOMGOM"
        )

    def test_removes_a_bare_ref_no_with_no_leading_singapore(self) -> None:
        assert strip_reference_suffix("SINGTEL BILL PAYMENT Ref No. : 8842") == "SINGTEL BILL PAYMENT"

    def test_a_different_reference_number_still_converges(self) -> None:
        first = strip_reference_suffix("SMP*GOMGOM Singapore Ref No. : 24575436044612140700973")
        second = strip_reference_suffix("SMP*GOMGOM Singapore Ref No. : 99999999999999999999999")
        assert first == second

    def test_leaves_text_with_no_reference_field_untouched(self) -> None:
        assert strip_reference_suffix("MCDONALDS-JUNCTION8") == "MCDONALDS-JUNCTION8"
