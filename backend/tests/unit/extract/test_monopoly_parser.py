"""The monopoly-core adapter. BUILD STEP 2.2 (see app/extract/monopoly_parser.py).

Two kinds of fixture, deliberately no real bank statement:

* The four generated failure-shape PDFs (encrypted, scanned, not_a_statement,
  malformed) -- the same corpus ``test_parser_failures.py`` uses for
  ``PdfplumberParser`` -- prove failure detection without any bank config at
  all, since none of the four is a real bank's statement.
* Monopoly's own bundled example bank and statement prove real detection and
  row extraction, including the sign-convention boundary, without any of
  Sean's redacted PDFs. ``MonopolyParser(banks=[ExampleBank])`` injects the
  bank list specifically so this works: the real, shipped bank list would
  never match Monopoly's own example statement.

What this file cannot prove -- and TC-IMP-009 still needs a real, redacted
statement for -- is that a *real* bank's regex configuration in Monopoly
matches *our* real PDFs, and that the sign convention documented in
app/extract/monopoly_parser.py holds for every bank we ship, not only the
bundled example. See tests/fixtures/pdfs/banks/manifest.toml.
"""

from __future__ import annotations

from datetime import date
from importlib import resources

import pytest
from monopoly.examples.example_bank import ExampleBank
from monopoly.statements.transaction import Transaction

from app.extract.base import ExtractionFailure
from app.extract.monopoly_parser import MonopolyParser
from app.money import DEFAULT_CURRENCY
from tests.fixtures.generate import OUTPUT_DIR
from tests.fixtures.generate import main as generate_fixtures

pytestmark = pytest.mark.unwritten

#: Filename -> the reason MonopolyParser.parse() must raise for it. Same four
#: fixtures and reasons as test_parser_failures.py: none of them is a real
#: bank's statement, so Monopoly's own detection never gets a chance to
#: matter for these -- the encryption, text-layer and open checks all run
#: before bank detection does (see the module docstring).
FIXTURE_REASONS: dict[str, str] = {
    "encrypted.pdf": "password_protected",
    "scanned.pdf": "no_text_layer",
    "not_a_statement.pdf": "not_a_statement",
    "malformed.pdf": "parser_error",
}


@pytest.fixture(scope="session", autouse=True)
def _generated_fixtures() -> None:
    generate_fixtures()


@pytest.fixture
def parser() -> MonopolyParser:
    return MonopolyParser()


@pytest.fixture
def example_parser() -> MonopolyParser:
    """A parser scoped to Monopoly's own dummy bank.

    The real, shipped bank list would never match Monopoly's own example
    statement, so exercising real detection + extraction needs this instead.
    """
    return MonopolyParser(banks=[ExampleBank])


def _read(name: str) -> bytes:
    return (OUTPUT_DIR / name).read_bytes()


def _example_statement_bytes() -> bytes:
    return resources.files("monopoly.examples").joinpath("example_statement.pdf").read_bytes()


class TestFailureShapes:
    @pytest.mark.p0
    @pytest.mark.parametrize(("filename", "reason"), FIXTURE_REASONS.items())
    def test_TC_FAIL_002_each_generated_fixture_raises_its_reason(
        self, parser: MonopolyParser, filename: str, reason: str
    ) -> None:
        """None of the four is a real bank statement, so this also proves
        detection never runs -- or never matters -- before the more specific
        checks (password, text layer) get a chance to fire first."""
        with pytest.raises(ExtractionFailure) as exc_info:
            parser.parse(_read(filename))

        assert exc_info.value.reason == reason
        assert str(exc_info.value)

    def test_wrong_password_is_still_password_protected(self, parser: MonopolyParser) -> None:
        with pytest.raises(ExtractionFailure) as exc_info:
            parser.parse(_read("encrypted.pdf"), password="not-the-password")

        assert exc_info.value.reason == "password_protected"

    @pytest.mark.parametrize("filename", FIXTURE_REASONS.keys())
    def test_supports_never_raises(self, parser: MonopolyParser, filename: str) -> None:
        """supports() has no password, so it cannot always give the real
        answer -- but it must never crash finding that out."""
        parser.supports(institution=None, pdf_bytes=_read(filename))

    def test_supports_defers_on_no_text_layer_rather_than_misreporting(
        self, parser: MonopolyParser
    ) -> None:
        """The bug this pins: bank detection alone would call the scanned
        fixture "unsupported" (no text for any identifier to match), which is
        the wrong reason and would stop parse() from ever running to give the
        right one. supports() must rule out "no text layer" first, the same
        order parse() uses, and defer (True) rather than misclassify."""
        assert parser.supports(institution=None, pdf_bytes=_read("scanned.pdf")) is True

    def test_supports_rejects_a_readable_unsupported_bank(self, parser: MonopolyParser) -> None:
        """Once there is enough text and still no bank match, supports() can
        give the real answer: False."""
        assert parser.supports(institution=None, pdf_bytes=_read("not_a_statement.pdf")) is False


