"""Repositories. The only layer that writes SQL.

Routers delegate to services, services delegate to here. No router touches a
query, so an endpoint cannot quietly acquire its own data access with its own
rules.

**Nothing in this file filters by ``user_id``.** That is not an oversight and
it is not a bug waiting to happen: the session dependency sets ``app.user_id``
and row level security applies the filter in the database. A query written here
as though the system were single-tenant is correct, and one that forgets a
filter returns zero rows rather than another tenant's data.

``UserRepository`` is implemented because sign-in has to work before anything
else can be tested at all. The rest are signatures: the queries are where the
product's decisions live, and they are yours.

===========================================================================
BUILD STEPS 3.1, 3.2 and 5.1 live in this file. Order matters:

  3.1 StatementRepository     depends on: nothing
      Verify: uv run pytest tests/integration/test_statement_repository.py

  3.2 TransactionRepository   depends on: 0.4 dedupe
      Verify: uv run pytest tests/integration/test_transaction_repository.py

  5.1 MerchantRepository      depends on: 0.2 normalise
      Verify: uv run pytest tests/integration/test_cascade.py

RuleRepository, CardRepository and DashboardRepository come later, with the
services that use them (steps 7.2, 8.1, 9.1).
===========================================================================
Write these tests against the ``sessionmaker_for_app`` fixture and
``tenant_session``, so they run as the application role with the policies live.
A repository test that connects as the migration role proves nothing.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Statement, Transaction, User


class UserRepository:
    """Runs against an unscoped session: sign-in must resolve an account before
    an account is known, so there is no tenant to scope to yet."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        # citext, so casing cannot produce a second account for one person.
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create(
        self, *, email: str, password_hash: str | None, display_name: str | None
    ) -> User:
        user = User(email=email, password_hash=password_hash, display_name=display_name)
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        user = await self.get(user_id)
        if user is not None:
            user.password_hash = password_hash

    async def has_committed_statements(self, user_id: uuid.UUID) -> bool:
        """Backs ``has_statements`` on ``GET /me``.

        Counts committed statements, not uploaded ones. An upload that never
        reconciled leaves the ledger empty, and unlocking a dashboard with
        nothing in it is worse than leaving it locked (TC-AUTH-012).
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Statement)
            .where(Statement.user_id == user_id, Statement.committed_at.is_not(None))
        )
        return bool(result.scalar_one())


class StatementRepository:
    """BUILD STEP 3.1. Depends on nothing.

    Write ``tests/integration/test_statement_repository.py`` first, including
    one case proving a second tenant cannot see the first tenant's statements
    through these methods.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, object_key: str, card_id: uuid.UUID | None) -> Statement:
        """Insert a pending statement.

        TODO:
          1. Insert with ``status='pending'``. Do not set ``user_id`` from an
             argument; the session already establishes the tenant.
          2. ``flush()`` so the caller has the id, but do not commit. The commit
             belongs to the caller's transaction, because the enqueue has to be
             inside it.
          3. Return the row.

        Must run in the same transaction as the enqueue. A crash between the
        two has to leave neither, never a pending statement with no job
        (TC-IMP-005).
        """
        raise NotImplementedError

    async def get(self, statement_id: uuid.UUID) -> Statement | None:
        """Fetch one statement.

        TODO:
          1. Select by id, with no ``user_id`` clause.
          2. Return ``None`` when absent. Another tenant's statement is also
             ``None`` here, because the policy makes it invisible rather than
             forbidden, which is why the API can answer 404 for both without
             deciding which it was.
        """
        raise NotImplementedError

    async def list_for_user(
        self, *, status: str | None = None, card_id: uuid.UUID | None = None
    ) -> list[Statement]:
        """The history table on screen 03.

        TODO:
          1. Select all statements, ordered by upload time descending.
          2. Apply the optional status and card filters.
        """
        raise NotImplementedError

    async def find_by_file_hash(self, file_sha256: bytes) -> Statement | None:
        """Statement-level idempotency.

        TODO:
          1. Select by ``file_sha256``.
          2. Call this **before** parsing. The whole point is to avoid spending
             anything on a document already seen (TC-DUP-002).
        """
        raise NotImplementedError

    async def find_by_card_and_period(
        self, *, card_id: uuid.UUID, period_start: date, period_end: date
    ) -> Statement | None:
        """Backs the 409 on screen 03d.

        TODO:
          1. Select by card and period.
          2. Remember this is a courtesy, not the guarantee. A unique index
             enforces it too, because TC-DUP-007 inserts directly in SQL with
             the service bypassed.
        """
        raise NotImplementedError


