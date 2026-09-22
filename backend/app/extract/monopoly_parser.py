"""monopoly-core parser.

The primary, and only default, parser (ADR-001). Monopoly already carries
tested configurations for DBS, POSB, OCBC, UOB, Standard Chartered, HSBC,
Citibank, Maybank and Trust, which is effectively the whole target market, and
it handles password-protected files natively. Adopting it removed weeks of
per-bank parser work from the roadmap. ``monopoly-core`` is a required
dependency, not an optional extra: there is no "not installed" fallback path
to guard, so this module imports it eagerly like anything else.

Detection is Monopoly's own, not ours. :meth:`supports`/:meth:`parse` ask
:class:`~monopoly.banks.BankDetector` whether any of Monopoly's ~20 bank
configs recognise the document, and reject outright when none do, rather than
handing an unsupported bank to a generic parser that would only guess. The
``banks`` list is injectable (defaults to :data:`monopoly.banks.banks`)
precisely so a test can drive detection and extraction against a small
in-repo bank config instead of a real, redacted statement.

Four things this module has to get right that are easy to get wrong by only
reading the Protocol this implements:

* **Detection needs the document unlocked first.** A :class:`TextIdentifier`
  matches against extracted text, which an encrypted, not-yet-authenticated
  document does not have. :meth:`parse` unlocks before detecting; ``supports``
  has no password to unlock with, so it defers to ``parse`` for an encrypted
  document rather than risk a false "unsupported" on a bank it cannot yet see.
* **The "no text layer" check has to run before detection, not after.** A
  scanned statement produces empty text for every bank's identifiers, which
  looks identical to "not a statement Monopoly covers" unless the no-text-layer
  case is ruled out first. Confirmed against the four generated failure-shape
  fixtures: swap the order and the scanned fixture reports the wrong reason.
* **Never trust an ambient password.** ``monopoly.pdf.PdfDocument`` defaults to
  reading a ``PDF_PASSWORDS`` environment variable or a ``.env`` file when no
  ``passwords`` argument is given. That default exists for Monopoly's own CLI;
  here it would mean one shared, ambient secret is tried against every user's
  upload. A password list is passed explicitly on every call, empty string
  when there is none, so nothing ambient is ever consulted.
* **Monopoly's sign convention is the opposite of ours.** Confirmed by running
  Monopoly's own bundled example statement: an ordinary, unmarked purchase
  comes back as a *negative* ``Transaction.amount``, and only an explicit
  credit marker (a refund, a payment) is positive. ``app/money.py``'s
  convention is the other way round -- a spend is positive, "a refund is
  negative" -- so :meth:`_to_minor` inverts the sign at this boundary. Getting
  this backwards would not raise anywhere; it would silently flip every user's
  spend and income. See ``test_ordinary_purchase_is_positive_and_refund_is_negative``.

===========================================================================
BUILD STEP 2.2   depends on: 0.1 money
Verify: uv run pytest tests/unit/extract/test_monopoly_parser.py
===========================================================================
The row-mapping half of this step no longer needs a real bank-statement PDF to
be exercised: Monopoly ships its own example bank and statement, and
``PdfParser.from_pages`` builds a parser straight from text, bypassing PDF
loading entirely. Both are used in the test file above.

What genuinely still needs a real, redacted statement (``tests/fixtures/pdfs/banks/``,
currently empty -- see its manifest) is confirming that a *real* bank's regex
configuration in Monopoly actually matches *our* real PDFs end to end, and that
the sign convention documented above holds for every bank we ship, not only
the bundled example. TC-IMP-009 is that test, and it is still blocked on those
files.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

from monopoly.banks import BankDetector
from monopoly.banks import banks as default_banks
from monopoly.exceptions import (
    MissingHeaderError,
    MissingStatementDateError,
    NoTransactionsFoundError,
)
from monopoly.pdf import (
    BadPasswordFormatError,
    MissingOCRError,
    MissingPasswordError,
    PdfDocument,
    PdfParser,
    WrongPasswordError,
)
from monopoly.pipeline import Pipeline
from monopoly.statements import SafetyCheckError
from pydantic import SecretStr

from app.extract.base import ExtractionFailure, ParsedRow, ParsedStatement
from app.money import DEFAULT_CURRENCY, sum_minor, to_minor

if TYPE_CHECKING:
    from monopoly.banks.base import BankBase

#: Mirrors ``monopoly.pdf.MIN_OCR_TEXT_LENGTH``: below this many characters of
#: extracted text, a page is treated as having no selectable text at all.
#: Checked once, document-wide, before detection -- so a scanned statement is
#: never mistaken for "not a bank Monopoly covers" (see module docstring).
MIN_TEXT_LENGTH: Final = 10


class MonopolyParser:
    """Wraps monopoly-core's bank detection and extraction."""

    name = "monopoly"

    def __init__(self, banks: list[type[BankBase]] | None = None) -> None:
        """``banks`` defaults to Monopoly's real, shipped bank list.

        Injectable so a test can pass a small in-repo bank config and exercise
        real detection without a real bank statement.
        """
        self._banks = banks if banks is not None else default_banks

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        """Whether Monopoly recognises the document.

        ``institution`` is deliberately ignored: it is a hint from how the
        user registered their card, and a user can register a card under the
        wrong issuer. The PDF cannot be wrong about which bank produced it, so
        detection is content-only, the same detection :meth:`parse` uses.

        Returns ``True`` (rather than the real answer) whenever the honest
        answer needs information or a check this method cannot perform on its
        own: an encrypted document with no password to try, bytes that fail to
        open at all, or a document with no text layer. All three are deferred
        to :meth:`parse`, which either has the password or raises the
        specific reason directly, instead of this method collapsing every one
        of them into a bare, less useful "unsupported". Skipping the text-layer
        check here would be a real bug, not just an imprecise one: a scanned
        statement has no text for any bank's identifiers to match, which looks
        identical to "not a bank Monopoly covers" unless ruled out first --
        and if ``supports`` says no, :meth:`parse` never runs to say otherwise.
        """
        try:
            document = PdfDocument(file_bytes=pdf_bytes, passwords=[SecretStr("")])
        except Exception:
            return True

        if document.is_encrypted:
            return True

        if len(document.raw_text.strip()) < MIN_TEXT_LENGTH:
            return True

        return BankDetector(document).detect_bank(self._banks) is not None

    def parse(self, pdf_bytes: bytes, *, password: str | None = None) -> ParsedStatement:
        """Run Monopoly and map its result onto :class:`ParsedStatement`.

        Every exception Monopoly can raise here is translated to one of the
        four named reasons the rest of the system understands
        (``password_protected``, ``no_text_layer``, ``not_a_statement``,
        ``parser_error``); nothing from the library escapes uncaught.
        """
        passwords = [SecretStr(password)] if password else [SecretStr("")]

        try:
            document = PdfDocument(file_bytes=pdf_bytes, passwords=passwords)
            document.unlock_document()
        except (WrongPasswordError, MissingPasswordError, BadPasswordFormatError) as exc:
            raise ExtractionFailure("password_protected", str(exc)) from exc
        except Exception as exc:
            raise ExtractionFailure("parser_error", f"Could not open document: {exc}") from exc

        try:
            if len(document.raw_text.strip()) < MIN_TEXT_LENGTH:
                raise ExtractionFailure("no_text_layer", "No selectable text found on any page.")

            bank = BankDetector(document).detect_bank(self._banks)
            if bank is None:
                raise ExtractionFailure(
                    "not_a_statement", "No bank recognised by monopoly-core matched this document."
                )

            parser = PdfParser(bank, document)
            pipeline = Pipeline(parser, passwords=passwords)
            # safety_check=False: run it ourselves afterwards so a failure
            # degrades to an unreconciled statement (the ordinary, expected
            # path through screen 03b) rather than losing the extracted rows.
            statement = pipeline.extract(safety_check=False)
            print(statement)
            transactions = pipeline.transform(statement)
        except ExtractionFailure:
            raise
        except MissingOCRError as exc:
            raise ExtractionFailure("no_text_layer", str(exc)) from exc
        except (
            NoTransactionsFoundError,
            MissingHeaderError,
            MissingStatementDateError,
            ValueError,
        ) as exc:
            raise ExtractionFailure("not_a_statement", str(exc)) from exc
        except Exception as exc:
            raise ExtractionFailure("parser_error", str(exc)) from exc

        reconciled = True
        try:
            statement.perform_safety_check()
        except SafetyCheckError:
            reconciled = False

        currency = statement.config.currency or DEFAULT_CURRENCY
        rows = [
            ParsedRow(
                posted_on=date.fromisoformat(tx.date),
                description_raw=tx.description,
                amount_minor=self._to_minor(tx.amount, tx.currency or currency),
                currency=tx.currency or currency,
                row_last4=tx.account,
            )
            for tx in transactions
        ]
        extracted_total_minor = sum_minor([row.amount_minor for row in rows])

        return ParsedStatement(
            rows=rows,
            # Carrying Monopoly's own safety check through rather than
            # recomputing one of our own is deliberate (TC-REC-013): our
            # Reconciler still runs downstream and cannot disagree with a
            # total we derived from its own input. `None` on failure, not the
            # summed rows, so an unreconciled statement stays unreconciled
            # rather than trivially agreeing with itself.
            printed_total_minor=extracted_total_minor if reconciled else None,
            currency=currency,
            period_start=statement.period_start.date() if statement.period_start else None,
            period_end=statement.statement_date.date(),
            detected_last4=statement.account,
            has_text_layer=True,
            parser=f"monopoly:{bank.name}",
            page_count=document.page_count,
            warnings=[] if reconciled else ["safety_check_failed"],
        )

    def _to_minor(self, amount: float, currency: str) -> int:
        """Convert one of Monopoly's amounts to signed minor units.

        Two conversions happen here, not one:

        1. Sign inversion. Monopoly signs a plain, unmarked amount negative
           (its statement-arithmetic convention) and an explicit credit marker
           positive; ours is the reverse (a spend is positive, a refund is
           negative -- see ``app/money.py``). See the module docstring for how
           this was confirmed rather than assumed.
        2. ``str(amount)`` rather than the raw ``float``, so the conversion
           goes through :class:`decimal.Decimal` from the same text a person
           would read, not through whatever binary approximation the float
           happens to hold.
        """
        return -to_minor(str(amount), currency)


__all__ = ["ExtractionFailure", "MonopolyParser"]
