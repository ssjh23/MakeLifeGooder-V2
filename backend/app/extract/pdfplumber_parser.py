"""pdfplumber fallback parser.

Kept for banks Monopoly does not cover, and as the documented exit from the
AGPL obligation Monopoly brings with it (ADR-013). That exit only stays open if
this path is real, so it is a supported parser rather than dead code.

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

from app.extract.base import ParsedStatement


class PdfplumberParser:
    """Generic column-inference parser."""

    name = "pdfplumber"

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        """Always true. This is the last resort, and something must claim the
        document so the failure is a named extraction failure the user can act
        on rather than "no parser matched"."""
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
        raise NotImplementedError

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
