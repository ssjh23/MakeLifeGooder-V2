"""StatementRepository. BUILD STEP 3.1 (see app/db/repositories.py).

RED until StatementRepository is written. Runs as ``ledger_app`` through
``sessionmaker_for_app`` with ``tenant_session``, so a method that forgot its
row level security would show up as an empty result here, not as a query that
happens to work because the test connected as the table owner.

``StatementRepository.create()`` only accepts ``object_key`` and ``card_id``:
the fields the later finder methods search on (``file_sha256``,
``period_start``/``period_end``) are not writable through it. Tests that need
those seed a ``Statement`` directly through the ORM instead of going through
the repository under test, the same way ``tests/security/test_isolation.py``
seeds prerequisite rows with a raw insert.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Account, AccountKind, Card, CardKind, Statement, StatementStatus
from app.db.repositories import StatementRepository
from app.db.session import tenant_session

pytestmark = [pytest.mark.integration, pytest.mark.unwritten]


async def _seed_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """``users`` carries no RLS policy, so a plain insert is enough."""
    await session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, 'Test User')"),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )


async def _seed_card(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """A real card, for the methods that search by ``card_id``.

    ``accounts`` and ``cards`` are both tenant tables, so this must run inside
    the same ``tenant_session`` as the statement it will be attached to.
    """
    account = Account(user_id=user_id, institution="DBS", account_kind=AccountKind.CREDIT)
    session.add(account)
    await session.flush()

    card = Card(
        user_id=user_id,
        account_id=account.id,
        nickname="Test Card",
        last4="4429",
        card_kind=CardKind.PRIMARY,
    )
    session.add(card)
    await session.flush()
    return card.id


class TestCreate:
    @pytest.mark.p0
    async def test_TC_IMP_005_inserts_a_pending_statement_owned_by_the_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """``user_id`` is never an argument to ``create()``: it must come from
        the session's tenant setting, the same way row level security reads
        it, or the insert cannot satisfy the table's own WITH CHECK policy."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            repo = StatementRepository(session)

            statement = await repo.create(object_key="statements/a.pdf", card_id=None)

            assert statement.user_id == user_id
            assert statement.object_key == "statements/a.pdf"
            assert statement.card_id is None
            assert statement.status == StatementStatus.PENDING

    async def test_create_accepts_a_card_id(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card_id = await _seed_card(session, user_id)

            statement = await StatementRepository(session).create(
                object_key="statements/b.pdf", card_id=card_id
            )

            assert statement.card_id == card_id

    @pytest.mark.p0
    async def test_TC_IMP_005_a_transaction_that_never_commits_leaves_nothing(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """The property the enqueue depends on: create() only flushes, so a
        crash before the caller's commit leaves no pending statement with no
        job, rather than a statement whose job was never enqueued."""
        user_id = uuid.uuid4()
        statement_id: uuid.UUID | None = None

        class _SimulatedCrashError(Exception):
            pass

        with pytest.raises(_SimulatedCrashError):
            async with tenant_session(sessionmaker_for_app, user_id) as session:
                await _seed_user(session, user_id)
                repo = StatementRepository(session)
                statement = await repo.create(object_key="statements/c.pdf", card_id=None)
                statement_id = statement.id

                # Flushed, so it is visible within this same open transaction...
                assert await repo.get(statement_id) is not None

                raise _SimulatedCrashError("before the caller's commit")

        # ...but the transaction was rolled back, not committed, so nothing
        # persisted past it.
        async with tenant_session(sessionmaker_for_app, user_id) as session:
            assert await StatementRepository(session).get(statement_id) is None


class TestGet:
    async def test_get_returns_none_for_a_missing_id(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            assert await StatementRepository(session).get(uuid.uuid4()) is None

    @pytest.mark.p0
    async def test_TC_SEC_004_another_tenants_statement_is_invisible_not_forbidden(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """The reason the API can answer 404 for both "does not exist" and
        "belongs to someone else" without deciding which: under RLS they are
        the same outcome, ``None``, not a different one it has to detect."""
        owner = uuid.uuid4()
        stranger = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            statement = await StatementRepository(session).create(
                object_key="statements/owner.pdf", card_id=None
            )
            statement_id = statement.id

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            assert await StatementRepository(session).get(statement_id) is None

        async with tenant_session(sessionmaker_for_app, owner) as session:
            assert await StatementRepository(session).get(statement_id) is not None


class TestListForUser:
    async def test_orders_by_upload_time_descending(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        base = datetime(2026, 7, 1, 12, 0, 0)

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            oldest = Statement(
                user_id=user_id,
                object_key="1.pdf",
                status=StatementStatus.PENDING,
                uploaded_at=base,
            )
            middle = Statement(
                user_id=user_id,
                object_key="2.pdf",
                status=StatementStatus.PENDING,
                uploaded_at=base + timedelta(days=1),
            )
            newest = Statement(
                user_id=user_id,
                object_key="3.pdf",
                status=StatementStatus.PENDING,
                uploaded_at=base + timedelta(days=2),
            )
            session.add_all([middle, oldest, newest])
            await session.flush()

            result = await StatementRepository(session).list_for_user()

            assert [s.object_key for s in result] == ["3.pdf", "2.pdf", "1.pdf"]

    async def test_filters_by_status(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            session.add_all(
                [
                    Statement(
                        user_id=user_id, object_key="ready.pdf", status=StatementStatus.READY
                    ),
                    Statement(
                        user_id=user_id, object_key="pending.pdf", status=StatementStatus.PENDING
                    ),
                ]
            )
            await session.flush()

            result = await StatementRepository(session).list_for_user(
                status=StatementStatus.READY.value
            )

            assert [s.object_key for s in result] == ["ready.pdf"]

    async def test_filters_by_card_id(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card_id = await _seed_card(session, user_id)
            session.add_all(
                [
                    Statement(
                        user_id=user_id,
                        object_key="on_card.pdf",
                        card_id=card_id,
                        status=StatementStatus.PENDING,
                    ),
                    Statement(
                        user_id=user_id,
                        object_key="unassigned.pdf",
                        card_id=None,
                        status=StatementStatus.PENDING,
                    ),
                ]
            )
            await session.flush()

            result = await StatementRepository(session).list_for_user(card_id=card_id)

            assert [s.object_key for s in result] == ["on_card.pdf"]

    @pytest.mark.p0
    async def test_only_includes_the_tenants_own_statements(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        owner = uuid.uuid4()
        stranger = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            await StatementRepository(session).create(object_key="owner.pdf", card_id=None)

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            result = await StatementRepository(session).list_for_user()

            assert result == []


class TestFindByFileHash:
    async def test_returns_the_matching_statement(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        file_hash = hashlib.sha256(b"one PDF's bytes").digest()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            session.add(
                Statement(
                    user_id=user_id,
                    object_key="seen.pdf",
                    file_sha256=file_hash,
                    status=StatementStatus.READY,
                )
            )
            await session.flush()

            found = await StatementRepository(session).find_by_file_hash(file_hash)

            assert found is not None
            assert found.object_key == "seen.pdf"

    async def test_returns_none_when_absent(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            found = await StatementRepository(session).find_by_file_hash(
                hashlib.sha256(b"x").digest()
            )

            assert found is None

    @pytest.mark.p0
    async def test_TC_DUP_002_is_scoped_to_the_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Two tenants uploading byte-identical files (a shared template
        statement, a sample document) is not a collision: the uniqueness this
        method backs is per-user, not global."""
        owner = uuid.uuid4()
        stranger = uuid.uuid4()
        file_hash = hashlib.sha256(b"a file two people happen to both have").digest()

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            session.add(
                Statement(
                    user_id=owner,
                    object_key="owner.pdf",
                    file_sha256=file_hash,
                    status=StatementStatus.READY,
                )
            )
            await session.flush()

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            assert await StatementRepository(session).find_by_file_hash(file_hash) is None


class TestFindByCardAndPeriod:
    async def test_returns_the_matching_statement(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        period_start, period_end = date(2026, 7, 15), date(2026, 8, 14)

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card_id = await _seed_card(session, user_id)
            session.add(
                Statement(
                    user_id=user_id,
                    object_key="july.pdf",
                    card_id=card_id,
                    period_start=period_start,
                    period_end=period_end,
                    status=StatementStatus.READY,
                )
            )
            await session.flush()

            found = await StatementRepository(session).find_by_card_and_period(
                card_id=card_id, period_start=period_start, period_end=period_end
            )

            assert found is not None
            assert found.object_key == "july.pdf"

    async def test_returns_none_when_absent(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card_id = await _seed_card(session, user_id)

            found = await StatementRepository(session).find_by_card_and_period(
                card_id=card_id, period_start=date(2026, 7, 15), period_end=date(2026, 8, 14)
            )

            assert found is None


class TestDatabaseConstraints:
    """TC-DUP-007: the service is not the guarantee. Raw SQL, service bypassed."""

    @pytest.mark.p0
    @pytest.mark.security
    async def test_TC_DUP_007_one_statement_per_card_per_period_is_enforced_by_the_database(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card_id = await _seed_card(session, user_id)
            await session.execute(
                text(
                    "INSERT INTO statements "
                    "(user_id, object_key, card_id, period_start, period_end, status) "
                    "VALUES (:uid, 'first.pdf', :card_id, '2026-07-15', '2026-08-14', 'ready')"
                ),
                {"uid": user_id, "card_id": card_id},
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO statements "
                        "(user_id, object_key, card_id, period_start, period_end, status) "
                        "VALUES (:uid, 'second.pdf', :card_id, '2026-07-15', '2026-08-14', 'ready')"
                    ),
                    {"uid": user_id, "card_id": card_id},
                )

    @pytest.mark.p0
    @pytest.mark.security
    async def test_TC_DUP_002_the_same_file_twice_for_one_user_is_enforced_by_the_database(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        file_hash = hashlib.sha256(b"uploaded, then uploaded again").digest()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            session.add(
                Statement(
                    user_id=user_id,
                    object_key="first.pdf",
                    file_sha256=file_hash,
                    status=StatementStatus.READY,
                )
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                session.add(
                    Statement(
                        user_id=user_id,
                        object_key="again.pdf",
                        file_sha256=file_hash,
                        status=StatementStatus.READY,
                    )
                )
                await session.flush()
