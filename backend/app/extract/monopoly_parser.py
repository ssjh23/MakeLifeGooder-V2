"""monopoly-core parser.

The primary parser (ADR-001). Monopoly already carries tested configurations
for DBS, POSB, OCBC, UOB, Standard Chartered, HSBC, Citibank, Maybank and
Trust, which is effectively the whole target market, and it handles
password-protected files natively. Adopting it removed weeks of per-bank parser
work from the roadmap.

It also ships a total-validation check, which is our reconciler already built.
The wrapper in ``reconcile.py`` adds the gap-recording flow that screen 03b
needs; it does not reimplement the arithmetic.

The import is lazy and guarded. monopoly-core pulls system packages that do not
install cleanly everywhere, and it is an optional extra so that ``uv sync``
always succeeds and the scaffold always runs. When it is missing the registry
falls back to pdfplumber and says so, rather than the process failing at import
time with a stack trace about poppler.

===========================================================================
BUILD STEP 2.2   depends on: 0.1 money   blocked on: your fixture PDFs
Verify: uv run pytest tests/integration/test_parsers.py
===========================================================================
Install the extra first: ``uv sync --extra parsers``.

Nothing after this step depends on it. Later steps seed rows directly, so an
absent fixture blocks only this file.
"""

from __future__ import annotations

from app.extract.base import ExtractionFailure, ParsedStatement


def is_available() -> bool:
    try:
        import monopoly  # noqa: F401
    except ImportError:
        return False
    return True


class MonopolyParser:
    """Wraps monopoly-core's bank detection and extraction."""

    name = "monopoly"

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        """Whether Monopoly recognises the document.

        TODO:
          1. Ask Monopoly to detect the bank from the document itself.
          2. Treat ``institution`` as a hint only. A user can register a card
             under the wrong issuer; the PDF cannot be wrong about which bank
             produced it.
          3. Return ``False`` rather than raising when it does not recognise
             the document, so the registry falls through to pdfplumber.
        """
        raise NotImplementedError

    def parse(self, pdf_bytes: bytes, *, password: str | None = None) -> ParsedStatement:
        """Run Monopoly and map its result onto :class:`ParsedStatement`.

        TODO:
          1. Hand the bytes and any password to Monopoly and let it pick its
             per-bank configuration.
          2. Map its rows onto :class:`~app.extract.base.ParsedRow`, converting
             amounts through :meth:`_to_minor` at this boundary.
          3. Carry Monopoly's own total-validation outcome through rather than
             recomputing it, so our reconciler and its safety check cannot
             disagree (TC-REC-013).
          4. Record ``parser`` as ``monopoly:<bank>``. It lands on the statement
             and in the logs, and is the first thing worth knowing when an
             extraction looks wrong.
          5. Translate its exceptions into :class:`ExtractionFailure` with
             ``password_protected``, ``no_text_layer``, ``not_a_statement`` or
             ``parser_error``. A library exception reaching the worker becomes a
             failure the user cannot act on.
        """
        raise NotImplementedError

    def _to_minor(self, amount: object, currency: str) -> int:
        """Convert one of Monopoly's amounts to signed minor units.

        TODO:
          1. Delegate to :func:`app.money.to_minor`.
          2. If Monopoly hands back a ``float``, convert via ``str`` rather than
             directly, or the precision error is baked in at the boundary.
          3. Preserve the sign convention: a refund stays negative.
        """
        raise NotImplementedError


__all__ = ["ExtractionFailure", "MonopolyParser", "is_available"]
