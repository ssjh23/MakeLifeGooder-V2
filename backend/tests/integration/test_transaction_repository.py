"""TransactionRepository. BUILD STEP 3.2 (see app/db/repositories.py). Depends
on 0.4 dedupe.

RED until TransactionRepository is written. Runs as ``ledger_app`` through
``sessionmaker_for_app`` with ``tenant_session``, same as
``test_statement_repository.py``.

A ``Statement`` is seeded directly through the ORM in every test here rather
than through ``StatementRepository``, so these tests do not depend on that
repository's own implementation status.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, AccountKind, Statement, StatementStatus, Transaction
from app.db.repositories import TransactionRepository
from app.db.session import tenant_session

pytestmark = [pytest.mark.integration, pytest.mark.unwritten]


def _hash(label: str) -> bytes:
    return hashlib.sha256(label.encode("utf-8")).digest()


async def _seed_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, 'Test User')"),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )


async def _seed_statement(
    session: AsyncSession, user_id: uuid.UUID, *, account_id: uuid.UUID | None = None
) -> uuid.UUID:
    statement = Statement(
        user_id=user_id,
        object_key="statement.pdf",
        account_id=account_id,
        status=StatementStatus.NEEDS_REVIEW,
    )
    session.add(statement)
    await session.flush()
    return statement.id


async def _seed_account(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    account = Account(user_id=user_id, institution="DBS", account_kind=AccountKind.CREDIT)
    session.add(account)
    await session.flush()
    return account.id


def _row(
    *,
    day: int = 22,
    description: str = "MCDONALDS-JUNCTION8",
    amount_minor: int = 1240,
    descriptor_key: str = "mcdonalds",
    dedupe_hash: bytes | None = None,
    account_id: uuid.UUID | None = None,
    excluded: bool = False,
) -> Transaction:
    return Transaction(
        posted_on=date(2026, 7, day),
        description_raw=description,
        descriptor_key=descriptor_key,
        amount_minor=amount_minor,
        dedupe_hash=(
            dedupe_hash if dedupe_hash is not None else _hash(f"{description}{day}{amount_minor}")
        ),
        account_id=account_id,
        excluded_at=datetime(2026, 8, 1) if excluded else None,
    )


class TestAddRows:
    @pytest.mark.p0
    async def test_bulk_inserts_rows_stamped_with_the_statement_and_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Rows are built by the caller without ``user_id``/``statement_id``
        set -- that is what "denormalise onto each row" means here, mirroring
        how StatementRepository.create() derives user_id from the session
        rather than an argument."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            rows = [_row(day=22), _row(day=23, description="FAIRPRICE FINEST NEX")]

            await TransactionRepository(session).add_rows(statement_id, rows)

            persisted = await TransactionRepository(session).list_for_statement(statement_id)
            assert len(persisted) == 2
            assert {t.user_id for t in persisted} == {user_id}
            assert {t.statement_id for t in persisted} == {statement_id}

    async def test_preserves_the_dedupe_hash_set_on_each_row(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            row_hash = _hash("a specific row")
            await TransactionRepository(session).add_rows(
                statement_id, [_row(dedupe_hash=row_hash)]
            )

            [persisted] = await TransactionRepository(session).list_for_statement(statement_id)
            assert persisted.dedupe_hash == row_hash

    async def test_denormalises_the_statements_account_id_onto_each_row(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """``account_id`` is what row_hash() was scoped by (dedupe.py); rows
        written against an account-tagged statement carry that same account,
        not whatever the caller happened to leave on the row."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            account_id = await _seed_account(session, user_id)
            statement_id = await _seed_statement(session, user_id, account_id=account_id)

            await TransactionRepository(session).add_rows(statement_id, [_row(account_id=None)])

            [persisted] = await TransactionRepository(session).list_for_statement(statement_id)
            assert persisted.account_id == account_id

    @pytest.mark.p0
    async def test_two_identical_rows_in_one_call_are_both_kept(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A real double charge, not a dedupe case (dedupe.py's own
        FilterKnown contract): add_rows() itself must not deduplicate."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            rows = [_row(dedupe_hash=_hash("x")), _row(dedupe_hash=_hash("y"))]

            await TransactionRepository(session).add_rows(statement_id, rows)

            persisted = await TransactionRepository(session).list_for_statement(statement_id)
            assert len(persisted) == 2


class TestListForStatement:
    async def test_orders_by_posted_on_then_line(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)

            later_date = Transaction(
                statement_id=statement_id,
                user_id=user_id,
                posted_on=date(2026, 7, 23),
                description_raw="second day",
                amount_minor=100,
                line=1,
            )
            second_line = Transaction(
                statement_id=statement_id,
                user_id=user_id,
                posted_on=date(2026, 7, 22),
                description_raw="first day, second line",
                amount_minor=200,
                line=2,
            )
            first_line = Transaction(
                statement_id=statement_id,
                user_id=user_id,
                posted_on=date(2026, 7, 22),
                description_raw="first day, first line",
                amount_minor=300,
                line=1,
            )
            session.add_all([later_date, second_line, first_line])
            await session.flush()

            result = await TransactionRepository(session).list_for_statement(statement_id)

            assert [t.description_raw for t in result] == [
                "first day, first line",
                "first day, second line",
                "second day",
            ]

    @pytest.mark.p0
    async def test_only_the_owners_rows_are_visible(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        owner = uuid.uuid4()
        stranger = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            statement_id = await _seed_statement(session, owner)
            await TransactionRepository(session).add_rows(statement_id, [_row()])

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            # A stranger cannot even see the statement id exists, but asking
            # for it directly must still come back empty, not another
            # tenant's rows.
            assert await TransactionRepository(session).list_for_statement(statement_id) == []


class TestFindDuplicatePairs:
    @pytest.mark.p0
    async def test_TC_TDUP_001_same_merchant_amount_and_day_pair_once(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Not twice: an unordered self-join gives both directions unless the
        query rules that out explicitly."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            await TransactionRepository(session).add_rows(
                statement_id,
                [
                    _row(day=22, dedupe_hash=_hash("p1")),
                    _row(day=22, dedupe_hash=_hash("p2")),
                ],
            )

            pairs = await TransactionRepository(session).find_duplicate_pairs(statement_id)

            assert len(pairs) == 1

    async def test_different_descriptor_key_does_not_pair(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            await TransactionRepository(session).add_rows(
                statement_id,
                [
                    _row(day=22, descriptor_key="mcdonalds", dedupe_hash=_hash("d1")),
                    _row(day=22, descriptor_key="fairprice", dedupe_hash=_hash("d2")),
                ],
            )

            assert await TransactionRepository(session).find_duplicate_pairs(statement_id) == []

    async def test_different_amount_does_not_pair(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            await TransactionRepository(session).add_rows(
                statement_id,
                [
                    _row(day=22, amount_minor=1240, dedupe_hash=_hash("a1")),
                    _row(day=22, amount_minor=1250, dedupe_hash=_hash("a2")),
                ],
            )

            assert await TransactionRepository(session).find_duplicate_pairs(statement_id) == []

    async def test_days_apart_does_not_pair(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            await TransactionRepository(session).add_rows(
                statement_id,
                [
                    _row(day=1, dedupe_hash=_hash("f1")),
                    _row(day=20, dedupe_hash=_hash("f2")),
                ],
            )

            assert await TransactionRepository(session).find_duplicate_pairs(statement_id) == []

    async def test_an_already_excluded_row_does_not_reappear(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A resolved pair must not keep surfacing on every re-check."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            await TransactionRepository(session).add_rows(
                statement_id,
                [
                    _row(day=22, dedupe_hash=_hash("e1")),
                    _row(day=22, dedupe_hash=_hash("e2"), excluded=True),
                ],
            )

            assert await TransactionRepository(session).find_duplicate_pairs(statement_id) == []


class TestExistingDedupeHashes:
    async def test_returns_only_the_hashes_already_present(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        known = _hash("already imported")
        unknown = _hash("never seen")

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            await TransactionRepository(session).add_rows(statement_id, [_row(dedupe_hash=known)])

            result = await TransactionRepository(session).existing_dedupe_hashes([known, unknown])

            assert result == {known}

    async def test_empty_input_returns_empty_output(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            assert await TransactionRepository(session).existing_dedupe_hashes([]) == set()

    @pytest.mark.p0
    async def test_TC_TDUP_009_is_scoped_to_the_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A hash that exists, but on another tenant's row, must not suppress
        a genuine import for this one (the reason row_hash() folds account_id
        in, not user_id alone -- this is the DB-read side of that guarantee)."""
        owner = uuid.uuid4()
        stranger = uuid.uuid4()
        shared_hash = _hash("coincidentally identical row content")

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            statement_id = await _seed_statement(session, owner)
            await TransactionRepository(session).add_rows(
                statement_id, [_row(dedupe_hash=shared_hash)]
            )

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            result = await TransactionRepository(session).existing_dedupe_hashes([shared_hash])

            assert result == set()


class TestDatabaseConstraints:
    @pytest.mark.p0
    @pytest.mark.security
    async def test_TC_TDUP_008_duplicate_dedupe_hash_for_one_user_is_enforced_by_the_database(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Raw SQL, service and repository both bypassed -- the guarantee is
        the constraint, not the application code in front of it."""
        user_id = uuid.uuid4()
        row_hash = _hash("the same row, twice")

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session, user_id)
            columns = (
                "(user_id, statement_id, posted_on, description_raw, amount_minor, dedupe_hash)"
            )
            await session.execute(
                text(
                    f"INSERT INTO transactions {columns} "
                    "VALUES (:uid, :sid, '2026-07-22', 'MCDONALDS-JUNCTION8', 1240, :hash)"
                ),
                {"uid": user_id, "sid": statement_id, "hash": row_hash},
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        f"INSERT INTO transactions {columns} "
                        "VALUES (:uid, :sid, '2026-08-05', 'a different row entirely', 999, :hash)"
                    ),
                    {"uid": user_id, "sid": statement_id, "hash": row_hash},
                )
