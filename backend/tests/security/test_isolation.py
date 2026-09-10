"""Tenant isolation. GREEN - these assert the scaffold, not unwritten logic.

The premise of ADR-008 is that isolation must survive application *bugs*, not
just correct code. Both approaches are equivalent when the code is right, so the
choice is entirely about the failure mode:

  app-layer filtering   a forgotten WHERE returns another tenant's rows.
                        Silent, and the worst possible outcome.
  row level security    the same bug returns zero rows. Loud, and caught in
                        development.

These tests demonstrate that property rather than restating it.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import TENANT_SETTING, tenant_session

pytestmark = [pytest.mark.security, pytest.mark.integration]


@pytest.mark.p0
async def test_TC_SEC_006_tenant_setting_is_transaction_local(
    sessionmaker_for_app: async_sessionmaker[AsyncSession],
) -> None:
    """``app.user_id`` dies with its transaction.

    This is the property that makes a pooled connection safe to reuse. Without
    it, the value set for one request would still be there for the next request
    that borrows the same backend.
    """
    user_id = uuid.uuid4()

    async with tenant_session(sessionmaker_for_app, user_id) as session:
        inside = await session.execute(
            text("SELECT current_setting(:name, true)"), {"name": TENANT_SETTING}
        )
        assert inside.scalar_one() == str(user_id)

    async with sessionmaker_for_app() as session:
        after = await session.execute(
            text("SELECT current_setting(:name, true)"), {"name": TENANT_SETTING}
        )
        assert after.scalar_one() in (None, ""), (
            "The tenant setting outlived its transaction. Something is using "
            "SET rather than SET LOCAL, or the pooler is not in transaction mode."
        )


@pytest.mark.p0
async def test_TC_SEC_003_query_without_a_filter_returns_nothing(
    sessionmaker_for_app: async_sessionmaker[AsyncSession],
) -> None:
    """The whole argument for RLS, demonstrated.

    A deliberately unfiltered query - the bug this is meant to survive - returns
    zero rows for another tenant rather than their data.
    """
    owner = uuid.uuid4()
    stranger = uuid.uuid4()

    async with tenant_session(sessionmaker_for_app, owner) as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, display_name) "
                "VALUES (:id, :email, 'Owner') ON CONFLICT DO NOTHING"
            ),
            {"id": owner, "email": f"{owner}@example.com"},
        )
        await session.execute(
            text(
                "INSERT INTO accounts (user_id, institution, account_kind, currency) "
                "VALUES (:uid, 'DBS', 'credit', 'SGD')"
            ),
            {"uid": owner},
        )

    # No WHERE clause at all. Under app-layer filtering this is the bug that
    # leaks; under RLS it is simply empty.
    async with tenant_session(sessionmaker_for_app, stranger) as session:
        rows = await session.execute(text("SELECT id FROM accounts"))
        assert rows.fetchall() == []

    async with tenant_session(sessionmaker_for_app, owner) as session:
        rows = await session.execute(text("SELECT id FROM accounts"))
        assert len(rows.fetchall()) == 1


@pytest.mark.p0
async def test_unscoped_session_sees_no_tenant_rows(
    sessionmaker_for_app: async_sessionmaker[AsyncSession],
) -> None:
    """With no tenant established, tenant tables are empty.

    Returning nothing is the correct answer for a query that never said who it
    was for, and it means a forgotten dependency fails closed.
    """
    owner = uuid.uuid4()
    async with tenant_session(sessionmaker_for_app, owner) as session:
        await session.execute(
            text("INSERT INTO users (id, email) VALUES (:id, :email)"),
            {"id": owner, "email": f"{owner}@example.com"},
        )
        await session.execute(
            text(
                "INSERT INTO accounts (user_id, institution, account_kind, currency) "
                "VALUES (:uid, 'OCBC', 'savings', 'SGD')"
            ),
            {"uid": owner},
        )

    async with sessionmaker_for_app() as session:
        rows = await session.execute(text("SELECT id FROM accounts"))
        assert rows.fetchall() == []


@pytest.mark.p0
async def test_application_role_does_not_own_the_tables(
    sessionmaker_for_app: async_sessionmaker[AsyncSession],
) -> None:
    """The precondition everything else here depends on.

    PostgreSQL exempts a table's owner from its policies. If the application
    connected as the migration role, every test above would pass while no
    isolation existed at all, so this asserts the premise directly.
    """
    async with sessionmaker_for_app() as session:
        result = await session.execute(
            text(
                "SELECT tableowner, current_user FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = 'transactions'"
            )
        )
        owner, current_user = result.one()
        assert owner != current_user, (
            f"The application is connected as {current_user}, which owns the tables. "
            "Row level security does not apply to a table's owner."
        )


@pytest.mark.p0
async def test_rls_is_enabled_and_forced_on_every_tenant_table(
    sessionmaker_for_app: async_sessionmaker[AsyncSession],
) -> None:
    """Guards against a new tenant table arriving without a policy.

    The schema knows which tables are tenant-scoped; this checks the database
    agrees. Adding a table with a ``user_id`` and forgetting the policy is the
    realistic way this system would lose isolation later.
    """
    from app.db.models import TENANT_TABLES

    async with sessionmaker_for_app() as session:
        result = await session.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(:names)"
            ),
            {"names": list(TENANT_TABLES)},
        )
        found = {row[0]: (row[1], row[2]) for row in result}

    for table in TENANT_TABLES:
        enabled, forced = found[table]
        assert enabled, f"{table} has no row level security"
        assert forced, f"{table} does not FORCE row level security"


@pytest.mark.p0
@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_SESSION_MODE_URL"),
    reason="Session-mode pooler not configured; start docker compose.",
)
async def test_TC_SEC_005_session_mode_leaks_session_state() -> None:
    """The canary. Demonstrates what ADR-008 forbids.

    Through a session-mode pooler a backend is pinned to one client for its
    whole life, so a plain ``SET`` survives into the next transaction on that
    connection. That is why tenant identity is carried with ``SET LOCAL``, and
    why the application's pooler must be in transaction mode.

    If this test ever starts failing, the assumption behind the deployment
    requirement has changed and the requirement needs revisiting - which is
    exactly what a canary is for.
    """
    url = os.environ["TEST_DATABASE_SESSION_MODE_URL"]
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0}, pool_size=1)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET app.canary = 'leaked'"))
            await connection.commit()

            # A separate transaction on the same pooled connection.
            leaked = await connection.execute(text("SELECT current_setting('app.canary', true)"))
            assert leaked.scalar_one() == "leaked", (
                "Session-mode pooling no longer preserves session state across "
                "transactions. The reasoning in ADR-008 should be re-checked."
            )
    finally:
        await engine.dispose()
