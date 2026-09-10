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

from app.telemetry import events, get_logger
from app.worker.app import app
from app.worker.dispatch import run_stage

logger = get_logger(__name__)


@app.task(name="statement.extract", retry=3, queue="extract")
async def extract_statement(
    *, statement_id: str, traceparent: str | None = None, password: str | None = None
) -> None:
    """Stage one: parse the PDF, reconcile, write rows.

    BUILD STEP 4.2. Verify with tests/integration/test_import_flow.py.

    TODO:
      1. Set the statement to ``processing``.
      2. Fetch the PDF from object storage by ``object_key``.
      3. Compute ``file_sha256`` and check for an existing statement. Catching
         a re-upload here avoids parsing a document already seen.
      4. Select a parser through ``ParserRegistry`` and parse. Take the parser
         as an argument so a stub can be injected, which is what lets this be
         tested before step 2.2 exists.
      5. Reconcile. **Write nothing** if it does not pass and no gap is
         recorded.
      6. Filter rows against ``existing_dedupe_hashes``.
      7. Write the rows, set ``needs_review``, and record ``parser`` and
         ``has_text_layer``.
      8. On ``ExtractionFailure``, set ``failed`` with the reason and write no
         rows at all. A partially imported file is worse than a rejected one
         (TC-FAIL-007).
      9. Blank ``password`` from the job payload when the job finishes. That
         payload is a database row, and it is the one place a password could
         linger (TC-FAIL-004).

    Then add the retry test: fail mid-write, allow the retry, and assert the
    final row count is correct with no duplicates from the retry (TC-FAIL-010).

    ``password`` is present only on a retry after screen 03c, is used once in
    memory, and is never persisted.
    """

    async def _handler() -> None:
        raise NotImplementedError("Extraction is not written yet.")

    await run_stage(
        stage="extract",
        statement_id=statement_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )


@app.task(name="statement.classify", retry=3, queue="classify")
async def classify_statement(*, statement_id: str, traceparent: str | None = None) -> None:
    """Stage two: normalise descriptors and resolve merchants.

    BUILD STEP 5.5. Verify with tests/integration/test_cascade.py.

    TODO:
      1. Load the statement's distinct ``description_raw`` values.
      2. Normalise each into ``descriptor_key`` and store it on the rows.
      3. Run ``CascadeResolver.resolve_many`` over the distinct keys, not over
         the rows. A statement with forty McDonald's lines is one descriptor.
      4. Write the resolved merchant, category, ``classified_by``, confidence
         and ``prompt_version`` back onto the rows.
      5. Enqueue the aggregate stage.
      6. Let ``LLMUnavailable`` leave those descriptors unresolved and still
         finish the stage. An outage degrades classification; it must not fail
         an import (TC-REV-019).

    Retried independently of extraction, which is the reason the stages are
    separate: the model provider is the least reliable component in the system
    and PDF parsing is the most expensive, so a provider hiccup must never
    cause a re-parse.
    """

    async def _handler() -> None:
        raise NotImplementedError("Classification is not written yet.")

    await run_stage(
        stage="classify",
        statement_id=statement_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )


@app.task(name="statement.aggregate", retry=3, queue="aggregate")
async def aggregate_statement(*, statement_id: str, traceparent: str | None = None) -> None:
    """Stage three: refresh the precomputed monthly totals.

    Wired as part of BUILD STEP 7.1.

    TODO:
      1. Call ``AggregateRefresher.refresh_statement``.
      2. Set the statement to ``ready``.
      3. Keep it idempotent, because this stage retries like the others.
    """

    async def _handler() -> None:
        raise NotImplementedError("Aggregate refresh is not written yet.")

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
        raise NotImplementedError("Rule reapply is not written yet.")

    await run_stage(
        stage="reapply",
        statement_id=user_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )


@app.task(name="account.purge", retry=5, queue="purge")
async def purge_account(*, user_id: str, traceparent: str | None = None) -> None:
    """Delete everything for one account, across both systems.

    BUILD STEP 9.3. Verify with tests/integration/test_account.py.

    TODO:
      1. Delete every object under the user's prefix, with
         ``delete_prefix``. Object storage deletes are already idempotent.
      2. Delete the database rows.
      3. Do storage first. If the order is reversed and the job dies in
         between, the rows naming those objects are gone and nothing remains to
         say which ones to delete: the bucket keeps them forever.
      4. Leave ``audit_log`` alone.
      5. Make the whole thing re-runnable. Test it by forcing a mid-purge
         failure and letting the retry complete (TC-ACCT-010).

    The only flow that has to stay consistent across the database and object
    storage, which is why it is an idempotent job rather than a request.

    ``audit_log`` rows survive. ``actor_user_id`` carries no foreign key
    precisely so the record outlives the account, which is when it matters.
    """

    async def _handler() -> None:
        raise NotImplementedError("Account purge is not written yet.")

    logger.info(events.ACCOUNT_DELETE_REQUESTED, user_id=user_id)
    await run_stage(
        stage="purge",
        statement_id=user_id,
        traceparent=traceparent,
        attempt=1,
        handler=_handler,
    )
