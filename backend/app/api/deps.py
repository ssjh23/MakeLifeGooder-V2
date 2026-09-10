"""Dependencies.

``get_db_session`` is the single most important function in the codebase. It is
the reason every service and every repository downstream can be written as
though the system had one user.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import RateLimited, Unauthenticated
from app.api.ratelimit.base import RateLimiter
from app.api.ratelimit.memory import InMemoryRateLimiter
from app.api.security import SessionCodec
from app.config import Settings, get_settings
from app.db.session import get_engine, get_sessionmaker, tenant_session, unscoped_session
from app.storage import ObjectStore, build_object_store

SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache
def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker(get_engine())


@lru_cache
def get_session_codec() -> SessionCodec:
    settings = get_settings()
    return SessionCodec(
        secret=settings.session_secret.get_secret_value(),
        ttl_seconds=settings.session_ttl_seconds,
    )


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """In-process today, Redis when the app is exposed publicly (ADR-010).

    Returned through the Protocol so the swap is a change here and nowhere
    else.
    """
    return InMemoryRateLimiter()


@lru_cache
def get_object_store() -> ObjectStore:
    return build_object_store(get_settings())


StoreDep = Annotated[ObjectStore, Depends(get_object_store)]


async def get_current_user_id(
    request: Request,
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
    settings: SettingsDep,
) -> uuid.UUID:
    """Resolve the caller from the session cookie.

    Absent, expired and tampered cookies all produce the same 401. The
    difference is of no use to a legitimate client and of considerable use to
    an attacker (TC-AUTH-009, TC-AUTH-010).
    """
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise Unauthenticated("No valid session.")
    user_id = codec.read(token)
    if user_id is None:
        raise Unauthenticated("No valid session.")
    return user_id


CurrentUser = Annotated[uuid.UUID, Depends(get_current_user_id)]


async def get_db_session(user_id: CurrentUser) -> AsyncIterator[AsyncSession]:
    """A database session scoped to the authenticated user.

    Opens a transaction on a connection borrowed from the transaction-mode
    pooler and sets ``app.user_id`` for the life of that transaction, which is
    what every row level security policy resolves against.

    Three properties hold together, and removing any one of them removes tenant
    isolation while leaving the tests passing:

      1. the setting is transaction-local, so it cannot be seen by the next
         request on the same pooled connection
      2. it is parameterised rather than interpolated, so a session cookie
         cannot become a SQL injection
      3. the connection belongs to ``ledger_app``, which owns no tables, so the
         policies apply to it at all

    The consequence to remember while writing queries downstream: **do not add
    a ``WHERE user_id`` clause**. The database applies it. A service written as
    single-tenant is correct here, and one that forgets its filter returns zero
    rows rather than somebody else's.
    """
    async with tenant_session(_sessionmaker(), user_id) as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_unscoped_session() -> AsyncIterator[AsyncSession]:
    """A session with no tenant established.

    Only for operations that legitimately precede identity: registration and
    sign-in, which must look a user up before there is a user. Tenant tables
    return nothing through this session, which is the correct answer rather
    than a limitation to work around.
    """
    async with unscoped_session(_sessionmaker()) as session:
        yield session


UnscopedSession = Annotated[AsyncSession, Depends(get_unscoped_session)]


def rate_limit(bucket: str, limit: int, window_seconds: int = 60):  # noqa: ANN201
    """Build a dependency enforcing one named limit.

    Buckets are namespaced, so exhausting the upload limit does not block
    sign-in (TC-RATE-005). The check runs before the endpoint body, so a
    rejected request writes no row and enqueues no job (TC-RATE-006).
    """

    async def _dependency(
        request: Request,
        user_id: CurrentUser,
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    ) -> None:
        verdict = await limiter.check(f"{bucket}:{user_id}", limit, window_seconds)
        # Present on allowed responses too, so a client can pace itself rather
        # than discovering the limit by hitting it (TC-RATE-002).
        request.state.rate_limit = verdict
        if not verdict.allowed:
            raise RateLimited(
                "Too many requests.",
                details={"limit": verdict.limit, "reset": verdict.reset_epoch},
            )

    return Depends(_dependency)