class TestExampleStatement:
    """Real detection and row extraction, via Monopoly's own bundled fixture."""

    @pytest.mark.p0
    def test_extracts_the_bundled_example_statement(self, example_parser: MonopolyParser) -> None:
        result = example_parser.parse(_example_statement_bytes())

        assert result.parser == "monopoly:example"
        assert len(result.rows) == 53
        assert result.currency == DEFAULT_CURRENCY  # ExampleBank sets no currency
        assert result.period_start is None  # ExampleBank sets no period_start_pattern
        assert result.period_end == date(2023, 7, 1)
        assert result.detected_last4 is None  # ExampleBank sets no account_pattern
        assert result.has_text_layer is True
        assert result.page_count > 0

    @pytest.mark.p0
    def test_reconciled_statement_carries_monopolys_safety_check_through(
        self, example_parser: MonopolyParser
    ) -> None:
        """Confirmed empirically: Monopoly's own safety check passes on this
        statement. TC-REC-013: our printed_total_minor must then agree with
        the summed rows exactly, not merely be present."""
        result = example_parser.parse(_example_statement_bytes())

        assert result.printed_total_minor is not None
        assert result.printed_total_minor == sum(row.amount_minor for row in result.rows)
        assert result.warnings == []

    @pytest.mark.p0
    def test_ordinary_purchase_is_positive_and_refund_is_negative(
        self, example_parser: MonopolyParser
    ) -> None:
        """The sign-convention finding, pinned against a real extraction.

        Monopoly reports an ordinary purchase as a negative amount and a
        payment/refund as positive -- the opposite of app/money.py's
        convention, where a spend is positive and "a refund is negative".
        Confirmed by running Monopoly's own example statement directly before
        writing the adapter: an unmarked $4.20 purchase came back as -4.2,
        and "PAYMENT BY INTERNET" $412.16 came back as +412.16.
        """
        result = example_parser.parse(_example_statement_bytes())
        by_description = {row.description_raw: row.amount_minor for row in result.rows}

        assert by_description["DELIGHTFUL BREAKFAST SINGAPORE SG"] == 420
        assert by_description["PAYMENT BY INTERNET"] == -41216


class TestToMinor:
    """The sign-inversion boundary in isolation, no PDF needed."""

    def test_a_credit_marked_amount_becomes_negative(self) -> None:
        """Monopoly: CR marker -> positive (a refund/payment). Ours: negative."""
        tx = Transaction(
            transaction_date="01/07", description="refund", amount="10.00", direction="CR"
        )
        assert tx.amount == 10.0

        assert MonopolyParser()._to_minor(tx.amount, "SGD") == -1000

    def test_an_unmarked_amount_becomes_positive(self) -> None:
        """Monopoly: no marker -> negative (an ordinary spend, its default).
        Ours: positive."""
        tx = Transaction(transaction_date="01/07", description="coffee", amount="4.20")
        assert tx.amount == -4.2

        assert MonopolyParser()._to_minor(tx.amount, "SGD") == 420
