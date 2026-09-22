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
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Card,
    Category,
    CategoryKind,
    CategoryMonthlyTotal,
    ClassifiedBy,
    DescriptorKeyOverride,
    JobStage,
    JobStatus,
    MatchType,
    Merchant,
    MerchantAlias,
    MerchantOverride,
    ProcessingJob,
    Rule,
    Statement,
    StatementStatus,
    Transaction,
    User,
)
from app.services._slug import slugify


async def _current_tenant_id(session: AsyncSession) -> uuid.UUID:
    """The ``app.user_id`` ``tenant_session()`` set with ``SET LOCAL``.

    Row level security reads this same setting to filter and check every
    query, but ``SET LOCAL`` populates a session variable, not a column
    default: a repository that writes a tenant-owned row has to stamp it
    explicitly, or the insert has nothing to satisfy the table's own
    ``WITH CHECK`` policy with.
    """
    result = await session.execute(text("SELECT current_setting('app.user_id')::uuid"))
    return cast(uuid.UUID, result.scalar_one())


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
        self,
        *,
        email: str,
        password_hash: str | None,
        display_name: str | None,
        auth_provider_id: str | None = None,
    ) -> User:
        user = User(
            email=email,
            password_hash=password_hash,
            display_name=display_name,
            auth_provider_id=auth_provider_id,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        user = await self.get(user_id)
        if user is not None:
            user.password_hash = password_hash

    async def set_auth_provider_id(self, user_id: uuid.UUID, auth_provider_id: str) -> None:
        """Link an existing account to an identity provider subject.

        Reached the first time a password account signs in through Auth0 with
        the same (verified) email -- one person, two paths in, one account.
        """
        user = await self.get(user_id)
        if user is not None:
            user.auth_provider_id = auth_provider_id

    async def has_committed_statements(self, user_id: uuid.UUID) -> bool:
        """Backs ``has_statements`` on ``GET /me`` and on login.

        Counts committed statements, not uploaded ones. An upload that never
        reconciled leaves the ledger empty, and unlocking a dashboard with
        nothing in it is worse than leaving it locked (TC-AUTH-012).

        ``statements`` carries ``FORCE ROW LEVEL SECURITY``, which applies its
        tenant policy even to a table's owner -- and this repository runs on
        the *unscoped* session (no tenant established; see the class
        docstring), which is exactly what lets sign-in look a user up before
        one is known. Without ``app.user_id`` set, the policy's
        ``user_id = current_setting('app.user_id')`` compares against a null
        setting, which is never true, so this query would silently and
        permanently return zero rows -- not an error, just an empty count on
        every call, for every user, forever. Scoping it here with ``SET
        LOCAL`` (transaction-local, like `tenant_session()`, and reset
        automatically at this request's commit) is what makes an explicit
        ``WHERE user_id`` clause actually see the rows it's filtering.
        """
        await self._session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        result = await self._session.execute(
            select(func.count())
            .select_from(Statement)
            .where(Statement.user_id == user_id, Statement.committed_at.is_not(None))
        )
        return bool(result.scalar_one())


class ProcessingJobRepository:
    """Stage tracking for the classify/aggregate pipeline.

    Backs ``StatementResponse.classification_status`` -- the signal that lets
    the review board tell "still classifying in the background" apart from
    "nothing matched". No unique constraint exists on ``(statement_id,
    stage)``, so writes here are get-then-create/update, never an
    upsert-on-conflict.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get(self, *, statement_id: uuid.UUID, stage: JobStage) -> ProcessingJob | None:
        result = await self._session.execute(
            select(ProcessingJob).where(
                ProcessingJob.statement_id == statement_id, ProcessingJob.stage == stage
            )
        )
        return result.scalar_one_or_none()

    async def start_attempt(self, *, statement_id: uuid.UUID, stage: JobStage) -> ProcessingJob:
        job = await self._get(statement_id=statement_id, stage=stage)
        if job is None:
            job = ProcessingJob(
                user_id=await _current_tenant_id(self._session),
                statement_id=statement_id,
                stage=stage,
                attempts=0,
            )
            self._session.add(job)
        job.status = JobStatus.RUNNING
        # `attempts` carries a server default, which populates in the database
        # rather than on the Python object -- a freshly-constructed row's
        # value is `None` here until the row round-trips, hence the fallback.
        job.attempts = (job.attempts or 0) + 1
        job.started_at = datetime.now(UTC)
        job.finished_at = None
        job.last_error = None
        await self._session.flush()
        return job

    async def mark_succeeded(self, *, statement_id: uuid.UUID, stage: JobStage) -> None:
        job = await self._get(statement_id=statement_id, stage=stage)
        if job is not None:
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(UTC)

    async def mark_failed(self, *, statement_id: uuid.UUID, stage: JobStage, error: str) -> None:
        job = await self._get(statement_id=statement_id, stage=stage)
        if job is not None:
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(UTC)
            job.last_error = error

    async def latest_status(self, *, statement_id: uuid.UUID) -> ProcessingJob | None:
        """The row that decides ``classification_status``: aggregate's job if
        one exists (it runs last), otherwise classify's."""
        aggregate_job = await self._get(statement_id=statement_id, stage=JobStage.AGGREGATE)
        if aggregate_job is not None:
            return aggregate_job
        return await self._get(statement_id=statement_id, stage=JobStage.CLASSIFY)


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
        statement = Statement(
            user_id=await _current_tenant_id(self._session),
            object_key=object_key,
            card_id=card_id,
            status=StatementStatus.PENDING,
        )
        self._session.add(statement)
        await self._session.flush()
        return statement

    async def get(self, statement_id: uuid.UUID) -> Statement | None:
        """Fetch one statement.

        TODO:
          1. Select by id, with no ``user_id`` clause.
          2. Return ``None`` when absent. Another tenant's statement is also
             ``None`` here, because the policy makes it invisible rather than
             forbidden, which is why the API can answer 404 for both without
             deciding which it was.
        """
        result = await self._session.execute(
            select(Statement).where(Statement.id == statement_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, *, status: str | None = None, card_id: uuid.UUID | None = None
    ) -> list[Statement]:
        """The history table on screen 03.

        TODO:
          1. Select all statements, ordered by upload time descending.
          2. Apply the optional status and card filters.
        """
        query = select(Statement)
        if status is not None:
            query = query.where(Statement.status == status)
        if card_id is not None:
            query = query.where(Statement.card_id == card_id)
        query = query.order_by(Statement.uploaded_at.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def find_by_file_hash(self, file_sha256: bytes) -> Statement | None:
        """Statement-level idempotency.

        TODO:
          1. Select by ``file_sha256``.
          2. Call this **before** parsing. The whole point is to avoid spending
             anything on a document already seen (TC-DUP-002).
        """
        result = await self._session.execute(
            select(Statement).where(Statement.file_sha256 == file_sha256)
        )
        return result.scalar_one_or_none()

    async def delete(self, statement: Statement) -> None:
        """Remove a statement outright, for
        :meth:`~app.services.statement.StatementService.discard`.

        Pre-commit only, enforced by the caller: this is not the undoable
        ``excluded_at`` removal a duplicate *row* gets, it is gone.
        """
        await self._session.delete(statement)
        await self._session.flush()

    async def find_by_card_and_period(
        self, *, card_id: uuid.UUID, period_start: date, period_end: date
    ) -> Statement | None:
        """Backs the 409 on screen 03d.

        Scoped to ``ready``, matching ``uq_statements_card_id_period``
        exactly (see that index's comment in ``app/db/models.py``): screen
        03d exists because a second, still-uncommitted draft for the same
        card and period must be able to coexist with a committed original
        until a person resolves it, so an unscoped query here could find more
        than the one row this is written to return. Filtering to the status
        the index itself is scoped to is what keeps "the one" true.

        A courtesy, not the guarantee. The unique index enforces it too,
        because TC-DUP-007 inserts directly in SQL with the service bypassed.
        """
        result = await self._session.execute(
            select(Statement).where(
                Statement.card_id == card_id,
                Statement.period_start == period_start,
                Statement.period_end == period_end,
                Statement.status == StatementStatus.READY,
            )
        )
        return result.scalar_one_or_none()


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
        user_id = await _current_tenant_id(self._session)
        statement = await self._session.get(Statement, statement_id)

        for row in rows:
            row.user_id = user_id
            row.statement_id = statement_id
            if row.account_id is None and statement is not None:
                row.account_id = statement.account_id
            # Denormalised the same way and for the same reason as account_id
            # above: the aggregate refresher's grain (BUILD STEP 7.1) and the
            # transaction list's card filter (7.2) both need it on the row
            # itself, not by joining back to the statement on every read.
            if row.card_id is None and statement is not None:
                row.card_id = statement.card_id

        self._session.add_all(rows)
        await self._session.flush()

    async def list_for_statement(self, statement_id: uuid.UUID) -> list[Transaction]:
        """TODO: select by statement, ordered by ``posted_on`` then ``line``, so
        the review screen matches the order on the paper document."""
        result = await self._session.execute(
            select(Transaction).where(Transaction.statement_id == statement_id).order_by(
                Transaction.posted_on, Transaction.line
            )
        )
        return list(result.scalars().all())

    async def has_unclassified(self, statement_id: uuid.UUID) -> bool:
        """Whether any counted row still has no category.

        Same condition ``ReviewService.finish`` gates completion on
        (``TC-REV`` finish tests), exposed here so the statement picker can
        tell "still needs a decision" apart from "fully reviewed" without a
        second full row fetch per statement.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.statement_id == statement_id,
                Transaction.excluded_at.is_(None),
                Transaction.category_id.is_(None),
            )
        )
        return result.scalar_one() > 0

    async def list_filtered(
        self,
        *,
        category_id: uuid.UUID | None = None,
        card_id: uuid.UUID | None = None,
        merchant_id: uuid.UUID | None = None,
        from_: date | None = None,
        to: date | None = None,
        search: str | None = None,
        sort: str = "date_desc",
    ) -> list[Transaction]:
        """The ``/transactions`` list, screen 06's drill-through (band 05).
        Excludes a removed duplicate the same way every other counted view
        does. ``search`` matches ``description_raw`` -- the printed text, not
        the normalised ``descriptor_key`` -- because this is the same
        as-printed column the individual-transactions table renders."""
        query = select(Transaction).where(Transaction.excluded_at.is_(None))
        if category_id is not None:
            query = query.where(Transaction.category_id == category_id)
        if card_id is not None:
            query = query.where(Transaction.card_id == card_id)
        if merchant_id is not None:
            query = query.where(Transaction.merchant_id == merchant_id)
        if from_ is not None:
            query = query.where(Transaction.posted_on >= from_)
        if to is not None:
            query = query.where(Transaction.posted_on <= to)
        if search:
            query = query.where(Transaction.description_raw.ilike(f"%{search}%"))

        order = {
            "date_desc": (Transaction.posted_on.desc(), Transaction.line.desc()),
            "date_asc": (Transaction.posted_on.asc(), Transaction.line.asc()),
            "amount_desc": (Transaction.amount_minor.desc(),),
            "amount_asc": (Transaction.amount_minor.asc(),),
        }.get(sort, (Transaction.posted_on.desc(),))
        query = query.order_by(*order)

        result = await self._session.execute(query)
        return list(result.scalars().all())

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
        result = await self._session.execute(
            select(Transaction).where(Transaction.statement_id == statement_id)
        )
        transactions = result.scalars().all()

        duplicates = []
        for i, t1 in enumerate(transactions):
            for t2 in transactions[i + 1:]:
                if (
                    t1.descriptor_key == t2.descriptor_key
                    and t1.amount_minor == t2.amount_minor
                    and abs((t1.posted_on - t2.posted_on).total_seconds()) <= 86400
                    and t1.excluded_at is None
                    and t2.excluded_at is None
                ):
                    duplicates.append((t1, t2))

        return duplicates

    async def get(self, row_id: uuid.UUID) -> Transaction | None:
        """Fetch one row, or ``None`` for absent or another tenant's (RLS)."""
        result = await self._session.execute(select(Transaction).where(Transaction.id == row_id))
        return result.scalar_one_or_none()

    async def delete(self, row: Transaction) -> None:
        """Remove a row extraction invented. Not for a duplicate: that is
        ``excluded_at``, undoable; this is gone."""
        await self._session.delete(row)
        await self._session.flush()

    async def delete_for_statement(self, statement_id: uuid.UUID) -> None:
        """Every row belonging to one statement, for :meth:`discard`."""
        rows = await self.list_for_statement(statement_id)
        for row in rows:
            await self._session.delete(row)
        await self._session.flush()

    async def existing_dedupe_hashes(self, hashes: list[bytes]) -> set[bytes]:
        """TODO: select the subset of ``hashes`` already present, so the extract
        stage can filter before writing. One query, not one per row."""
        result = await self._session.execute(
            select(Transaction.dedupe_hash).where(Transaction.dedupe_hash.in_(hashes))
        )
        # dedupe_hash is nullable on the column, but `.in_(hashes)` (hashes is
        # always bytes) can never match a NULL row, so this filter is a type
        # narrowing, not a real runtime possibility.
        return {row_hash for row_hash in result.scalars().all() if row_hash is not None}

    async def list_by_description_raw(self, description_raw: str) -> list[Transaction]:
        """Every one of the tenant's rows carrying this exact raw text, for
        :meth:`~app.services.review.ReviewService.split_descriptor`."""
        result = await self._session.execute(
            select(Transaction).where(Transaction.description_raw == description_raw)
        )
        return list(result.scalars().all())

    async def list_by_descriptor_key(self, descriptor_key: str) -> list[Transaction]:
        """Every one of the tenant's rows in this merchant group, across
        every statement -- not just the one statement's board currently
        shown -- for :meth:`~app.services.review.ReviewService.merge_descriptors`."""
        result = await self._session.execute(
            select(Transaction).where(Transaction.descriptor_key == descriptor_key)
        )
        return list(result.scalars().all())

    async def descriptor_key_in_use(
        self, descriptor_key: str, *, excluding_description_raw: str
    ) -> bool:
        """Whether any row other than the one being split already carries
        this key, so a freshly-derived key can be made unique before it's
        written."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.descriptor_key == descriptor_key,
                Transaction.description_raw != excluding_description_raw,
            )
        )
        return result.scalar_one() > 0


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

        No ``user_id`` clause: row level security on ``merchant_overrides``
        (a tenant table) scopes it, which is what makes one person's opinion
        invisible to everyone else.
        """
        result = await self._session.execute(
            select(MerchantOverride.merchant_id).where(
                MerchantOverride.descriptor_key == descriptor_key
            )
        )
        return result.scalars().first()

    async def find_alias(self, descriptor_key: str) -> uuid.UUID | None:
        """Cascade rung two. One indexed read: ``descriptor_key`` is globally
        unique, which is what the global alias cache buys -- no join, no
        per-tenant scoping, cross-tenant by design (ADR-006)."""
        result = await self._session.execute(
            select(MerchantAlias.merchant_id).where(
                MerchantAlias.descriptor_key == descriptor_key
            )
        )
        return result.scalars().first()

    async def find_similar(self, descriptor_key: str, threshold: float) -> uuid.UUID | None:
        """Cascade rung three, using pg_trgm.

        String similarity, deliberately, not semantic similarity -- the
        argument in ADR-006: embeddings place "MCDONALDS (CCP)" near
        "McDonalds" and also near Burger King, which is the wrong notion of
        sameness for merchant identity.
        """
        score = func.similarity(MerchantAlias.descriptor_key, descriptor_key)
        result = await self._session.execute(
            select(MerchantAlias.merchant_id).where(score >= threshold).order_by(score.desc())
        )
        return result.scalars().first()

    async def find_similar_with_score(
        self, descriptor_key: str, threshold: float
    ) -> tuple[uuid.UUID, float] | None:
        """:meth:`find_similar`, plus the match's own similarity score.

        Kept separate rather than changing :meth:`find_similar`'s return
        shape: that method's contract is already exercised by
        ``TestFindSimilar`` and this is the only caller that needs the score
        itself, to report it as :class:`~app.classify.cascade.Resolution`'s
        confidence rather than only the threshold every match already cleared.
        """
        score = func.similarity(MerchantAlias.descriptor_key, descriptor_key)
        result = await self._session.execute(
            select(MerchantAlias.merchant_id, score.label("score"))
            .where(score >= threshold)
            .order_by(score.desc())
        )
        row = result.first()
        return (row.merchant_id, float(row.score)) if row is not None else None

    async def get(self, merchant_id: uuid.UUID) -> Merchant | None:
        """Fetch one merchant, for its ``default_category_id`` once a rung
        has resolved a ``merchant_id`` from a descriptor."""
        return await self._session.get(Merchant, merchant_id)

    async def get_many(self, merchant_ids: set[uuid.UUID]) -> dict[uuid.UUID, Merchant]:
        """Bulk lookup, one query rather than N -- for enriching a
        transaction list with each row's merchant name (screen 06's
        individual-transactions band)."""
        if not merchant_ids:
            return {}
        result = await self._session.execute(
            select(Merchant).where(Merchant.id.in_(merchant_ids))
        )
        return {merchant.id: merchant for merchant in result.scalars().all()}

    async def find_by_canonical_name(self, canonical_name: str) -> Merchant | None:
        """An existing merchant whose name matches once casing and
        hyphenation are normalised away -- not a fuzzy match, see
        :attr:`Merchant.name_key`.

        Checked by the LLM rung (BUILD STEP 5.3) before it creates a new
        row: two different unseen descriptors that both genuinely name the
        same real-world merchant (a normaliser gap the corpus hasn't caught
        yet, two banks phrasing the same chain differently, or simply
        "Old Tea Hut" vs "OLD-TEA-HUT") would otherwise each mint their own
        ``Merchant`` -- correctly named, but a second identity for a
        merchant that already has one, splitting its history across two
        rows on every dashboard band that groups by ``merchant_id``.
        """
        result = await self._session.execute(
            select(Merchant).where(
                Merchant.name_key == slugify(canonical_name, fallback="merchant")
            )
        )
        return result.scalars().first()

    async def create(
        self, *, canonical_name: str, default_category_id: uuid.UUID | None
    ) -> Merchant:
        """A new cross-tenant merchant, for a descriptor the cascade has
        never seen before (rung four). No RLS applies here: see
        ``merchants``' table comment in ``0001_baseline``.
        """
        merchant = Merchant(
            canonical_name=canonical_name,
            name_key=slugify(canonical_name, fallback="merchant"),
            default_category_id=default_category_id,
        )
        self._session.add(merchant)
        await self._session.flush()
        return merchant

    async def upsert_alias(
        self, *, descriptor_key: str, merchant_id: uuid.UUID, source: str, confidence: float | None
    ) -> None:
        """Write the cross-tenant cache. See :class:`app.classify.alias.AliasWriter`
        for the policy questions; this method is the SQL, unconditional: the
        caller decides whether a write should happen at all, this just makes
        it happen, overwriting whatever was there.
        """
        statement = pg_insert(MerchantAlias).values(
            descriptor_key=descriptor_key,
            merchant_id=merchant_id,
            source=source,
            confidence=confidence,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[MerchantAlias.descriptor_key],
            set_={
                "merchant_id": statement.excluded.merchant_id,
                "source": statement.excluded.source,
                "confidence": statement.excluded.confidence,
            },
        )
        await self._session.execute(statement)
        await self._session.flush()


class DescriptorKeyOverrideRepository:
    """A person's own ruling that one exact raw line is its own merchant.

    See :class:`app.db.models.DescriptorKeyOverride` for why this keys on
    ``description_raw`` rather than ``descriptor_key`` like everything else
    in this file.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_one(self, description_raw: str) -> DescriptorKeyOverride | None:
        result = await self._session.execute(
            select(DescriptorKeyOverride).where(
                DescriptorKeyOverride.description_raw == description_raw
            )
        )
        return result.scalar_one_or_none()

    async def find_many(self, description_raws: set[str]) -> dict[str, str]:
        """Batch lookup for a whole statement's distinct raw texts, so
        classification issues one query rather than one per row."""
        if not description_raws:
            return {}
        result = await self._session.execute(
            select(
                DescriptorKeyOverride.description_raw, DescriptorKeyOverride.descriptor_key
            ).where(DescriptorKeyOverride.description_raw.in_(description_raws))
        )
        # dict(result.all()) reads cleaner but mypy rejects it: Row isn't
        # recognised as a tuple[str, str] by dict()'s overloads even though
        # it unpacks fine, which the comprehension below does rely on.
        return {  # noqa: C416
            description_raw: descriptor_key for description_raw, descriptor_key in result.all()
        }

    async def create(
        self, *, user_id: uuid.UUID, description_raw: str, descriptor_key: str
    ) -> DescriptorKeyOverride:
        override = DescriptorKeyOverride(
            user_id=user_id, description_raw=description_raw, descriptor_key=descriptor_key
        )
        self._session.add(override)
        await self._session.flush()
        return override

    async def set(
        self, *, user_id: uuid.UUID, description_raw: str, descriptor_key: str
    ) -> None:
        """Create or repoint the override for this raw text.

        Unlike :meth:`create`, this never raises on a pre-existing row --
        merging two merchant groups can revisit a raw text that was already
        split off on its own, and a merge repointing it again is a
        correction, not a collision.
        """
        existing = await self.find_one(description_raw)
        if existing is not None:
            existing.descriptor_key = descriptor_key
        else:
            self._session.add(
                DescriptorKeyOverride(
                    user_id=user_id, description_raw=description_raw, descriptor_key=descriptor_key
                )
            )
        await self._session.flush()


class CategoryRepository:
    """BUILD STEP 9.2, with the category service.

    ``find_by_slug`` is used earlier, by the cascade's LLM rung (5.5): a
    suggestion's ``category_slug`` must resolve against the system taxonomy
    seeded by ``alembic/versions/0004_seed_categories.py``, never create one
    on the fly. A tenant session cannot insert a system row at all --
    ``categories.tenant_isolation`` checks writes against the plain tenant
    predicate, which ``user_id IS NULL`` never satisfies (see that policy's
    comment in ``0001_baseline``) -- so an unrecognised slug is a suggestion
    the cascade drops, not a row this repository can create to cover for it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_slug(self, slug: str) -> Category | None:
        """A system category (``user_id IS NULL``), visible to every tenant."""
        result = await self._session.execute(
            select(Category).where(Category.slug == slug, Category.user_id.is_(None))
        )
        return result.scalar_one_or_none()

    async def find_by_slug_for_user(self, slug: str, user_id: uuid.UUID) -> Category | None:
        """The tenant's *own* category at this slug, distinct from
        :meth:`find_by_slug`'s system-wide lookup.

        ``uq_categories_user_id_slug`` is scoped to ``(user_id, slug)``, so a
        would-be duplicate only collides with a row carrying this exact
        ``user_id`` -- a same-named system category never does, since that
        row's ``user_id`` is ``NULL``. Checked before inserting (screen 04b's
        "+ New category"), so a repeat name surfaces as 409 rather than the
        database's own constraint violation.
        """
        result = await self._session.execute(
            select(Category).where(Category.slug == slug, Category.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get(self, category_id: uuid.UUID) -> Category | None:
        return await self._session.get(Category, category_id)

    async def list_for_user(self) -> list[tuple[Category, int]]:
        """The tenant's own categories plus every system one, each with a
        row count. CATEGORY_PREDICATE's USING clause already scopes the
        ``SELECT`` to exactly these two groups."""
        result = await self._session.execute(select(Category).order_by(Category.name))
        categories = list(result.scalars().all())
        counted = []
        for category in categories:
            count_result = await self._session.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.category_id == category.id, Transaction.excluded_at.is_(None))
            )
            counted.append((category, count_result.scalar_one()))
        return counted

    async def create(self, *, slug: str, name: str, colour: str | None, kind: str) -> Category:
        category = Category(
            slug=slug,
            name=name,
            colour_hex=colour,
            # CategoryKind(kind), not the raw string: unlike a value read back
            # from the database, an attribute assigned in the constructor is
            # never coerced to the enum type, and the response model reads
            # ``.kind.value`` on exactly this still-in-memory instance.
            kind=CategoryKind(kind),
            user_id=await _current_tenant_id(self._session),
        )
        self._session.add(category)
        await self._session.flush()
        return category

    async def delete(self, category: Category) -> None:
        await self._session.delete(category)
        await self._session.flush()

    async def row_count(self, category_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.category_id == category_id)
        )
        return result.scalar_one()

    async def reassign(self, *, from_category_id: uuid.UUID, to_category_id: uuid.UUID) -> int:
        """Move every transaction, override and rule off the source category
        onto the target, before the source is deleted.

        A rule left pointing at a deleted category stops classifying and
        says nothing, which surfaces a month later as "why did this stop
        working" (TC-CAT-007) -- moved here rather than left dangling.
        """
        result = await self._session.execute(
            select(Transaction).where(Transaction.category_id == from_category_id)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.category_id = to_category_id

        await self._session.execute(
            MerchantOverride.__table__.update()
            .where(MerchantOverride.category_id == from_category_id)
            .values(category_id=to_category_id)
        )
        await self._session.execute(
            Rule.__table__.update().where(Rule.category_id == from_category_id).values(category_id=to_category_id)
        )
        await self._session.execute(
            Merchant.__table__.update()
            .where(Merchant.default_category_id == from_category_id)
            .values(default_category_id=to_category_id)
        )
        await self._session.flush()
        return len(rows)


class RuleRepository:
    """BUILD STEP 8.1, with the rule service.

    A pattern matches a row if it matches either ``descriptor_key`` (the
    normalised join key every other rung of the cascade uses) or
    ``description_raw`` (the untouched statement line). ``normalise()`` can
    be destructive enough -- its processor-prefix fallback collapses
    ``"SMP**OLD TEA HUT (CHANGI SG Ref No. : ..."`` down to just ``"smp"``
    -- to discard the very merchant name a pattern is written against
    before a rule ever gets a chance to see it; falling back to the raw
    line is what lets a rule reach a merchant descriptor_key has already
    destroyed. Only two overlapping patterns are a defined conflict
    (CLAUDE.md's own open question); three or more is not attempted here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _condition(
        self, *, pattern: str, match_type: str, ignore_case: bool, match_negative: bool
    ):  # noqa: ANN201 - a SQLAlchemy ColumnElement[bool], not worth spelling out
        """One needle, tested against two haystacks, OR'd, negated once.

        Negating the OR as a whole -- not negating each column's check
        independently and OR'ing the negatives -- is what makes
        ``match_negative`` mean "matches neither column" (``~(A | B) ==
        ~A & ~B``, De Morgan). Negating per-column first would only
        require the pattern to be absent from *one* of the two strings.
        """
        needle = pattern.lower() if ignore_case else pattern

        def _positive(column: Any) -> Any:
            column = func.lower(column) if ignore_case else column
            match MatchType(match_type):
                case MatchType.CONTAINS:
                    return column.contains(needle, autoescape=True)
                case MatchType.STARTS_WITH:
                    return column.startswith(needle, autoescape=True)
                case MatchType.ENDS_WITH:
                    return column.endswith(needle, autoescape=True)
                case MatchType.EXACT:
                    return column == needle

        condition = or_(
            _positive(Transaction.descriptor_key), _positive(Transaction.description_raw)
        )
        return ~condition if match_negative else condition

    async def matching_rows(
        self,
        *,
        pattern: str,
        match_type: str,
        ignore_case: bool,
        match_negative: bool = False,
    ) -> int:
        """Count transactions a pattern would match, without changing any.

        Backs the live preview while the user types, so it has to be cheap:
        one indexed-ish count, no row materialisation.
        """
        condition = self._condition(
            pattern=pattern, match_type=match_type, ignore_case=ignore_case, match_negative=match_negative
        )
        result = await self._session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.descriptor_key.is_not(None), condition)
        )
        return result.scalar_one()

    async def matching_row_stats(
        self,
        *,
        pattern: str,
        match_type: str,
        ignore_case: bool,
        match_negative: bool = False,
    ) -> tuple[int, int, int]:
        """``(matches, already_categorised, manual_count)`` for a live
        preview: how many rows a pattern would touch, how many of those
        already carry some category, and how many are a person's own
        override -- the number that stops a rule silently overwriting a
        decision somebody made by hand."""
        condition = self._condition(
            pattern=pattern, match_type=match_type, ignore_case=ignore_case, match_negative=match_negative
        )
        result = await self._session.execute(
            select(
                func.count(),
                func.count().filter(Transaction.category_id.is_not(None)),
                func.count().filter(Transaction.classified_by == ClassifiedBy.OVERRIDE),
            )
            .select_from(Transaction)
            .where(Transaction.descriptor_key.is_not(None), condition)
        )
        row = result.one()
        return (row[0], row[1], row[2])

    async def matching_transactions(
        self,
        *,
        pattern: str,
        match_type: str,
        ignore_case: bool,
        match_negative: bool = False,
        only_unclassified: bool = False,
        keep_overrides: bool = True,
    ) -> list[Transaction]:
        """The actual rows a pattern matches, for applying a rule's category.

        ``keep_overrides=True`` (the default, and the only mode the live
        create/update path uses) excludes every row whose ``classified_by``
        is ``override``: a rule may never overwrite a person's own ruling.
        ``keep_overrides=False`` exists only for an explicit, user-requested
        full reapply (BUILD STEP 8.2) that means to discard them.
        """
        condition = self._condition(
            pattern=pattern, match_type=match_type, ignore_case=ignore_case, match_negative=match_negative
        )
        query = select(Transaction).where(Transaction.descriptor_key.is_not(None), condition)
        if keep_overrides:
            query = query.where(
                or_(Transaction.classified_by.is_(None), Transaction.classified_by != ClassifiedBy.OVERRIDE)
            )
        if only_unclassified:
            query = query.where(Transaction.category_id.is_(None))
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def all_for_user(self) -> list[Rule]:
        """Every rule, with no per-rule match count -- for a caller doing its
        own in-memory matching (:mod:`app.classify.rules`) against a batch of
        candidates rather than one at a time."""
        result = await self._session.execute(select(Rule).order_by(Rule.created_at))
        return list(result.scalars().all())

    async def list_for_user(self) -> list[tuple[Rule, int]]:
        """The tenant's rules, each with a live count of rows it currently
        matches."""
        rules = await self.all_for_user()
        return [
            (
                rule,
                await self.matching_rows(
                    pattern=rule.pattern,
                    match_type=rule.match_type,
                    ignore_case=rule.ignore_case,
                    match_negative=rule.match_negative,
                ),
            )
            for rule in rules
        ]

    async def find_conflicts(
        self,
        *,
        pattern: str,
        match_type: str,
        ignore_case: bool = True,
        match_negative: bool = False,
        exclude_rule_id: uuid.UUID | None = None,
    ) -> list[tuple[Rule, int]]:
        """Existing rules whose matched rows intersect this pattern's.

        The overlap count per rule, because the user needs the size of the
        collision to choose between narrowing the new pattern and deleting
        the old one -- never resolved silently.
        """
        new_condition = self._condition(
            pattern=pattern, match_type=match_type, ignore_case=ignore_case, match_negative=match_negative
        )
        rules = (await self._session.execute(select(Rule))).scalars().all()

        conflicts: list[tuple[Rule, int]] = []
        for rule in rules:
            if exclude_rule_id is not None and rule.id == exclude_rule_id:
                continue
            existing_condition = self._condition(
                pattern=rule.pattern,
                match_type=rule.match_type,
                ignore_case=rule.ignore_case,
                match_negative=rule.match_negative,
            )
            result = await self._session.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.descriptor_key.is_not(None), new_condition, existing_condition)
            )
            overlap = result.scalar_one()
            if overlap > 0:
                conflicts.append((rule, overlap))
        return conflicts

    async def get(self, rule_id: uuid.UUID) -> Rule | None:
        return await self._session.get(Rule, rule_id)

    async def get_many(self, rule_ids: set[uuid.UUID]) -> dict[uuid.UUID, Rule]:
        """Bulk lookup, one query rather than N -- for showing which rule
        classified each row on a list of transactions (screen 06's
        individual-transactions band), mirroring
        :meth:`MerchantRepository.get_many`."""
        if not rule_ids:
            return {}
        result = await self._session.execute(select(Rule).where(Rule.id.in_(rule_ids)))
        return {rule.id: rule for rule in result.scalars().all()}

    async def create(
        self,
        *,
        pattern: str,
        match_type: str,
        category_id: uuid.UUID,
        scope: str,
        ignore_case: bool,
        match_negative: bool,
    ) -> Rule:
        rule = Rule(
            user_id=await _current_tenant_id(self._session),
            pattern=pattern,
            match_type=match_type,
            category_id=category_id,
            scope=scope,
            ignore_case=ignore_case,
            match_negative=match_negative,
        )
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def delete(self, rule: Rule) -> None:
        await self._session.delete(rule)
        await self._session.flush()


class CardRepository:
    """BUILD STEP 9.1, with the card service."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_with_counts(self) -> list[tuple[Card, int]]:
        """Cards with ``statement_count``, in one query rather than N+1."""
        result = await self._session.execute(
            select(Card, func.count(Statement.id))
            .outerjoin(Statement, Statement.card_id == Card.id)
            .group_by(Card.id)
            .order_by(Card.created_at)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def get(self, card_id: uuid.UUID) -> Card | None:
        return await self._session.get(Card, card_id)


class DashboardRepository:
    """BUILD STEP 7.2, with the dashboard service. Depends on 7.1 aggregates.

    Reads ``category_monthly_totals`` for every banded number -- never
    aggregates ``transactions`` for a total the refresher already precomputed.
    The two exceptions, :meth:`unclassified_total` and
    :meth:`merchant_totals_in_category`, are not that: unclassified money and
    per-merchant ranking within a category are grains
    ``category_monthly_totals`` structurally cannot hold (its own
    ``category_id`` and ``card_id`` columns are ``NOT NULL``, and it carries
    no merchant column at all), not a duplicate of a total the table already
    has.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def totals_for_months(self, months: list[date]) -> list[CategoryMonthlyTotal]:
        if not months:
            return []
        result = await self._session.execute(
            select(CategoryMonthlyTotal).where(CategoryMonthlyTotal.month.in_(months))
        )
        return list(result.scalars().all())

    async def unclassified_total(self, *, start: date, end_exclusive: date) -> tuple[int, int]:
        """``(row_count, total_minor)`` for the period's unclassified rows.

        The count, not the sum, is what should gate the dashboard's lock: two
        unclassified refunds and charges can net to zero without the money
        being any less unaccounted for.
        """
        result = await self._session.execute(
            select(func.count(), func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.category_id.is_(None),
                Transaction.excluded_at.is_(None),
                Transaction.posted_on >= start,
                Transaction.posted_on < end_exclusive,
            )
        )
        row = result.one()
        return (row[0], row[1])

    async def merchant_totals_in_category(
        self, category_id: uuid.UUID, *, start: date, end_exclusive: date
    ) -> list[tuple[uuid.UUID | None, int, int]]:
        """``(merchant_id, total_minor, txn_count)`` per merchant, for one
        category over a period -- screen 06b's ranking, computed on the drill
        down rather than precomputed for every category every month."""
        result = await self._session.execute(
            select(Transaction.merchant_id, func.sum(Transaction.amount_minor), func.count())
            .where(
                Transaction.category_id == category_id,
                Transaction.excluded_at.is_(None),
                Transaction.posted_on >= start,
                Transaction.posted_on < end_exclusive,
            )
            .group_by(Transaction.merchant_id)
        )
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def card_totals_in_category(
        self, category_id: uuid.UUID, *, start: date, end_exclusive: date
    ) -> list[tuple[uuid.UUID, int]]:
        result = await self._session.execute(
            select(Transaction.card_id, func.sum(Transaction.amount_minor))
            .where(
                Transaction.category_id == category_id,
                Transaction.card_id.is_not(None),
                Transaction.excluded_at.is_(None),
                Transaction.posted_on >= start,
                Transaction.posted_on < end_exclusive,
            )
            .group_by(Transaction.card_id)
        )
        return [(row[0], row[1]) for row in result.all()]