class TransactionRepository:
    """BUILD STEP 3.2. Depends on 0.4 dedupe.

    Write ``tests/integration/test_transaction_repository.py`` first, including
    a case that inserts a duplicate ``(user_id, dedupe_hash)`` in raw SQL and
    asserts the **database** rejects it (TC-TDUP-008).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_rows(self, statement_id: uuid.UUID, rows: list[Transaction]) -> None:
        """Write extracted rows.

        TODO:
          1. Bulk insert.
          2. Set ``dedupe_hash`` on every row, or the constraint protecting
             against overlapping statements has nothing to work with.
          3. Denormalise ``account_id`` onto each row. It is the scope of the
             uniqueness constraint.
        """
        raise NotImplementedError

    async def list_for_statement(self, statement_id: uuid.UUID) -> list[Transaction]:
        """TODO: select by statement, ordered by ``posted_on`` then ``line``, so
        the review screen matches the order on the paper document."""
        raise NotImplementedError

    async def find_duplicate_pairs(
        self, statement_id: uuid.UUID
    ) -> list[tuple[Transaction, Transaction]]:
        """Same merchant, same amount, within twenty-four hours.

        TODO:
          1. Self-join the statement's rows on ``descriptor_key`` and
             ``amount_minor``.
          2. Constrain to pairs within twenty-four hours of each other.
          3. Return each pair once, not twice. An unordered self-join gives you
             both directions.
          4. Exclude rows already marked excluded, so a resolved pair does not
             reappear.

        Flagged for a person to settle, never removed automatically: a genuine
        repeat purchase is indistinguishable from a double charge to everyone
        except the person who made it.

        This is a different thing from ``dedupe_hash``, which catches the same
        row arriving in two overlapping statements and is skipped silently
        because that is not a judgment call (TC-TDUP-006).
        """
        raise NotImplementedError

    async def existing_dedupe_hashes(self, hashes: list[bytes]) -> set[bytes]:
        """TODO: select the subset of ``hashes`` already present, so the extract
        stage can filter before writing. One query, not one per row."""
        raise NotImplementedError


class MerchantRepository:
    """BUILD STEP 5.1. Depends on 0.2 normalise.

    The three cheap rungs of the cascade. Write them together with
    ``tests/integration/test_cascade.py`` and assert each rung short-circuits:
    an override means no alias lookup happens at all.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_override(self, descriptor_key: str) -> uuid.UUID | None:
        """Cascade rung one. The user's own ruling beats everything.

        TODO:
          1. Select from ``merchant_overrides`` by ``descriptor_key``.
          2. No ``user_id`` clause. The policy scopes it, which is what makes
             one person's opinion invisible to everyone else.
        """
        raise NotImplementedError

    async def find_alias(self, descriptor_key: str) -> uuid.UUID | None:
        """Cascade rung two.

        TODO:
          1. Select from ``merchant_aliases`` by ``descriptor_key``.
          2. One indexed read, which is what the global uniqueness of that
             column buys. If this needs a join, something upstream is wrong.
        """
        raise NotImplementedError

    async def find_similar(self, descriptor_key: str, threshold: float) -> uuid.UUID | None:
        """Cascade rung three, using pg_trgm.

        TODO:
          1. Use ``similarity()`` against ``merchant_aliases.descriptor_key``.
          2. Filter above ``threshold`` and take the single best match.
          3. Tune the threshold against real descriptors, not invented ones.
             Too low merges unrelated merchants; too high means the rung never
             fires and every near miss becomes a paid model call.

        Similarity here means *string* similarity, not semantic similarity.
        That distinction is the argument in ADR-006: embeddings place
        "MCDONALDS (CCP)" near "McDonalds" and also near Burger King, which is
        the wrong notion of sameness for merchant identity.
        """
        raise NotImplementedError

    async def upsert_alias(
        self, *, descriptor_key: str, merchant_id: uuid.UUID, source: str, confidence: float | None
    ) -> None:
        """Write the cross-tenant cache. See :class:`app.classify.alias.AliasWriter`
        for the policy questions; this method is the SQL.

        TODO:
          1. ``INSERT ... ON CONFLICT (descriptor_key) DO UPDATE``.
          2. Decide whether a later, more confident source may overwrite an
             earlier one, and make it explicit rather than incidental.
        """
        raise NotImplementedError


class RuleRepository:
    """BUILD STEP 8.1, with the rule service."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self) -> list[object]:
        """TODO: select the tenant's rules with a count of rows each matches."""
        raise NotImplementedError

    async def matching_rows(self, *, pattern: str, match_type: str, ignore_case: bool) -> int:
        """TODO: count transactions a pattern would match, without changing any.

        Backs the live preview while the user types, so it has to be cheap.
        """
        raise NotImplementedError

    async def find_conflicts(self, *, pattern: str, match_type: str) -> list[object]:
        """Overlapping patterns. Never resolved silently.

        TODO:
          1. Find existing rules whose matched rows intersect this pattern's.
          2. Return the overlap count per rule, because the user needs the size
             of the collision to choose between narrowing and deleting.
        """
        raise NotImplementedError


class CardRepository:
    """BUILD STEP 9.1, with the card service."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_with_counts(self) -> list[object]:
        """TODO: cards with ``statement_count``, in one query rather than N+1."""
        raise NotImplementedError

    async def get(self, card_id: uuid.UUID) -> object | None:
        """TODO: select by id, no user filter."""
        raise NotImplementedError


class DashboardRepository:
    """BUILD STEP 7.2, with the dashboard service. Depends on 7.1 aggregates."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bands(self, *, range_: str, month: date | None) -> object:
        """Reads ``category_monthly_totals``.

        TODO:
          1. Select from the precomputed table for the requested range.
          2. Never aggregate ``transactions`` here. If you find yourself
             writing ``SUM(amount_minor)`` in this method, the aggregate
             refresher is missing a trigger and this is papering over it.
          3. Group into the five bands the screen needs: by month, by category,
             by card.

        The precomputed table is the reason the dashboard has a latency budget
        it can meet, and it is also why Redis was not needed at launch: caching
        an aggregate that is already precomputed buys nothing and adds an
        invalidation problem on every reclassification (ADR-010).
        """
        raise NotImplementedError

    async def category_detail(self, category_id: uuid.UUID) -> object:
        """TODO: six months of totals, companies ranked within the category, and
        which cards were used."""
        raise NotImplementedError
