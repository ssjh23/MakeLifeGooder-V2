"""Cards, categories, export and deletion. Screens 02b to 02d, 08 to 08c.

===========================================================================
BUILD STEPS 9.1, 9.2 and 9.3 live in this file. They are independent of each
other, so take them in whichever order you need.

  9.1 CardService      depends on: 3.1 statement repository
      Verify: uv run pytest tests/integration/test_cards.py

  9.2 CategoryService  depends on: 6.3 review classify
      Verify: uv run pytest tests/integration/test_categories.py

  9.3 AccountService   depends on: 7.1 aggregates
      Verify: uv run pytest tests/integration/test_account.py
===========================================================================
Last phase. Everything here is reachable from the product working end to end,
which is why it comes after the dashboard rather than before it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.storage import ObjectStore


class CardService:
    """BUILD STEP 9.1."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_cards(self) -> object:
        """TODO: cards with ``statement_count`` and ``archived``, in one query.
        A count per card in a loop is the obvious N+1 here."""
        raise NotImplementedError

    async def create(self, **fields: object) -> object:
        """Create a card.

        TODO:
          1. Create or reuse the account for the institution and kind.
          2. Insert the card.
          3. Reject a ``last4`` that is not exactly four digits, and reject any
             field beyond the schema rather than ignoring it. There is no place
             to store a full number, an expiry or a CVV, so the only failure
             mode is accepting one and dropping it silently, which looks like
             success to the caller (TC-CARD-002, TC-CARD-003).
          4. Enforce the unique active last4 per user.
        """
        raise NotImplementedError

    async def update(self, card_id: uuid.UUID, **changes: object) -> object:
        """TODO: rename and recolour only. Assert in the test that statement
        count and rows are untouched (TC-CARD-005)."""
        raise NotImplementedError

    async def archive_preview(self, card_id: uuid.UUID) -> object:
        """What archiving would do.

        TODO:
          1. List every statement on the card.
          2. Compute which totals move and which stay.
          3. Share this implementation with :meth:`archive`. Computing the
             effect line in the client would duplicate the logic and eventually
             disagree with what actually happens.
        """
        raise NotImplementedError

    async def archive(self, card_id: uuid.UUID, dispositions: list[object]) -> object:
        """Archive a card, reversibly.

        TODO:
          1. Require a disposition for **every** statement, and reject the whole
             request when one is missing. A partially archived card leaves
             statements attached to something the user believes is gone
             (TC-CARD-007).
          2. Apply ``keep`` or ``reassign`` per statement.
          3. Set the card to archived. Category totals are unchanged; the
             per-card split moves.
          4. Make it reversible, which is what distinguishes it from the
             deletion path below.
        """
        raise NotImplementedError

    async def restore(self, card_id: uuid.UUID) -> object:
        """TODO: reactivate the card with its statements attached. Described in
        screen 02d's copy but with no entry point drawn on 02b, so the endpoint
        settles the behaviour even though the route into it is open."""
        raise NotImplementedError

    async def delete_statements(self, card_id: uuid.UUID, *, confirm: bool) -> object:
        """Destructive and not recoverable.

        TODO:
          1. Reject without ``confirm``. Nothing irreversible happens because a
             field was forgotten (TC-CARD-010).
          2. Delete the statements, their transactions, **and the stored PDFs**.
             It spans two systems and has to finish in both (TC-CARD-011).
          3. Refresh the affected monthly totals so the dashboard drops.
        """
        raise NotImplementedError


class CategoryService:
    """BUILD STEP 9.2."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_categories(self) -> object:
        """TODO: the tenant's categories plus system ones, each with a row
        count."""
        raise NotImplementedError

    async def create(self, *, name: str, colour: str | None, kind: str) -> object:
        """TODO: derive a slug from the name, enforce uniqueness per user, and
        set ``kind``. Without ``kind`` a credit card payment counts twice and
        doubles every total."""
        raise NotImplementedError

    async def update(self, category_id: uuid.UUID, **changes: object) -> object:
        """TODO: rename and recolour. The slug stays fixed: code and rules
        reference it, so renaming the display name must not break them."""
        raise NotImplementedError

    async def merge(self, category_id: uuid.UUID, *, into_category_id: uuid.UUID) -> object:
        """Move rows, then delete the source.

        TODO:
          1. Repoint every transaction, override and rule at the target.
          2. Delete the source.
          3. Recompute monthly totals for both.
          4. Make sure no rule is left pointing at the deleted category. A
             dangling rule stops classifying and says nothing, which surfaces a
             month later as "why did this stop working" (TC-CAT-007).
        """
        raise NotImplementedError

    async def delete(self, category_id: uuid.UUID) -> None:
        """TODO: refuse with 409 while any row still references it. Deleting a
        category with rows would orphan them into an unclassified state the
        user never chose."""
        raise NotImplementedError


class AccountService:
    """BUILD STEP 9.3."""

    def __init__(self, session: AsyncSession, store: ObjectStore) -> None:
        self._session = session
        self._store = store

    async def inventory(self) -> object:
        """TODO: count statements, transactions, rules, categories and cards.
        Reused verbatim by the deletion screen, so a person is shown the same
        numbers in both places."""
        raise NotImplementedError

    async def start_export(self, **request: object) -> object:
        """Begin an export.

        TODO:
          1. Require ``scope_id`` unless the scope is ``all``.
          2. Compute the column list, row count and estimated size, and return
             them **before** writing anything. Somebody about to take their
             data elsewhere should know what they are getting first.
          3. Write the file and store it, then expose a signed download URL.
          4. Export rules and categories as a separate file.
          5. Add the test asserting exported totals match the dashboard for the
             same period. If they diverge, one of the two is wrong and it
             matters which (TC-ACCT-005).
        """
        raise NotImplementedError

    async def get_export(self, export_id: uuid.UUID) -> object:
        """TODO: status, and a signed URL once ready."""
        raise NotImplementedError

    async def delete_everything(self, *, confirmation: str) -> None:
        """Delete the account.

        TODO:
          1. Require the literal string ``DELETE``, case-sensitive. Reject
             ``delete`` (TC-ACCT-007).
          2. Mark the account for deletion and enqueue ``account.purge``.
          3. Clear the session and return 202 immediately. The user should not
             wait on a bucket.
          4. In the job, delete the objects and then the rows, and make the
             whole thing idempotent: a partial failure must re-run cleanly and
             the second run must not trip over the first run's work
             (TC-ACCT-010).
          5. Leave ``audit_log`` alone. ``actor_user_id`` carries no foreign key
             precisely so the record outlives the account, which is the moment
             it matters most (TC-ACCT-011).
        """
        raise NotImplementedError
