"""Parser selection.

A thin router, deliberately. Monopoly's own configurations do the real bank
detection, so this only decides which strategy is asked and records which one
answered, because ``parser`` ends up on the statement and in the logs and is
the first thing worth knowing when an extraction looks wrong.

``monopoly-core`` is a required dependency (ADR-001) and the only parser wired
in by default: a PDF whose bank it does not recognise is rejected outright
(``not_a_statement``) rather than handed to a generic fallback -- extracting
*something* from a bank Monopoly was never configured for is a worse failure
than a named rejection the user can act on.

:class:`~app.extract.pdfplumber_parser.PdfplumberParser` still exists,
implementing the same :class:`~app.extract.base.StatementParser` protocol, as
the documented AGPL exit (ADR-013): swapping it in here (or adding it back as
a second entry) is a one-line change to the ``parsers`` list, not a rewrite of
this file or of anything that calls it.
"""

from __future__ import annotations

from app.extract.base import ExtractionFailure, StatementParser
from app.extract.monopoly_parser import MonopolyParser


class ParserRegistry:
    def __init__(self, parsers: list[StatementParser] | None = None) -> None:
        self._parsers = parsers if parsers is not None else [MonopolyParser()]

    def select(self, *, institution: str | None, pdf_bytes: bytes) -> StatementParser:
        """First parser that claims the document.

        No parser claiming a document is a real, expected outcome now -- an
        unsupported bank -- not the impossible case it was when a fallback
        parser sat at the end of the list claiming everything. Raised as the
        same :class:`~app.extract.base.ExtractionFailure` a parser itself
        would raise, so the worker's single ``except ExtractionFailure``
        handles both.
        """
        for parser in self._parsers:
            if parser.supports(institution=institution, pdf_bytes=pdf_bytes):
                return parser
        raise ExtractionFailure("not_a_statement", "No configured parser recognised this document.")
