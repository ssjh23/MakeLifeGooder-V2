"""Extraction pipeline: fetch, parse, reconcile-and-record, dedupe, write.

BUILD STEP 4.2's actual work. ``app/worker/tasks.py`` stays thin -- it opens
the tenant session and wraps this in telemetry -- so this function is what a
test calls directly, with a stub store and a stub parser, no queue and no
real PDF involved.

Nothing here classifies a row. That is step 5.x, not written: every row this
writes lands with ``category_id`` and ``descriptor_key`` unset, which is the
documented meaning of "genuinely unclassified", not a shortcut.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Card, FailureReason, StatementStatus, Transaction
from app.db.repositories import StatementRepository, TransactionRepository
from app.extract.base import ExtractionFailure
from app.extract.dedupe import DedupeGuard
from app.extract.reconcile import Reconciler
from app.extract.registry import ParserRegistry
from app.storage import ObjectStore
from app.telemetry import events, get_logger

logger = get_logger(__name__)


async def extract(
    session: AsyncSession,
    *,
    statement_id: uuid.UUID,
    store: ObjectStore,
    registry: ParserRegistry,
    password: str | None = None,
) -> None:
    """Run one extraction attempt.

    Idempotent under retry: every row carries a ``dedupe_hash``
    (``app/extract/dedupe.py``, BUILD STEP 0.4), and rows already present --
    including this same statement's own rows from a prior attempt that wrote
    some before failing -- are filtered out before the insert, so a retry
    cannot double a row (TC-FAIL-010).

    Does not gate on reconciliation. The arithmetic gate is the commit step
    (BUILD STEP 4.4): an unreconciled statement still needs its rows written,
    because screen 03b is exactly where a person fixes the shortfall by
    editing them. "Nothing enters the ledger unreconciled" describes what
    *commit* permits, not what extraction is allowed to record.
    """
    statement_repo = StatementRepository(session)
    transaction_repo = TransactionRepository(session)
    dedupe = DedupeGuard()

    statement = await statement_repo.get(statement_id)
    if statement is None:
        # Gone between enqueue and dequeue -- discarded, most likely. Nothing
        # to extract into.
        return

    statement.status = StatementStatus.PROCESSING

    if statement.account_id is None and statement.card_id is not None:
        card = await session.get(Card, statement.card_id)
        if card is not None:
            statement.account_id = card.account_id

    pdf_bytes = store.get_bytes(key=statement.object_key)

    file_hash = dedupe.statement_hash(pdf_bytes)
    duplicate_upload = await statement_repo.find_by_file_hash(file_hash)
    if duplicate_upload is None or duplicate_upload.id == statement.id:
        # A second, genuine duplicate is left without its own file_sha256
        # rather than raising: the per-user uniqueness constraint on that
        # column would otherwise reject this write outright, and the rows
        # this statement is about to write are already protected by
        # dedupe_hash below regardless of whether the hash was stored here.
        statement.file_sha256 = file_hash

    try:
        parser = registry.select(institution=None, pdf_bytes=pdf_bytes)
        parsed = parser.parse(pdf_bytes, password=password)
    except ExtractionFailure as exc:
        statement.status = StatementStatus.FAILED
        statement.failure_reason = FailureReason(exc.reason)
        statement.processed_at = datetime.now(UTC)
        logger.info(
            events.STATEMENT_EXTRACT_FAILED,
            statement_id=str(statement.id),
            reason=exc.reason,
        )
        return

    #: Scoping key for row-level dedupe. Prefers the real account, per
    #: dedupe.py's own reasoning (two of a person's cards can carry a
    #: coincidentally identical charge and both are real). Falling back to
    #: the tenant when no card has been tagged yet trades a theoretical
    #: collision between two never-tagged statements for the alternative,
    #: which is no cross-statement dedupe protection at all until a card is
    #: confirmed -- the worse failure, since a real double-import would go
    #: uncaught silently.
    dedupe_scope = str(statement.account_id or statement.user_id)

    hashes = [dedupe.row_hash(row, account_id=dedupe_scope) for row in parsed.rows]
    known = await transaction_repo.existing_dedupe_hashes(hashes)
    fresh_rows = dedupe.filter_known(parsed.rows, known, account_id=dedupe_scope)

    transactions = [
        Transaction(
            posted_on=row.posted_on,
            description_raw=row.description_raw,
            amount_minor=row.amount_minor,
            currency=row.currency,
            row_last4=row.row_last4,
            page=row.page,
            line=row.line,
            dedupe_hash=dedupe.row_hash(row, account_id=dedupe_scope),
        )
        for row in fresh_rows
    ]
    if transactions:
        await transaction_repo.add_rows(statement.id, transactions)

    statement.printed_total_minor = parsed.printed_total_minor
    statement.currency = parsed.currency
    statement.period_start = parsed.period_start
    statement.period_end = parsed.period_end
    statement.detected_last4 = parsed.detected_last4
    statement.parser = parsed.parser
    statement.has_text_layer = parsed.has_text_layer
    statement.status = StatementStatus.NEEDS_REVIEW
    statement.processed_at = datetime.now(UTC)

    result = Reconciler().check(parsed)
    logger.info(
        events.STATEMENT_EXTRACT_SUCCEEDED,
        statement_id=str(statement.id),
        row_count=len(transactions),
        parser=parsed.parser,
        has_text_layer=parsed.has_text_layer,
        reconciled=result.reconciled,
        difference_nonzero=result.difference_minor != 0,
    )
