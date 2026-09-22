"""Parser failure detection. RED until PdfplumberParser.parse() is written.

BUILD STEP 2.1 (see app/extract/pdfplumber_parser.py). One case per generated
failure-shape fixture, each asserting the right ``ExtractionFailure.reason``:
encrypted -> password_protected, scanned -> no_text_layer, not_a_statement ->
not_a_statement, malformed -> parser_error.

Pure Python and no PDF needs to be supplied: the four fixtures are generated
by ``tests/fixtures/generate.py``, since a failure shape is a structural
property rather than a bank's layout.
"""

from __future__ import annotations

import pytest

from app.extract.base import ExtractionFailure
from app.extract.pdfplumber_parser import PdfplumberParser
from tests.fixtures.generate import OUTPUT_DIR
from tests.fixtures.generate import main as generate_fixtures

pytestmark = pytest.mark.unwritten

#: Filename -> the reason PdfplumberParser.parse() must raise for it.
FIXTURE_REASONS: dict[str, str] = {
    "encrypted.pdf": "password_protected",
    "scanned.pdf": "no_text_layer",
    "not_a_statement.pdf": "not_a_statement",
    "malformed.pdf": "parser_error",
}


@pytest.fixture(scope="session", autouse=True)
def _generated_fixtures() -> None:
    """Regenerate the four failure-shape PDFs once per test run.

    Deterministic and cheap, so there is no staleness to guard against by
    checking whether they already exist.
    """
    generate_fixtures()


@pytest.fixture
def parser() -> PdfplumberParser:
    return PdfplumberParser()


def _read(name: str) -> bytes:
    return (OUTPUT_DIR / name).read_bytes()


@pytest.mark.p0
@pytest.mark.parametrize(("filename", "reason"), FIXTURE_REASONS.items())
def test_TC_FAIL_001_each_generated_fixture_raises_its_reason(
    parser: PdfplumberParser, filename: str, reason: str
) -> None:
    """Every unreadable shape fails as a named, user-actionable reason.

    Not just any exception: screen 03c maps ``reason`` to a specific recovery,
    so an uncaught library exception here is a failure the user cannot act on
    (a 500), which is exactly what this gate exists to prevent.
    """
    with pytest.raises(ExtractionFailure) as exc_info:
        parser.parse(_read(filename))

    assert exc_info.value.reason == reason
    assert str(exc_info.value), "the message must say something a user can act on"


def test_encrypted_pdf_with_wrong_password_is_still_password_protected(
    parser: PdfplumberParser,
) -> None:
    """"If password is given, try it first" means attempt it, not trust it.

    A wrong password must fail exactly like no password at all, not raise a
    raw decryption error from the underlying library.
    """
    with pytest.raises(ExtractionFailure) as exc_info:
        parser.parse(_read("encrypted.pdf"), password="not-the-password")

    assert exc_info.value.reason == "password_protected"


def test_scanned_pdf_is_rejected_not_sent_to_ocr(parser: PdfplumberParser) -> None:
    """ADR-002: an image with no text layer must be refused, never guessed.

    An OCR misread of an amount passes every type check and ties to nothing,
    which corrupts a month silently. There is no wrong-but-plausible output
    to assert against here — the whole point is that none is produced, only
    the same ``no_text_layer`` failure as the primary case above.
    """
    with pytest.raises(ExtractionFailure) as exc_info:
        parser.parse(_read("scanned.pdf"))

    assert exc_info.value.reason == "no_text_layer"


def test_malformed_bytes_raise_extraction_failure_not_a_bare_exception(
    parser: PdfplumberParser,
) -> None:
    """A truncated download or a renamed file must not crash the worker.

    Pinned separately from the parametrized case above because this is the
    fixture most likely to leak a raw parsing exception (a struct-unpacking
    or EOF error) if the library call is not wrapped.
    """
    try:
        parser.parse(_read("malformed.pdf"))
    except ExtractionFailure as exc:
        assert exc.reason == "parser_error"
    else:
        pytest.fail("expected ExtractionFailure, but parse() returned normally")


def test_completely_empty_bytes_are_a_parser_error(parser: PdfplumberParser) -> None:
    """Not one of the four generated fixtures, but the same failure shape as
    malformed: zero bytes is not a PDF either, and must not crash the worker."""
    with pytest.raises(ExtractionFailure) as exc_info:
        parser.parse(b"")

    assert exc_info.value.reason == "parser_error"
