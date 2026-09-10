"""Parser selection.

A thin router, deliberately. Monopoly's own configurations do the real bank
detection, so this only decides which strategy is asked and records which one
answered, because ``parser`` ends up on the statement and in the logs and is
the first thing worth knowing when an extraction looks wrong.
"""

from __future__ import annotations

from app.extract.base import StatementParser
from app.extract.monopoly_parser import MonopolyParser, is_available
from app.extract.pdfplumber_parser import PdfplumberParser
from app.telemetry import get_logger

logger = get_logger(__name__)


class ParserRegistry:
    def __init__(self, parsers: list[StatementParser] | None = None) -> None:
        if parsers is not None:
            self._parsers = parsers
            return

        self._parsers = []
        if is_available():
            self._parsers.append(MonopolyParser())
        else:
            # Said out loud rather than silently degrading. Running the whole
            # suite against the fallback would make the SG bank coverage look
            # worse than it is and hide the real cause.
            logger.warning("parser.monopoly_unavailable", reason="not_installed")
        self._parsers.append(PdfplumberParser())

    def select(self, *, institution: str | None, pdf_bytes: bytes) -> StatementParser:
        """First parser that claims the document. pdfplumber always claims."""
        for parser in self._parsers:
            if parser.supports(institution=institution, pdf_bytes=pdf_bytes):
                return parser
        raise RuntimeError("No parser claimed the document, which should be impossible.")
