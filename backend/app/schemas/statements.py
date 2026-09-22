"""Import payloads. Screens 03 to 03d."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, SecretStr

from app.schemas.common import CurrencyCode, Last4, MoneyStr, Period, Reconciliation, Schema

StatementStatusLiteral = Literal["pending", "processing", "needs_review", "ready", "failed"]
FailureReasonLiteral = Literal[
    "password_protected", "no_text_layer", "not_a_statement", "parser_error"
]
#: Distinct from ``StatementStatusLiteral``, which already means something
#: else during extraction (``processing``/``needs_review``). This tracks the
#: post-commit classify/aggregate pipeline instead, via ``ProcessingJob``.
#: ``None`` means "not applicable" -- not yet committed.
ClassificationStatusLiteral = Literal["in_progress", "done", "failed"]


class UploadUrlRequest(Schema):
    content_type: Literal["application/pdf"] = "application/pdf"
    size_bytes: int = Field(gt=0)


class UploadUrlResponse(Schema):
    """A presigned permission to write one object.

    The URL is a bearer credential: anyone holding it can write that key until
    it expires. It is never logged (ADR-014), and the key is chosen by the
    server rather than accepted from the client.
    """

    upload_id: str
    url: str
    expires_at: datetime
    required_headers: dict[str, str]


class StatementRegister(Schema):
    upload_id: str
    #: Optional here, required before commit. Extraction may identify the card
    #: from the PDF header, and the user confirms it either way.
    card_id: uuid.UUID | None = None


class ExtractionReport(Schema):
    """The four facts screen 03a shows.

    That screen reports rather than spins, because the next screen's behaviour
    depends on exactly these values and a user who can see them can tell a slow
    import from a stuck one.
    """

    has_text_layer: bool
    card_identified: str | None = None
    period: Period | None = None
    printed_total: MoneyStr | None = None
    currency: CurrencyCode | None = None
    rows_extracted: int
    parser: str | None = None


class StatementFailure(Schema):
    reason: FailureReasonLiteral
    message: str


class StatementResponse(Schema):
    id: uuid.UUID
    status: StatementStatusLiteral
    card_id: uuid.UUID | None = None
    period: Period | None = None
    #: The statement's closing date alone -- unlike ``period``, this is set
    #: whenever the parser found *a* date at all. Most bank formats only ever
    #: print a closing date, not an explicit period start, so ``period``
    #: (which needs both bounds) stays null far more often than a statement
    #: actually has a month it belongs to. This is what screen 04's picker
    #: shows next to a statement's id.
    statement_month: date | None = None
    #: Exposed so a stuck statement can be traced from a support conversation
    #: without database access.
    trace_id: str | None = None
    request_id: str | None = None
    extraction: ExtractionReport | None = None
    failure: StatementFailure | None = None
    reconciliation: Reconciliation | None = None
    #: Where classification stands in the background pipeline, so the review
    #: board can distinguish "still classifying" from "nothing matched".
    #: ``None`` before commit, when classification has not been enqueued yet.
    classification_status: ClassificationStatusLiteral | None = None
    #: True while any counted row on this statement still has no category --
    #: the same condition that blocks ``POST .../review/finish``. ``False``
    #: pre-commit and once every row is classified, so the statement picker
    #: (screen 04) can separate "still needs a decision" from a statement
    #: someone already finished reviewing, rather than listing every
    #: committed statement forever with no way to tell them apart.
    needs_review: bool = False
    uploaded_at: datetime


class StatementTag(Schema):
    card_id: uuid.UUID


class DeleteStatementRequest(Schema):
    """Removes a statement outright, whether it's been committed or not.

    Pre-commit this is indistinguishable from ``/discard`` -- the ledger ends
    up exactly as if the statement never existed, so ``confirm`` is not
    required. Once committed, real spending history disappears and every
    month the statement touched is recalculated, so ``confirm`` must be
    explicit (TC-CARD-010's reasoning applies here too: the irreversible path
    should not be reachable by a request that merely forgot a field).
    """

    confirm: bool = False


class UnlockRequest(Schema):
    """Used once, in memory, then discarded.

    Never stored, never logged, never written to a job payload (TC-FAIL-004).
    This model exists partly as documentation of that: the password has exactly
    one path through the system and it ends at the parser.
    """

    password: SecretStr


class ManualEntryRequest(Schema):
    """Opens hand entry for a scan.

    The user supplies the printed total, which then becomes the figure their
    typed rows must tie to. Reconciliation still applies; the difference is
    only where the total came from.
    """

    printed_total: MoneyStr
    currency: CurrencyCode = "SGD"
    period: Period


class RowCreate(Schema):
    posted_on: date
    description: str = Field(max_length=500)
    amount: MoneyStr


class RowUpdate(Schema):
    posted_on: date | None = None
    description: str | None = Field(default=None, max_length=500)
    amount: MoneyStr | None = None


class RowResponse(Schema):
    id: uuid.UUID
    posted_on: date
    description: str
    amount: MoneyStr
    currency: CurrencyCode
    skipped: bool = False
    deleted: bool = False
    page: int | None = None
    line: int | None = None


class RowsResponse(Schema):
    rows: list[RowResponse]
    reconciliation: Reconciliation


class RowMutationResponse(Schema):
    """Every row change returns the live reconciliation state.

    So the countdown on screen 03b updates from this response rather than a
    refetch per keystroke.
    """

    row: RowResponse | None = None
    reconciliation: Reconciliation


class CommitRequest(Schema):
    """The gate.

    Committing without ``accept_gap`` when the rows do not tie returns 422.
    That is the enforcement; the disabled button is a courtesy (TC-REC-003).

    ``accept_gap`` permanently marks the statement unreconciled and the gap is
    then carried in every view of that month, which is why a reason is required
    with it (TC-REC-011).
    """

    accept_gap: bool = False
    gap_reason: str | None = Field(default=None, max_length=500)


class ResolveDuplicateRequest(Schema):
    action: Literal["replace", "keep_both", "cancel"]
    #: Required for keep_both: two statements for the same period must belong
    #: to different cards, or the uniqueness rule is simply broken twice.
    target_card_id: uuid.UUID | None = None
