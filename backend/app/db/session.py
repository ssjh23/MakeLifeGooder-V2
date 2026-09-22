"""Engines and the tenant-scoped session.

Three engines, because three callers need different guarantees.

  application  through pgbouncer in transaction mode, as ``ledger_app``
  worker       direct to Postgres, because LISTEN/NOTIFY does not survive a
               transaction-mode pooler
  migrations   handled by Alembic, synchronously, as the owning role

The single most important function in the codebase is
:func:`tenant_session`. Read its docstring before changing anything here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings

#: The Postgres run-time parameter carrying tenant identity. Every row level
#: security policy resolves to this value.
TENANT_SETTING = "app.user_id"


def _connect_args() -> dict[str, object]:
    """asyncpg options required when a transaction-mode pooler is in front.

    Server-side prepared statements are bound to a backend connection. Under
    transaction pooling that connection is handed to another client at COMMIT,
    so a cached statement handle can be used against a backend that has never
    seen it. The symptom is an intermittent ``InvalidSQLStatementNameError``
    that reads like a driver bug and disappears under low load, which is the
    worst possible failure mode to debug.

    Setting the cache to zero costs a re-plan per statement. At this system's
    load that is not measurable.
    """
    return {"statement_cache_size": 0}


@lru_cache
def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Engine used by the API. Goes through the transaction-mode pooler."""
    settings = settings or get_settings()
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args=_connect_args(),
        echo=False,
    )


async def current_tenant_id(session: AsyncSession) -> uuid.UUID:
    """The ``app.user_id`` :func:`tenant_session` set with ``SET LOCAL``.

    For the rare caller that has to hand the tenant off somewhere RLS cannot
    follow it -- a job payload, which is read back by a worker process on a
    connection of its own. Everything else should keep relying on RLS rather
    than reading this back out.
    """
    result = await session.execute(text("SELECT current_setting('app.user_id')::uuid"))
    return cast(uuid.UUID, result.scalar_one())


@lru_cache
def get_worker_engine(settings: Settings | None = None) -> AsyncEngine:
    """Engine used by the worker. Connects directly to Postgres.

    procrastinate waits on LISTEN/NOTIFY to pick up work the moment it is
    enqueued. A transaction-mode pooler releases the backend at COMMIT, so a
    listener registered on it is not reachable and the worker silently falls
    back to polling. Jobs still run, just later, which makes this the kind of
    misconfiguration that looks like "the queue feels slow" for a week.
    """
    settings = settings or get_settings()
    return create_async_engine(
        str(settings.database_worker_url),
        pool_pre_ping=True,
        connect_args=_connect_args(),
        echo=False,
    )


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@lru_cache
def get_worker_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """The worker's own sessionmaker, direct to Postgres, cached like the API's.

    A worker task knows only ``statement_id`` and (per ``register()``, so the
    task can reach ``tenant_session`` before it has queried anything) the
    tenant's ``user_id``. This is what it opens that tenant session against.
    """
    return get_sessionmaker(get_worker_engine())


@asynccontextmanager
async def tenant_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction scoped to one tenant.

    This is the security seam. Everything downstream, every service and every
    repository, is written as though the system were single-tenant, and this
    function is what makes that safe.

    Three properties, and all three are required:

    1. **The setting is transaction-local.** ``set_config(..., true)`` is the
       parameterised form of ``SET LOCAL``, so the value dies at COMMIT or
       ROLLBACK and cannot be observed by the next transaction on this
       connection. A plain ``SET`` would persist on the pooled backend and be
       visible to a different user's request. There is a test that scans this
       repository to keep it that way.

    2. **It is parameterised.** ``SET LOCAL`` does not accept bind parameters,
       so the tempting form is an f-string, and that is a SQL injection site
       fed by a session cookie. ``set_config`` takes a parameter and is the
       reason this is a ``SELECT``.

    3. **The connection is not the table owner.** PostgreSQL exempts a table's
       owner from row level security. The application connects as
       ``ledger_app``, which owns nothing; the migration role owns everything.
       Connect with the wrong role and every isolation test still passes while
       no isolation exists.

    A missing ``WHERE user_id`` under these policies returns zero rows. That is
    the whole argument for putting isolation in the database: the bug is loud
    rather than silent, and it is caught in development rather than by a user
    seeing somebody else's spending.
    """
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": TENANT_SETTING, "value": str(user_id)},
        )
        yield session


@asynccontextmanager
async def unscoped_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session with no tenant set.

    For the few operations that are genuinely not tenant-scoped: registration
    and sign-in, which must find a user before a user is known, and reads of
    the shared merchant and alias tables. Row level security still applies to
    tenant tables, and with no ``app.user_id`` set they return nothing, which
    is the correct answer rather than an inconvenience.
    """
    async with sessionmaker() as session, session.begin():
        yield session
