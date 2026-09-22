"""Parser selection. See app/extract/registry.py.

Routing is tested against stub parsers, not real ones, so it is a test of the
loop in ``select()`` and nothing else. The one integration-shaped case at the
bottom checks that the real default (Monopoly-only, reject-outright) actually
behaves that way end to end.
"""

from __future__ import annotations

import pytest

from app.extract.base import ExtractionFailure
from app.extract.monopoly_parser import MonopolyParser
from app.extract.registry import ParserRegistry
from tests.fixtures.generate import OUTPUT_DIR
from tests.fixtures.generate import main as generate_fixtures


class _StubParser:
    def __init__(self, name: str, *, claims: bool) -> None:
        self.name = name
        self._claims = claims

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        return self._claims

    def parse(self, pdf_bytes: bytes, *, password: str | None = None):  # pragma: no cover
        raise NotImplementedError


def test_default_registry_uses_monopoly_only() -> None:
    registry = ParserRegistry()
    assert [type(p) for p in registry._parsers] == [MonopolyParser]


def test_select_returns_the_first_parser_that_claims_the_document() -> None:
    first = _StubParser("first", claims=False)
    second = _StubParser("second", claims=True)
    third = _StubParser("third", claims=True)
    registry = ParserRegistry(parsers=[first, second, third])

    assert registry.select(institution=None, pdf_bytes=b"anything") is second


@pytest.mark.p0
def test_select_rejects_when_no_parser_claims_the_document() -> None:
    """No fallback claims everything any more: an unsupported bank is now a
    real, expected outcome, raised the same way a parser's own rejection
    would be so the worker's one ExtractionFailure handler covers both."""
    registry = ParserRegistry(parsers=[_StubParser("only", claims=False)])

    with pytest.raises(ExtractionFailure) as exc_info:
        registry.select(institution=None, pdf_bytes=b"anything")

    assert exc_info.value.reason == "not_a_statement"


@pytest.fixture(scope="session", autouse=True)
def _generated_fixtures() -> None:
    generate_fixtures()


@pytest.mark.p0
def test_the_real_default_registry_rejects_an_unsupported_bank() -> None:
    """Same fixture test_monopoly_parser.py uses directly on the parser,
    exercised here through the registry's own routing instead."""
    registry = ParserRegistry()
    pdf_bytes = (OUTPUT_DIR / "not_a_statement.pdf").read_bytes()

    with pytest.raises(ExtractionFailure) as exc_info:
        registry.select(institution=None, pdf_bytes=pdf_bytes)

    assert exc_info.value.reason == "not_a_statement"
