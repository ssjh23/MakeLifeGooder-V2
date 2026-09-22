"""BUILD STEP 7.1: the aggregate refresher.

The one assertion that covers the product's central promise: recategorise a
row, and the category totals move while the statement total does not.
Reclassification relabels money; it never creates or destroys it.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.aggregate.refresh import AggregateRefresher
from app.db.models import Account, Card, Category, Statement, StatementStatus, Transaction
from app.db.session import tenant_session

pytestmark = pytest.mark.integration


async def _seed_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    from sqlalchemy import text

    await session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, 'Test User')"),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )


async def _seed_card(session: AsyncSession, user_id: uuid.UUID, *, last4: str = "4429") -> Card:
    account = Account(user_id=user_id, institution="DBS", account_kind="credit")
    session.add(account)
    await session.flush()
    card = Card(user_id=user_id, account_id=account.id, nickname="Test Card", last4=last4)
    session.add(card)
    await session.flush()
    return card


async def _seed_category(
    session: AsyncSession, user_id: uuid.UUID, *, slug: str = "test-groceries"
) -> Category:
    category = Category(slug=slug, name=slug.title(), user_id=user_id)
    session.add(category)
    await session.flush()
    return category


async def _seed_statement(session: AsyncSession, user_id: uuid.UUID, card: Card) -> Statement:
    statement = Statement(
        user_id=user_id,
        account_id=card.account_id,
        card_id=card.id,
        object_key=f"uploads/{uuid.uuid4()}.pdf",
        status=StatementStatus.READY,
        currency="SGD",
    )
    session.add(statement)
    await session.flush()
    return statement


async def _seed_row(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    statement: Statement,
    category_id: uuid.UUID | None,
    amount_minor: int,
    posted_on: date,
    excluded: bool = False,
) -> Transaction:
    from datetime import UTC, datetime

    row = Transaction(
        user_id=user_id,
        statement_id=statement.id,
        account_id=statement.account_id,
        card_id=statement.card_id,
        posted_on=posted_on,
        description_raw="A MERCHANT",
        amount_minor=amount_minor,
        category_id=category_id,
        dedupe_hash=uuid.uuid4().bytes + uuid.uuid4().bytes,
        excluded_at=datetime.now(UTC) if excluded else None,
    )
    session.add(row)
    await session.flush()
    return row


class TestRefreshStatement:
    @pytest.mark.p0
    async def test_recategorising_a_row_moves_category_totals_but_not_the_statement_total(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card = await _seed_card(session, user_id)
            groceries = await _seed_category(session, user_id, slug="groceries-a")
            shopping = await _seed_category(session, user_id, slug="shopping-a")
            statement = await _seed_statement(session, user_id, card)
            row = await _seed_row(
                session,
                user_id=user_id,
                statement=statement,
                category_id=groceries.id,
                amount_minor=1000,
                posted_on=date(2026, 7, 22),
            )

            await AggregateRefresher(session).refresh_statement(statement.id)

            totals = await self._totals(session, user_id)
            assert totals[(card.id, groceries.id, date(2026, 7, 1))] == 1000
            statement_total_before = sum(totals.values())

            row.category_id = shopping.id
            await session.flush()
            await AggregateRefresher(session).refresh_statement(statement.id)

            totals = await self._totals(session, user_id)
            assert (card.id, groceries.id, date(2026, 7, 1)) not in totals
            assert totals[(card.id, shopping.id, date(2026, 7, 1))] == 1000
            assert sum(totals.values()) == statement_total_before

    async def test_excluded_rows_do_not_count(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card = await _seed_card(session, user_id)
            category = await _seed_category(session, user_id, slug="groceries-b")
            statement = await _seed_statement(session, user_id, card)
            await _seed_row(
                session,
                user_id=user_id,
                statement=statement,
                category_id=category.id,
                amount_minor=1000,
                posted_on=date(2026, 7, 22),
            )
            await _seed_row(
                session,
                user_id=user_id,
                statement=statement,
                category_id=category.id,
                amount_minor=500,
                posted_on=date(2026, 7, 23),
                excluded=True,
            )

            await AggregateRefresher(session).refresh_statement(statement.id)

            totals = await self._totals(session, user_id)
            assert totals[(card.id, category.id, date(2026, 7, 1))] == 1000

    async def test_unclassified_rows_are_never_folded_into_a_category(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card = await _seed_card(session, user_id)
            statement = await _seed_statement(session, user_id, card)
            await _seed_row(
                session,
                user_id=user_id,
                statement=statement,
                category_id=None,
                amount_minor=1000,
                posted_on=date(2026, 7, 22),
            )

            await AggregateRefresher(session).refresh_statement(statement.id)

            totals = await self._totals(session, user_id)
            assert totals == {}

    async def test_running_it_twice_gives_the_same_answer_as_once(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            card = await _seed_card(session, user_id)
            category = await _seed_category(session, user_id, slug="groceries-c")
            statement = await _seed_statement(session, user_id, card)
            await _seed_row(
                session,
                user_id=user_id,
                statement=statement,
                category_id=category.id,
                amount_minor=1000,
                posted_on=date(2026, 7, 22),
            )

            refresher = AggregateRefresher(session)
            await refresher.refresh_statement(statement.id)
            await refresher.refresh_statement(statement.id)

            totals = await self._totals(session, user_id)
            assert totals[(card.id, category.id, date(2026, 7, 1))] == 1000

    @staticmethod
    async def _totals(
        session: AsyncSession, user_id: uuid.UUID
    ) -> dict[tuple[uuid.UUID, uuid.UUID, date], int]:
        from sqlalchemy import select

        from app.db.models import CategoryMonthlyTotal

        result = await session.execute(
            select(CategoryMonthlyTotal).where(CategoryMonthlyTotal.user_id == user_id)
        )
        return {
            (row.card_id, row.category_id, row.month): row.total_minor
            for row in result.scalars().all()
        }
