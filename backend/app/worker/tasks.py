"""Job definitions.

Every task takes ``traceparent`` as an argument. procrastinate owns its table
schema, so that is where the W3C trace context rides: into the job row's
``args`` JSONB, and back out here. CLAUDE.md describes it as "stored on the job
row", which this satisfies without forking the dependency, and promoting it to
a dedicated column later would be an additive migration.

Each task is thin. It restores the trace, calls the stage, and enqueues the
next one. The work itself lives in the extract, classify and aggregate modules,
so a stage can be tested without a queue.

===========================================================================
Handlers here belong to four different build steps:

  4.2 extract_statement    depends on: 1.1, 2.1, 3.1, 3.2
  5.5 classify_statement   depends on: 5.3, 5.4
  8.2 reapply_rules        depends on: 7.1, 8.1
  9.3 purge_account        depends on: 7.1

Aggregate is wired as part of 7.1.
===========================================================================
Inject the collaborators rather than constructing them inside the handlers. It
is the difference between being able to test the extract stage with a stub
parser today and having to wait for step 2.2 and a real bank statement.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.aggregate.refresh import AggregateRefresher
from app.classify.alias import AliasWriter
from app.classify.cascade import CascadeResolver
from app.classify.llm import LLMAdapter, build_llm_adapter
from app.classify.pipeline import classify
from app.config import get_settings
from app.db.models import JobStage, Statement, StatementStatus, User
from app.db.repositories import (
    CategoryRepository,
    MerchantRepository,
    ProcessingJobRepository,
    StatementRepository,
)
from app.db.session import get_worker_sessionmaker, tenant_session, unscoped_session
from app.extract.pipeline import extract
from app.extract.registry import ParserRegistry
from app.services.rules import RuleService
from app.storage import ObjectStore, build_object_store
from app.telemetry import events, get_logger
from app.worker.app import app
from app.worker.dispatch import run_stage
from app.worker.queue import enqueue

logger = get_logger(__name__)


async def _scrub_password(statement_id: str) -> None:
    """Erase ``password`` from every job payload recorded for this statement.

    ``args`` is a database column (``procrastinate_jobs.args``), so a password
    passed into a job is a password at rest until this runs. Matched by
    ``statement_id`` and task name rather than a specific job id: it catches
    every attempt that ever carried one for this statement, not only the one
    that just finished (TC-FAIL-004).
    """
    async with unscoped_session(get_worker_sessionmaker()) as session:
        await session.execute(
            text(
                "UPDATE procrastinate_jobs SET args = args - 'password' "
                "WHERE task_name = 'statement.extract' "
                "AND args ->> 'statement_id' = :statement_id "
                "AND args ? 'password'"
            ),
            {"statement_id": statement_id},
        )


@app.task(name="statement.extract", retry=3, queue="extract")
async def extract_statement(
    *,
    statement_id: str,
    user_id: str,
    traceparent: str | None = None,
    password: str | None = None,
    store: ObjectStore | None = None,
    registry: ParserRegistry | None = None,
) -> None:
    """Stage one: parse the PDF, reconcile, write rows.

    BUILD STEP 4.2. Verify with tests/integration/test_import_flow.py.

    ``store`` and ``registry`` are injectable and default to the real ones:
    what lets this be called directly with a stub parser in a test, with no
    queue and no real PDF involved. ``user_id`` rides on the job alongside
    ``statement_id`` because the worker connects as ``ledger_app`` too --
    row level security applies to it exactly as it does to the API -- and a
    job cannot open a tenant session on a tenant it does not yet know, which
    is the one thing it must know before it can even look ``statement_id``
    up.

    ``password`` is present only on a retry after screen 03c, is used once in
    memory here, and is scrubbed from every job payload that carried it once
    this attempt finishes, success or failure (TC-FAIL-004).
    """
    store = store or build_object_store(get_settings())
    registry = registry or ParserRegistry()

    async def _handler() -> None:
        async with tenant_session(get_worker_sessionmaker(), uuid.UUID(user_id)) as session:
            await extract(
                session,
                statement_id=uuid.UUID(statement_id),
                store=store,
                registry=registry,
                password=password,
            )

    try:
        await run_stage(
            stage="extract",
            statement_id=statement_id,
            traceparent=traceparent,
            attempt=1,
            handler=_handler,
        )
    finally:
        if password:
            await _scrub_password(statement_id)


async def _record_stage(
    session: AsyncSession,
    *,
    statement_id: str,
    stage: JobStage,
    outcome: Callable[[], Awaitable[None]],
) -> None:
    """Wrap one stage's work with :class:`~app.db.models.ProcessingJob`
    bookkeeping, so ``StatementResponse.classification_status`` has something
    to read while the stage is still running.

    Independent of :func:`app.worker.dispatch.run_stage`, which owns
    telemetry and the dead-letter decision; this owns only the row a request
    can see mid-flight.
    """
    repo = ProcessingJobRepository(session)
    await repo.start_attempt(statement_id=uuid.UUID(statement_id), stage=stage)
    try:
        await outcome()
    except Exception as exc:
        await repo.mark_failed(statement_id=uuid.UUID(statement_id), stage=stage, error=str(exc))
        raise
    else:
        await repo.mark_succeeded(statement_id=uuid.UUID(statement_id), stage=stage)


@app.task(name="statement.classify", retry=3, queue="classify")
async def classify_statement(
    *,
    statement_id: str,
    user_id: str,
    traceparent: str | None = None,
    llm: LLMAdapter | None = None,
) -> None:
    """Stage two: normalise descriptors and resolve merchants.

    BUILD STEP 5.5. Verify with tests/integration/test_cascade.py.

    ``llm`` is injectable and defaults to the configured adapter, the same
    shape ``extract_statement`` takes ``store`` and ``registry``: what lets a
    test drive this with the deterministic fake, or a stub that forces an
    outage, with no queue involved.

    Retried independently of extraction, which is the reason the stages are
    separate: the model provider is the least reliable component in the system
    and PDF parsing is the most expensive, so a provider hiccup must never
    cause a re-parse.
    """
    settings = get_settings()
    llm = llm or build_llm_adapter(settings)

    async def _handler() -> None:
        async with tenant_session(get_worker_sessionmaker(), uuid.UUID(user_id)) as session:

            async def _classify() -> None:
                merchants = MerchantRepository(session)
                cascade = CascadeResolver(
                    merchants,
                    CategoryRepository(session),
                    AliasWriter(merchants),
                    llm,
                    trgm_threshold=settings.trgm_similarity_threshold,
                )
                await classify(session, statement_id=uuid.UUID(statement_id), cascade=cascade)

                # Same transaction as the classify writes above: a crash between
                # the two would otherwise leave classified rows with no aggregate
                # job behind them, exactly the two-write hazard ADR-004 already
                # rules out for the extract stage.
                await enqueue(
                    session,
                    aggregate_statement,
                    statement_id=statement_id,
                    user_id=user_id,
                    traceparent=traceparent,
                )

            await _record_stage(
                session, statement_id=statement_id, stage=JobStage.CLASSIFY, outcome=_classify
            )

    await run_stage(
        stage="classify",
        statement_id=statement_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )


@app.task(name="statement.aggregate", retry=3, queue="aggregate")
async def aggregate_statement(
    *, statement_id: str, user_id: str, traceparent: str | None = None
) -> None:
    """Stage three: refresh the precomputed monthly totals.

    Wired as part of BUILD STEP 7.1.

    Idempotent, because this stage retries like the others: refreshing the
    same months twice is a delete-and-reinsert of the same rows, never an
    accumulation (see :meth:`~app.aggregate.refresh.AggregateRefresher.refresh_months`).
    """

    async def _handler() -> None:
        async with tenant_session(get_worker_sessionmaker(), uuid.UUID(user_id)) as session:

            async def _aggregate() -> None:
                await AggregateRefresher(session).refresh_statement(uuid.UUID(statement_id))

                statement = await session.get(Statement, uuid.UUID(statement_id))
                if statement is not None:
                    statement.status = StatementStatus.READY

            await _record_stage(
                session, statement_id=statement_id, stage=JobStage.AGGREGATE, outcome=_aggregate
            )

        logger.info(events.STATEMENT_AGGREGATE_SUCCEEDED, statement_id=statement_id)

    await run_stage(
        stage="aggregate",
        statement_id=statement_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )


@app.task(name="rules.reapply", retry=3, queue="rules")
async def reapply_rules(
    *,
    user_id: str,
    keep_overrides: bool = True,
    months: list[str] | None = None,
    traceparent: str | None = None,
) -> None:
    """Re-run every rule across history.

    BUILD STEP 8.2. Verify with tests/integration/test_rules.py.

    TODO:
      1. Select the rows in scope for the requested months.
      2. With ``keep_overrides``, exclude rows where ``classified_by`` is
         ``override``. This is the promise most likely to be broken by a later
         refactor, so write its test first (TC-RULE-009).
      3. Re-run the rules and update the matched rows.
      4. Refresh the aggregates for every month touched.
      5. Log which branch ran, how many rows changed, and how many overrides
         were preserved or discarded. With ``keep_overrides=False`` that log
         line is the only remaining record of what was destroyed.

    A job rather than a request: it can touch every row a person has, and the
    preview screen has already told them what it will change.
    """

    async def _handler() -> None:
        month_dates = [date.fromisoformat(f"{m}-01") for m in months] if months else None
        async with tenant_session(get_worker_sessionmaker(), uuid.UUID(user_id)) as session:
            changed, preserved, discarded = await RuleService(session).apply_all_rules(
                months=month_dates, keep_overrides=keep_overrides
            )
        logger.info(
            events.RULES_REAPPLY_COMPLETED,
            user_id=user_id,
            changed=changed,
            overrides_preserved=preserved,
            overrides_discarded=discarded,
        )

    await run_stage(
        stage="reapply",
        statement_id=user_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )


@app.task(name="account.purge", retry=5, queue="purge")
async def purge_account(
    *, user_id: str, traceparent: str | None = None, store: ObjectStore | None = None
) -> None:
    """Delete everything for one account, across both systems.

    BUILD STEP 9.3. Verify with tests/integration/test_account.py.

    Storage first, database second: if the job dies in between, a retry
    still has the statement rows naming which objects to delete. Reversed,
    a crash would leave the rows gone and nothing left to say which objects
    the bucket should drop, so they never would.

    The database half is one ``DELETE FROM users``, not a table-by-table
    walk: every tenant table's foreign key to ``users`` is
    ``ondelete="CASCADE"`` (0001_baseline), so Postgres removes accounts,
    cards, statements, transactions, overrides, rules and exports in the
    same statement. ``audit_log.actor_user_id`` carries no foreign key by
    design, so it is never touched -- the record outlives the account,
    which is when it matters.

    Idempotent by construction: object deletes are already idempotent, and
    once the user row is gone a retry's own tenant-scoped reads simply find
    nothing left to do.
    """
    store = store or build_object_store(get_settings())

    async def _handler() -> None:
        async with tenant_session(get_worker_sessionmaker(), uuid.UUID(user_id)) as session:
            statements = await StatementRepository(session).list_for_user()
            for statement in statements:
                store.delete(key=statement.object_key)
            store.delete_prefix(prefix=f"exports/{user_id}/")

            user = await session.get(User, uuid.UUID(user_id))
            if user is not None:
                await session.delete(user)

        logger.info(events.ACCOUNT_DELETE_COMPLETED, user_id=user_id, objects_deleted=len(statements))

    logger.info(events.ACCOUNT_DELETE_REQUESTED, user_id=user_id)
    await run_stage(
        stage="purge",
        statement_id=user_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )
