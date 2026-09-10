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
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.storage import ObjectStore


class StatementService:
    def __init__(self, session: AsyncSession, store: ObjectStore) -> None:
        self._session = session
        self._store = store

    # -- Step 4.1 ----------------------------------------------------------

    async def create_upload_url(self, *, content_type: str, size_bytes: int) -> object:
        """Issue a presigned PUT.

        TODO:
          1. Delegate to ``self._store.presign_upload``.
          2. Let ``UploadRejected`` become a 400. Rejecting at URL issue means
             an unusable object never reaches the bucket at all.
          3. Map onto ``UploadUrlResponse``.
          4. Never log the URL. It is a bearer credential, and a logged one is
             a writable object handle sitting in your log backend.

        The API never receives the bytes, so concurrent uploads are a storage
        concern rather than an API memory concern (ADR-009).
        """
        raise NotImplementedError

    async def register(self, *, upload_id: str, card_id: uuid.UUID | None) -> uuid.UUID:
        """Record the uploaded object and enqueue extraction.

        TODO:
          1. Derive the object key from ``upload_id`` and confirm the object
             exists. A client can call this without having uploaded anything.
          2. Insert the statement with ``StatementRepository.create``.
          3. Capture ``current_traceparent()`` from :mod:`app.telemetry`.
          4. Enqueue ``statement.extract`` with the statement id and that
             traceparent, **inside the same transaction** as the insert.
          5. Stamp ``trace_id`` and ``request_id`` onto the row, so a stuck
             statement can be traced from a support conversation without
             database access.
          6. Return 202 with the statement. There is no synchronous path.

        Test both halves: assert the statement row and the queued job both
        exist, then force a crash between them and assert neither does
        (TC-IMP-005). procrastinate stores jobs in this same Postgres precisely
        so that second case is unrepresentable rather than merely handled.
        """
        raise NotImplementedError

    async def get(self, statement_id: uuid.UUID) -> object:
        """Status and the extraction report.

        TODO:
          1. Fetch, and raise ``NotFound`` when absent.
          2. Build the extraction report: text layer, card identified, period,
             printed total, rows extracted, parser. Screen 03a shows exactly
             these four facts because the next screen's behaviour depends on
             them.
          3. Include ``trace_id`` and, on failure, the reason and request id.

        Absent and belonging-to-someone-else both raise NotFound. Row level
        security already makes the second case return nothing, so this does not
        have to remember to check.
        """
        raise NotImplementedError

    # -- Step 4.3 ----------------------------------------------------------

    async def rows(self, statement_id: uuid.UUID) -> object:
        """TODO: return the rows plus live reconciliation state, so screen 03b
        can render both from one request."""
        raise NotImplementedError

    async def add_row(
        self,
        statement_id: uuid.UUID,
        *,
        posted_on: object,
        description: str,
        amount: Decimal,
    ) -> object:
        """Add a row missed by extraction.

        TODO:
          1. Refuse if the statement is already committed. Editing rows after
             commit would change a total the dashboard has already reported.
          2. Convert the amount with :func:`app.money.to_minor`.
          3. Insert the row and set its ``dedupe_hash``.
          4. Re-run the reconciler and return the result **in this response**.

        Every row mutation returns the reconciliation, so screen 03b counts
        down from the response the keystroke already produced rather than
        issuing a second request per character.
        """
        raise NotImplementedError

    async def edit_row(
        self, statement_id: uuid.UUID, row_id: uuid.UUID, **changes: object
    ) -> object:
        """TODO: apply the changes, recompute ``dedupe_hash`` if the identity
        fields moved, re-reconcile, return the state. Editing a reconciled
        statement must re-block the commit (TC-REC-006)."""
        raise NotImplementedError

    async def skip_row(self, statement_id: uuid.UUID, row_id: uuid.UUID) -> object:
        """TODO: mark the row skipped so the reconciler excludes it, then
        return the new state. Skipping is not deleting: the row stays visible
        on screen struck through."""
        raise NotImplementedError

    async def delete_row(self, statement_id: uuid.UUID, row_id: uuid.UUID) -> object:
        """TODO: remove a row extraction invented, then re-reconcile."""
        raise NotImplementedError

    async def unlock(self, statement_id: uuid.UUID, *, password: str) -> None:
        """Retry extraction with a password.

        TODO:
          1. Re-enqueue ``statement.extract`` with the password and the
             **same** traceparent, so the retry appears on the original trace.
          2. Reset the status to ``pending``.
          3. Do not persist the password anywhere: not on the statement, not in
             a log, not in an audit row.
          4. Blank it from the job payload once the job completes. That payload
             is a database row, and it is the one place a password could
             otherwise linger (TC-FAIL-004).
        """
        raise NotImplementedError

    # -- Step 4.4, the gate ------------------------------------------------

    async def commit(
        self, statement_id: uuid.UUID, *, accept_gap: bool, gap_reason: str | None
    ) -> object:
        """The gate.

        TODO:
          1. Refuse a statement with no card tagged. A committed statement that
             belongs to no card cannot appear on any dashboard band.
          2. Check for an existing statement on the same card and period, and
             raise ``AlreadyImported`` (409) carrying
             ``existing_statement_id`` and the resolutions the client renders.
          3. Reconcile, then call ``Reconciler.assert_committable``. Let
             ``NotReconciled`` propagate; the handler maps it to 422.
          4. Require ``gap_reason`` when ``accept_gap`` is set, and reject with
             400 otherwise. An unexplained gap is indistinguishable from a bug
             six months later (TC-REC-011).
          5. Record the gap on the statement when accepted, permanently.
          6. Set ``committed_at``. This is what flips ``has_statements`` and
             unlocks the navigation.
          7. Enqueue the classify stage.

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
        raise NotImplementedError

    async def discard(self, statement_id: uuid.UUID) -> None:
        """TODO: delete the pending statement and its rows, and remove the
        stored object. The ledger must be exactly as it was."""
        raise NotImplementedError

    async def resolve_duplicate(
        self, statement_id: uuid.UUID, *, action: str, target_card_id: uuid.UUID | None
    ) -> object:
        """Settle screen 03d.

        TODO:
          1. ``replace``: carry manual categories from the existing statement
             onto the new rows, then re-run rules on the remainder. A person's
             own decisions survive a re-import, which is the whole reason
             replace exists rather than delete-then-add.
          2. ``keep_both``: require ``target_card_id`` and retag, or the
             uniqueness rule is simply broken twice.
          3. ``cancel``: discard the new file and leave the original untouched,
             with no orphan object left in storage.
        """
        raise NotImplementedError
