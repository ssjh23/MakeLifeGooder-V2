"""Parsing a statement PDF into rows.

One of only two Protocols in the worker. Both exist because the project has
committed to swapping the implementation behind them: a new bank parser here, a
different model provider in the classifier. Everything else is concrete, since
an interface with one implementation is speculative generality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


class ExtractionFailure(Exception):
    """Parsing failed in a way the user can be told about.

    ``reason`` matches the four cases screen 03c handles, each of which has a
    named recovery. A failure the user cannot act on is just a 500.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ParsedRow:
    posted_on: date
    description_raw: str
    #: Signed minor units. Never a float, at any point, including here.
    amount_minor: int
    currency: str = "SGD"
    row_last4: str | None = None
    page: int | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedStatement:
    """What a parser returns.

    ``printed_total_minor`` is ground truth. It is read from the document, not
    computed from the rows, which is the entire basis of reconciliation: if the
    total were derived from the rows it would always agree with them and would
    prove nothing.
    """

    rows: list[ParsedRow]
    printed_total_minor: int | None
    currency: str
    period_start: date | None
    period_end: date | None
    detected_last4: str | None
    has_text_layer: bool
    parser: str
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


class StatementParser(Protocol):
    """A strategy for one family of bank layouts."""

    name: str

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        """Whether this parser claims the document."""
        ...

    def parse(self, pdf_bytes: bytes, *, password: str | None = None) -> ParsedStatement:
        """Extract rows and the printed total.

        Must raise :class:`ExtractionFailure` rather than returning a partial
        result. A half-parsed statement that reaches the reconciler looks like a
        statement that does not balance, which sends the user to fix rows by
        hand for a problem that was never theirs.

        Must be deterministic. The same PDF has to produce identical rows on
        every run, or the fixture tests are meaningless and the no-aggregation
        -error criterion is untestable rather than merely hard (TC-IMP-010).
        This is the reason extraction is not a model's job.
        """
        ...
