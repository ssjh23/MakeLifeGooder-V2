"""Import and reconciliation.

The commit gate lives here, in the service, not in the router and not in the
client. A disabled button is a courtesy to a person using the interface; it is
not enforcement, and TC-REC-003 calls the endpoint directly to prove it.

===========================================================================
BUILD STEPS 4.1, 4.3 and 4.4 live in this file.

  4.1 upload url + register   depends on: 3.1
  4.3 row mutations           depends on: 1.1, 3.2
  4.4 the commit gate         depends on: 1.1, 3.1

Verify all three with: uv run pytest tests/integration/test_import_flow.py
===========================================================================
After 4.1 you get the first real milestone. Start the API, the worker and the
frontend, drop a PDF on the import screen, and watch it reach ``failed`` with
one ``trace_id`` shared across the API and worker logs. That is the whole
architecture proven before any extraction exists.

After 4.4 a statement can be committed, ``has_statements`` flips, and the
frontend navigation unlocks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.aggregate.refresh import AggregateRefresher
from app.api.errors import AlreadyImported, DuplicateFileUpload, LedgerError, NotFound
from app.classify.normalise import normalise
from app.db.models import FailureReason, JobStatus, Statement, StatementStatus, Transaction
from app.db.repositories import ProcessingJobRepository, StatementRepository, TransactionRepository
from app.db.session import current_tenant_id
from app.extract.base import ParsedRow, ParsedStatement
from app.extract.dedupe import DedupeGuard
from app.extract.reconcile import Reconciler, ReconcileResult
from app.money import to_decimal_string, to_minor
from app.schemas.common import Period, Reconciliation
from app.schemas.statements import (
    ClassificationStatusLiteral,
    ExtractionReport,
    RowMutationResponse,
    RowResponse,
    RowsResponse,
    StatementFailure,
    StatementResponse,
    UploadUrlResponse,
)
from app.storage import ObjectStore, UploadRejected, derive_object_key
from app.telemetry import current_request_id, current_traceparent, events, get_logger
from app.worker.queue import enqueue
from app.worker.tasks import classify_statement, extract_statement

#: One line per :class:`~app.db.models.FailureReason`, shown on screen 03c.
FAILURE_MESSAGES: dict[FailureReason, str] = {
    FailureReason.PASSWORD_PROTECTED: "This PDF is password protected.",
    FailureReason.NO_TEXT_LAYER: "This PDF has no selectable text -- it looks like a scan.",
    FailureReason.NOT_A_STATEMENT: "This file was not recognised as a bank statement.",
    FailureReason.PARSER_ERROR: "This file could not be read.",
}

logger = get_logger(__name__)


class StatementService:
    def __init__(self, session: AsyncSession, store: ObjectStore) -> None:
        self._session = session
        self._store = store
        self._statement_repo = StatementRepository(session)
        self._transaction_repo = TransactionRepository(session)
        self._processing_job_repo = ProcessingJobRepository(session)

    # -- Step 4.1 ----------------------------------------------------------

    async def create_upload_url(self, *, content_type: str, size_bytes: int) -> UploadUrlResponse:
        """Issue a presigned PUT.

        The API never receives the bytes, so concurrent uploads are a storage
        concern rather than an API memory concern (ADR-009). The URL itself is
        never logged: it is a bearer credential, and a logged one is a
        writable object handle sitting in the log backend.
        """
        try:
            presigned = self._store.presign_upload(content_type=content_type, size_bytes=size_bytes)
        except UploadRejected as exc:
            # Rejecting here means an unusable object never reaches the bucket
            # at all, rather than surfacing on the register call that follows.
            raise LedgerError(str(exc)) from exc

        return UploadUrlResponse(
            upload_id=presigned.upload_id,
            url=presigned.url,
            expires_at=datetime.fromtimestamp(presigned.expires_at_epoch, tz=UTC),
            required_headers=presigned.required_headers,
        )

    async def register(
        self, *, upload_id: str, card_id: uuid.UUID | None
    ) -> StatementResponse:
        """Record the uploaded object and enqueue extraction.

        Both writes -- the statement row and the queued job -- go through this
        same ``AsyncSession``, whose transaction the caller (the ``/statements``
        request) commits or rolls back as a whole. A crash between the two is
        therefore unrepresentable rather than merely handled: either both
        exist, or neither does (TC-IMP-005, ADR-004).
        """
        object_key = derive_object_key(upload_id)
        if not self._store.exists(key=object_key):
            # A client can call this without having uploaded anything.
            raise LedgerError("Uploaded object does not exist.")

        # Checked here, synchronously, rather than left to extract() deep in
        # the worker: the whole point is to answer "have I seen this exact
        # document" before the person navigates away, not minutes later as
        # an unexplained statement with the right total and zero rows (every
        # row's dedupe_hash already existing from the earlier upload is what
        # produces that). Only needs the raw bytes, so it costs nothing that
        # parsing would. extract()'s own file-hash check stays as a silent
        # backstop for the narrower race of two concurrent registrations of
        # the same file before either finishes.
        pdf_bytes = self._store.get_bytes(key=object_key)
        file_hash = DedupeGuard().statement_hash(pdf_bytes)
        duplicate = await self._statement_repo.find_by_file_hash(file_hash)
        if duplicate is not None:
            logger.info(
                events.DUPLICATE_FILE_DETECTED, existing_statement_id=str(duplicate.id)
            )
            raise DuplicateFileUpload(
                "You've already uploaded this exact file.",
                details={
                    "existing_statement_id": str(duplicate.id),
                    "resolutions": ["replace", "cancel"],
                },
            )

        statement = await self._statement_repo.create(object_key=object_key, card_id=card_id)
        statement.file_sha256 = file_hash

        traceparent = current_traceparent()
        await enqueue(
            self._session,
            extract_statement,
            statement_id=str(statement.id),
            user_id=str(await current_tenant_id(self._session)),
            traceparent=traceparent,
        )

        statement.trace_id = traceparent.split("-")[1] if traceparent else None
        statement.request_id = current_request_id()

        logger.info(
            events.STATEMENT_UPLOAD_REGISTERED,
            statement_id=str(statement.id),
            card_id=str(card_id) if card_id else None,
        )

        return StatementResponse.model_validate(statement)

    async def list(
        self, *, status_filter: str | None = None, card_id: uuid.UUID | None = None
    ) -> list[StatementResponse]:
        """The history table on screen 03."""
        statements = await self._statement_repo.list_for_user(status=status_filter, card_id=card_id)
        return [await self._statement_response(statement) for statement in statements]

    async def tag(self, statement_id: uuid.UUID, *, card_id: uuid.UUID) -> StatementResponse:
        """Assign or change the card a statement belongs to. Pre-commit only:
        a committed statement's card is part of the record it settled."""
        statement = await self._require_uncommitted(statement_id)
        statement.card_id = card_id
        await self._session.flush()
        return await self._statement_response(statement)

    async def get(self, statement_id: uuid.UUID) -> StatementResponse:
        """Status and the extraction report.

        Absent and belonging-to-someone-else both raise NotFound. Row level
        security already makes the second case return nothing, so this does not
        have to remember to check.
        """
        statement = await self._require_statement(statement_id)
        return await self._statement_response(statement)

    # -- Step 4.3 ----------------------------------------------------------

    async def rows(self, statement_id: uuid.UUID) -> RowsResponse:
        """Rows plus live reconciliation state, so screen 03b renders both
        from one request."""
        statement = await self._require_statement(statement_id)
        transactions = await self._transaction_repo.list_for_statement(statement_id)
        result = self._reconcile(statement, transactions)
        return RowsResponse(
            rows=[self._row_response(row) for row in transactions],
            reconciliation=self._reconciliation_response(result),
        )

    async def add_row(
        self,
        statement_id: uuid.UUID,
        *,
        posted_on: date,
        description: str,
        amount: str,
    ) -> RowMutationResponse:
        """Add a row missed by extraction.

        Every row mutation returns the reconciliation, so screen 03b counts
        down from the response the keystroke already produced rather than
        issuing a second request per character.
        """
        statement = await self._require_uncommitted(statement_id)
        amount_minor = to_minor(amount, statement.currency)

        row = Transaction(
            posted_on=posted_on,
            description_raw=description,
            descriptor_key=normalise(description),
            amount_minor=amount_minor,
            currency=statement.currency,
        )
        row.dedupe_hash = self._row_hash(statement, row)
        await self._transaction_repo.add_rows(statement_id, [row])

        return await self._row_mutation_response(statement, row=row)

    async def edit_row(
        self,
        statement_id: uuid.UUID,
        row_id: uuid.UUID,
        *,
        posted_on: date | None = None,
        description: str | None = None,
        amount: str | None = None,
    ) -> RowMutationResponse:
        """Correct a misread row.

        Recomputes ``dedupe_hash`` whenever an identity field moves: the hash
        is a function of exactly the fields this can change, so a stale one
        would silently stop protecting against a genuine future duplicate of
        the *new* content. Re-reconciling on every edit is what re-blocks
        commit on a statement that used to balance (TC-REC-006).
        """
        statement = await self._require_uncommitted(statement_id)
        row = await self._require_row(statement_id, row_id)

        if posted_on is not None:
            row.posted_on = posted_on
        if description is not None:
            row.description_raw = description
            row.descriptor_key = normalise(description)
        if amount is not None:
            row.amount_minor = to_minor(amount, statement.currency)
        row.dedupe_hash = self._row_hash(statement, row)
        await self._session.flush()

        return await self._row_mutation_response(statement, row=row)

    async def _set_exclusion(
        self, statement_id: uuid.UUID, row_id: uuid.UUID, reason: str | None
    ) -> RowMutationResponse:
        statement = await self._require_uncommitted(statement_id)
        row = await self._require_row(statement_id, row_id)
        row.excluded_at = datetime.now(UTC) if reason is not None else None
        row.excluded_reason = reason
        await self._session.flush()
        return await self._row_mutation_response(statement, row=row)

    async def skip_row(self, statement_id: uuid.UUID, row_id: uuid.UUID) -> RowMutationResponse:
        """Exclude a row from the counted total without deleting it.

        Shares ``excluded_at`` with duplicate removal (screen 05): both mean
        "excluded from the total, stays on the record, undoable", and are the
        same fact about a row rather than two.
        """
        return await self._set_exclusion(statement_id, row_id, "skip")

    async def delete_row(self, statement_id: uuid.UUID, row_id: uuid.UUID) -> RowMutationResponse:
        """Soft-exclude a row extraction invented outright.

        Shares ``excluded_at`` with ``skip_row`` -- same fact, different
        reason -- so it is undoable via ``undo_row_exclusion`` exactly like
        a skip is.
        """
        return await self._set_exclusion(statement_id, row_id, "delete")

    async def undo_row_exclusion(
        self, statement_id: uuid.UUID, row_id: uuid.UUID
    ) -> RowMutationResponse:
        """Clears either a skip or a delete. A no-op, not an error, on a row
        that wasn't excluded -- mirrors ``ReviewService.undo_duplicate``'s
        own lenient precedent.
        """
        return await self._set_exclusion(statement_id, row_id, None)

    async def unlock(self, statement_id: uuid.UUID, *, password: str) -> None:
        """Retry extraction with a password.

        Never persisted: used once, passed straight to the re-enqueued job,
        and scrubbed from every job payload for this statement once that
        attempt finishes (``app/worker/tasks.py``, TC-FAIL-004). Never written
        to the statement row or a log line.
        """
        statement = await self._require_statement(statement_id)
        if statement.status != StatementStatus.FAILED:
            raise LedgerError("Only a failed statement can be unlocked.")

        # Continues the original trace (same trace_id, a fresh span) rather
        # than starting a new one, so every retry is still one query away by
        # the trace_id already shown on screen 03c.
        traceparent = (
            f"00-{statement.trace_id}-{uuid.uuid4().hex[:16]}-01"
            if statement.trace_id
            else current_traceparent()
        )

        statement.status = StatementStatus.PENDING
        statement.failure_reason = None

        await enqueue(
            self._session,
            extract_statement,
            statement_id=str(statement.id),
            user_id=str(await current_tenant_id(self._session)),
            traceparent=traceparent,
            password=password,
        )

    # -- Step 4.4, the gate ------------------------------------------------

    async def commit(
        self, statement_id: uuid.UUID, *, accept_gap: bool, gap_reason: str | None
    ) -> StatementResponse:
        """The gate.

        Then add the bypass test: call the endpoint directly on an unreconciled
        statement with the frontend out of the picture, and assert 422
        (TC-REC-003). The disabled button is not the enforcement.

        Nothing enters the ledger unreconciled. An accepted gap is not an
        exception to that: it is recorded permanently and carried in every view
        of that month, so the arithmetic still adds up, visibly, with the
        missing piece named.

        This method is the reason the product can be trusted. Everything else
        is presentation.
        """
        statement = await self._require_statement(statement_id)

        if statement.committed_at is not None:
            raise LedgerError("This statement has already been committed.")

        if accept_gap and not gap_reason:
            raise LedgerError("gap_reason is required when accept_gap is true.")

        if statement.card_id is None:
            # A committed statement that belongs to no card cannot appear on
            # any dashboard band.
            raise LedgerError("A card must be tagged before this statement can be committed.")

        await self._check_not_already_imported(statement)

        transactions = await self._transaction_repo.list_for_statement(statement_id)
        result = self._reconcile(statement, transactions)
        if accept_gap:
            assert gap_reason is not None  # narrowed above
            result = Reconciler().apply_gap(result, reason=gap_reason)

        # Raises NotReconciled (422) unless reconciled or a gap was just
        # accepted; the handler maps it, nothing here has to.
        Reconciler().assert_committable(result, accept_gap=accept_gap)

        # Stamped here, not at extraction: a statement can be uploaded before
        # any card is tagged, and this is the first point a card is
        # guaranteed to exist (the check above). Without it every row's
        # ``card_id`` stays null forever, and AggregateRefresher's own grain
        # (BUILD STEP 7.1) silently excludes every row from every dashboard
        # band -- the exact failure mode the comment above this method
        # already promises cannot happen. ``row_last4`` is meant to beat this
        # per row once something resolves it to a real card (unbuilt), so an
        # already-stamped row is left alone rather than overwritten.
        for row in transactions:
            if row.card_id is None:
                row.card_id = statement.card_id

        statement.reconciled = result.reconciled
        statement.gap_reason = gap_reason if accept_gap else None
        statement.committed_at = datetime.now(UTC)
        statement.status = StatementStatus.READY

        await enqueue(
            self._session,
            classify_statement,
            statement_id=str(statement.id),
            user_id=str(await current_tenant_id(self._session)),
            traceparent=current_traceparent(),
        )

        logger.info(
            events.STATEMENT_COMMIT_SUCCEEDED,
            statement_id=str(statement.id),
            reconciled=result.reconciled,
            difference_nonzero=result.difference_minor != 0,
        )

        return await self._statement_response(statement)

    async def source_page_url(self, statement_id: uuid.UUID, page: int) -> str:
        """Short-lived signed URL for the source PDF.

        Bearer credential. Never logged. There is no per-page split in
        storage -- one PDF, one object -- so ``page`` names where the client's
        own viewer should jump to, not a different object to sign.
        """
        statement = await self._require_statement(statement_id)
        return self._store.presign_download(key=statement.object_key)

    async def discard(self, statement_id: uuid.UUID) -> None:
        """Abandon a pre-commit import. The ledger must be exactly as it was
        before this statement was ever uploaded."""
        statement = await self._require_uncommitted(statement_id)
        await self._transaction_repo.delete_for_statement(statement_id)
        self._store.delete(key=statement.object_key)
        await self._statement_repo.delete(statement)

    async def remove(self, statement_id: uuid.UUID, *, confirm: bool) -> None:
        """Delete a statement outright, committed or not.

        Pre-commit this is exactly :meth:`discard` and needs no confirmation:
        nothing real has happened yet. Once committed it is genuinely
        destructive -- real spending history disappears -- so ``confirm``
        must be explicit, and every month the statement's rows touched gets
        recalculated afterward. That refresh has to run *after* the rows are
        gone and read their months *before* that, which is the reason this
        doesn't just call :func:`AggregateRefresher.refresh_statement`: that
        helper queries the statement's own rows to find their months, and by
        the time a refresh should happen here there are none left to query.
        """
        statement = await self._require_statement(statement_id)
        was_committed = statement.committed_at is not None
        if was_committed and not confirm:
            raise LedgerError("confirm must be true to delete a committed statement.")

        months: list[date] = []
        if was_committed:
            transactions = await self._transaction_repo.list_for_statement(statement_id)
            months = [row.posted_on.replace(day=1) for row in transactions]

        await self._transaction_repo.delete_for_statement(statement_id)
        self._store.delete(key=statement.object_key)
        await self._statement_repo.delete(statement)

        if months:
            await AggregateRefresher(self._session).refresh_months(months)

        logger.info(
            events.STATEMENT_DELETED, statement_id=str(statement_id), was_committed=was_committed
        )

    async def resolve_duplicate(
        self, statement_id: uuid.UUID, *, action: str, target_card_id: uuid.UUID | None
    ) -> StatementResponse | None:
        """Settle screen 03d.

        Classification (BUILD STEP 5.x) and rules (8.x) are not written yet,
        so there is nothing to carry across a ``replace`` beyond the rows
        themselves -- no override survives re-import because none can exist
        yet. Flagged here rather than silently approximated, the same way an
        unbuilt screen is flagged elsewhere in this codebase: when 6.x/8.x
        land, this is the call site that needs the override-carrying logic
        added.
        """
        statement = await self._require_uncommitted(statement_id)

        if action == "cancel":
            await self.discard(statement_id)
            return None

        if action == "keep_both":
            if target_card_id is None:
                raise LedgerError("target_card_id is required for keep_both.")
            statement.card_id = target_card_id
            statement.account_id = None
            return await self.commit(statement_id, accept_gap=False, gap_reason=None)

        if action == "replace":
            existing = await self._find_existing_for_period(statement)
            if existing is not None:
                await self._transaction_repo.delete_for_statement(existing.id)
                self._store.delete(key=existing.object_key)
                await self._statement_repo.delete(existing)
            return await self.commit(statement_id, accept_gap=False, gap_reason=None)

        raise LedgerError(f"Unknown resolution action: {action!r}")

    # -- Shared helpers ------------------------------------------------------

    async def _require_statement(self, statement_id: uuid.UUID) -> Statement:
        statement = await self._statement_repo.get(statement_id)
        if statement is None:
            raise NotFound("Statement not found.")
        return statement

    async def _require_uncommitted(self, statement_id: uuid.UUID) -> Statement:
        """Refuse a statement already committed. Editing or removing a row
        after commit would change a total the dashboard has already reported,
        and discarding one would leave a dangling reference to nothing."""
        statement = await self._require_statement(statement_id)
        if statement.committed_at is not None:
            raise LedgerError("This statement has already been committed.")
        return statement

    async def _require_row(self, statement_id: uuid.UUID, row_id: uuid.UUID) -> Transaction:
        row = await self._transaction_repo.get(row_id)
        if row is None or row.statement_id != statement_id:
            raise NotFound("Row not found.")
        return row

    async def _find_existing_for_period(self, statement: Statement) -> Statement | None:
        if (
            statement.card_id is None
            or statement.period_start is None
            or statement.period_end is None
        ):
            return None
        existing = await self._statement_repo.find_by_card_and_period(
            card_id=statement.card_id,
            period_start=statement.period_start,
            period_end=statement.period_end,
        )
        if existing is None or existing.id == statement.id:
            return None
        return existing

    async def _check_not_already_imported(self, statement: Statement) -> None:
        existing = await self._find_existing_for_period(statement)
        if existing is not None:
            raise AlreadyImported(
                "A statement for this card and period has already been committed.",
                details={
                    "existing_statement_id": str(existing.id),
                    "resolutions": ["replace", "keep_both", "cancel"],
                },
            )

    def _row_hash(self, statement: Statement, row: Transaction) -> bytes:
        parsed_row = ParsedRow(
            posted_on=row.posted_on,
            description_raw=row.description_raw,
            amount_minor=row.amount_minor,
            currency=row.currency,
        )
        # Same fallback, same reasoning, as app/extract/pipeline.py: prefer
        # the real account, and only fall back to the tenant when no card has
        # been tagged yet.
        scope = str(statement.account_id or statement.user_id)
        return DedupeGuard().row_hash(parsed_row, account_id=scope)

    def _reconcile(self, statement: Statement, transactions: list[Transaction]) -> ReconcileResult:
        """Adapts live ``Transaction`` rows onto :class:`Reconciler`, which
        was written against :class:`~app.extract.base.ParsedStatement`
        (BUILD STEP 1.1). Excludes skipped rows, which is what makes skipping
        one change the difference (TC-REC-007)."""
        parsed_rows = [
            ParsedRow(
                posted_on=row.posted_on,
                description_raw=row.description_raw,
                amount_minor=row.amount_minor,
                currency=row.currency,
            )
            for row in transactions
            if row.excluded_at is None
        ]
        parsed = ParsedStatement(
            rows=parsed_rows,
            printed_total_minor=statement.printed_total_minor,
            currency=statement.currency,
            period_start=statement.period_start,
            period_end=statement.period_end,
            detected_last4=statement.detected_last4,
            has_text_layer=bool(statement.has_text_layer),
            parser=statement.parser or "",
        )
        return Reconciler().check(parsed)

    def _reconciliation_response(self, result: ReconcileResult) -> Reconciliation:
        return Reconciliation(
            extracted_total=to_decimal_string(result.extracted_total_minor, result.currency),
            printed_total=to_decimal_string(result.printed_total_minor or 0, result.currency),
            difference=to_decimal_string(result.difference_minor, result.currency),
            currency=result.currency,
            reconciled=result.reconciled,
        )

    def _row_response(self, row: Transaction) -> RowResponse:
        return RowResponse(
            id=row.id,
            posted_on=row.posted_on,
            description=row.description_raw,
            amount=to_decimal_string(row.amount_minor, row.currency),
            currency=row.currency,
            skipped=row.excluded_at is not None,
            deleted=row.excluded_reason == "delete",
            page=row.page,
            line=row.line,
        )

    async def _row_mutation_response(
        self, statement: Statement, *, row: Transaction | None
    ) -> RowMutationResponse:
        transactions = await self._transaction_repo.list_for_statement(statement.id)
        result = self._reconcile(statement, transactions)
        return RowMutationResponse(
            row=self._row_response(row) if row is not None else None,
            reconciliation=self._reconciliation_response(result),
        )

    async def _classification_status(
        self, statement: Statement
    ) -> ClassificationStatusLiteral | None:
        """Where the background classify/aggregate pipeline stands.

        ``None`` pre-commit: classification is never enqueued until
        :meth:`commit` runs. A committed statement with no ``ProcessingJob``
        row yet (the worker hasn't picked up the job) is reported as
        ``in_progress`` rather than ``None``, so there is no gap where the
        review board would show nothing to explain the missing merchants.
        """
        if statement.committed_at is None:
            return None
        job = await self._processing_job_repo.latest_status(statement_id=statement.id)
        if job is None or job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            return "in_progress"
        if job.status == JobStatus.FAILED:
            return "failed"
        return "done"

    async def _statement_response(self, statement: Statement) -> StatementResponse:
        extraction = None
        failure = None
        reconciliation = None

        if statement.status not in (StatementStatus.PENDING,):
            transactions = await self._transaction_repo.list_for_statement(statement.id)
            extraction = ExtractionReport(
                has_text_layer=bool(statement.has_text_layer),
                card_identified=f"•••• {statement.detected_last4}"
                if statement.detected_last4
                else None,
                period=Period(from_=statement.period_start, to=statement.period_end)
                if statement.period_start and statement.period_end
                else None,
                printed_total=to_decimal_string(statement.printed_total_minor, statement.currency)
                if statement.printed_total_minor is not None
                else None,
                currency=statement.currency,
                rows_extracted=len(transactions),
                parser=statement.parser,
            )
            if statement.status not in (StatementStatus.FAILED,):
                reconciliation = self._reconciliation_response(
                    self._reconcile(statement, transactions)
                )

        if statement.status == StatementStatus.FAILED and statement.failure_reason is not None:
            failure = StatementFailure(
                reason=statement.failure_reason,
                message=FAILURE_MESSAGES[statement.failure_reason],
            )

        return StatementResponse(
            id=statement.id,
            status=statement.status,
            card_id=statement.card_id,
            period=Period(from_=statement.period_start, to=statement.period_end)
            if statement.period_start and statement.period_end
            else None,
            statement_month=statement.period_end or statement.period_start,
            trace_id=statement.trace_id,
            request_id=statement.request_id,
            extraction=extraction,
            failure=failure,
            reconciliation=reconciliation,
            classification_status=await self._classification_status(statement),
            needs_review=(
                statement.committed_at is not None
                and await self._transaction_repo.has_unclassified(statement.id)
            ),
            uploaded_at=statement.uploaded_at,
        )
