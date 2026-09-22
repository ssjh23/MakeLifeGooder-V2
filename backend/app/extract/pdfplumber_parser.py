"""pdfplumber parser -- the AGPL exit, not the default.

``ParserRegistry`` (ADR-001) now wires in only :class:`~app.extract.monopoly_parser.MonopolyParser`,
and rejects a PDF outright when Monopoly does not recognise its bank, rather
than handing it to a generic fallback. This class is not registered by
default. It exists as the documented exit from the AGPL obligation Monopoly
brings with it (ADR-013): dropping ``monopoly-core`` is a one-line change to
the ``parsers`` list passed into ``ParserRegistry``, not a rewrite, and that
exit only stays open if this path is real, so it is a supported parser rather
than dead code -- it implements the same :class:`~app.extract.base.StatementParser`
protocol Monopoly's adapter does.

Pure Python, so it installs anywhere and always runs.

===========================================================================
BUILD STEP 2.1 (failure detection)   depends on: nothing
Verify: uv run pytest tests/unit/extract/test_parser_failures.py
---------------------------------------------------------------------------
BUILD STEP 2.2 (row extraction)      depends on: 0.1 money
Verify: uv run pytest tests/integration/test_parsers.py
===========================================================================
Deliberately split, because the two halves have different prerequisites.

Failure detection needs only the four generated fixtures, so build it now:

    uv run python -m tests.fixtures.generate

Row extraction needs real bank statements, which is the one input that cannot
be generated. **Nothing after this depends on 2.2.** Later steps seed rows
directly into the database, so if the PDFs are not ready, skip ahead and come
back to it.
"""

from __future__ import annotations

import io
import re
from typing import Final

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.extract.base import ExtractionFailure, ParsedStatement

#: Mirrors MonopolyParser's MIN_TEXT_LENGTH, so the same shape of statement is
#: judged the same way regardless of which parser sees it.
MIN_TEXT_LENGTH: Final = 10

#: A decimal amount, the shape every transaction row ends in. Its total
#: absence from the document is what distinguishes a readable PDF with no
#: transaction table from a genuine statement, without needing the column
#: inference that row extraction (step 2.2) requires.
_AMOUNT_PATTERN: Final = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}")


class PdfplumberParser:
    """Generic column-inference parser."""

    name = "pdfplumber"

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        """Always true. If this is ever the only registered parser again (the
        AGPL exit taken), something must claim every document so the failure
        is a named extraction failure the user can act on, rather than
        ``ParserRegistry`` rejecting it as "no parser matched"."""

        return True

    def parse(self, pdf_bytes: bytes, *, password: str | None = None) -> ParsedStatement:
        """Extract rows and the printed total.

        TODO for step 2.1, failure detection. Write the test first:
        ``tests/unit/extract/test_parser_failures.py``, four cases, one per
        generated fixture, each asserting the right reason.

          1. Detect an encrypted PDF and raise ``ExtractionFailure`` with
             ``password_protected``. If ``password`` is given, try it first.
          2. Detect a page with no text layer and raise ``no_text_layer``. Do
             **not** call OCR. Character errors in amounts pass every type
             check, tie to nothing, and silently corrupt a month (ADR-002).
          3. Detect a readable PDF with no transaction table and raise
             ``not_a_statement``. The interesting one, because nothing is wrong
             with the file and only the content can reject it.
          4. Catch malformed bytes and raise ``parser_error`` rather than
             letting a library exception escape.

        TODO for step 2.2, row extraction:

          5. Extract words with positions and call :meth:`_infer_columns`.
          6. Build a :class:`~app.extract.base.ParsedRow` per line, converting
             amounts with :func:`app.money.to_minor` **here**, at the boundary.
             Nothing downstream should ever see a float.
          7. Read the printed total from the document. Never compute it from
             the rows: a total derived from the rows always agrees with them
             and proves nothing, which would make reconciliation meaningless.
          8. Read the period and the last four digits from the header.
          9. Confirm determinism by parsing one fixture three times and
             comparing (TC-IMP-010).
        """
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
        except PdfReadError as exc:
            raise ExtractionFailure("parser_error", f"Could not open document: {exc}") from exc
        except Exception as exc:
            raise ExtractionFailure("parser_error", f"Could not open document: {exc}") from exc

        if reader.is_encrypted and not reader.decrypt(password or ""):
            raise ExtractionFailure("password_protected", "The document is password protected.")

        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as exc:
            raise ExtractionFailure("parser_error", f"Could not read document: {exc}") from exc

        if len(text.strip()) < MIN_TEXT_LENGTH:
            raise ExtractionFailure("no_text_layer", "No selectable text found on any page.")

        if not _AMOUNT_PATTERN.search(text):
            raise ExtractionFailure(
                "not_a_statement", "No transaction table found in this document."
            )

        raise NotImplementedError(
            "Row extraction needs a real bank statement (BUILD STEP 2.2)."
        )

    def _infer_columns(self, words: list[dict[str, object]]) -> dict[str, tuple[float, float]]:
        """Group words into date, description and amount columns by x position.

        The hard part of a generic parser, and the reason Monopoly's per-bank
        configurations are worth adopting rather than competing with.

        TODO:
          1. Cluster word x-positions to find column boundaries.
          2. Identify the amount column from the right edge: amounts are
             right-aligned on essentially every statement.
          3. Identify the date column from the left edge and a date shape.
          4. Treat everything between them as the description.
          5. Expect to iterate. Start with one bank and widen only when a second
             one forces it.
        """
         
        raise NotImplementedError
