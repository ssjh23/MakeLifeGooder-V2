"""Test harness.

Two rules shape everything here.

**The test environment is as hostile as production.** Tests connect as
``ledger_app`` through the transaction-mode pooler, exactly as the deployed API
does. Connecting as the migration role would be more convenient and would make
every row level security test pass while proving nothing, because a table's
owner is exempt from its policies.

**Nothing reaches a paid or remote service.** The LLM adapter is the
deterministic fake, and object storage is MinIO from ``docker compose``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Set before app.config is imported anywhere, so the cached Settings object is
# built against the test database rather than the development one.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LLM_ADAPTER", "fake")


def _test_url(name: str, fallback: str) -> str:
    return os.environ.get(name) or fallback


TEST_DATABASE_URL = _test_url(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://ledger_app:ledger_app_pw@localhost:6432/ledger_test",
)
TEST_MIGRATOR_URL = _test_url(
    "TEST_DATABASE_MIGRATOR_URL",
    "postgresql+psycopg://ledger_migrator:ledger_migrator_pw@localhost:5432/ledger_test",
)
TEST_SESSION_MODE_URL = _test_url(
    "TEST_DATABASE_SESSION_MODE_URL",
    "postgresql+asyncpg://ledger_app:ledger_app_pw@localhost:6433/ledger_test",
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DATABASE_MIGRATOR_URL"] = TEST_MIGRATOR_URL
os.environ["DATABASE_WORKER_URL"] = TEST_DATABASE_URL.replace(":6432", ":5432")
os.environ["DATABASE_SESSION_MODE_URL"] = TEST_SESSION_MODE_URL


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    """Run migrations once per session, as the owning role.

    Alembic rather than ``metadata.create_all``: the migration is where the
    policies, the grants and the ``FORCE ROW LEVEL SECURITY`` live, and a test
    database built from the metadata alone would have tables with no isolation
    on them. Every security test would then pass against a schema that is not
    the one being deployed.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "ALEMBIC_DATABASE_URL": TEST_MIGRATOR_URL},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "Migrations failed. Is `docker compose up -d` running?\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    yield


@pytest_asyncio.fixture
async def clean_database(migrated_database: None) -> AsyncIterator[None]:
    """Empty every table between tests.

    TRUNCATE as the owning role, because the application role cannot truncate
    and should not be able to. Restarting identities and cascading keeps the
    reset total, so a test never inherits another's rows.
    """
    engine = create_async_engine(
        TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://"),
    )
    async with engine.begin() as connection:
        tables = await connection.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        names = [row[0] for row in tables]
        if names:
            await connection.execute(
                text(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE")
            )
    await engine.dispose()
    yield


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_engine(clean_database: None) -> AsyncIterator[object]:
    """Engine configured exactly as the API's.

    Through the pooler, as the non-owning role, with the prepared statement
    cache disabled. Any divergence here would make the isolation suite a test
    of something other than the deployed configuration.
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"statement_cache_size": 0},
        pool_size=2,
        max_overflow=0,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_for_app(app_engine: object) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(clean_database: None) -> AsyncIterator[AsyncClient]:
    """An HTTP client against the real application.

    In-process via ASGI, so there is no server to start, but the full
    middleware, dependency and exception-handler stack runs. A test that
    bypassed the app object would not exercise the session cookie, the request
    id or the error envelope, which are the parts most likely to break.
    """
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest_asyncio.fixture
async def user_a(client: AsyncClient) -> dict[str, str]:
    return await _register(client, "alice@example.com")


@pytest_asyncio.fixture
async def user_b(client: AsyncClient) -> dict[str, str]:
    """A second tenant.

    Present in every isolation test, because tenant isolation cannot be
    demonstrated with one user: a query returning the right rows for the only
    user in the database proves nothing at all.
    """
    return await _register(client, "bob@example.com")


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery", "name": email.split("@")[0]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "id": body["id"],
        "email": email,
        "cookie": response.cookies.get("ledger_session", ""),
    }


@pytest.fixture
def auth(user_a: dict[str, str]) -> dict[str, str]:
    """Cookie header for the primary test user."""
    return {"Cookie": f"ledger_session={user_a['cookie']}"}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm() -> object:
    from app.classify.llm.fake import FakeLLMAdapter

    return FakeLLMAdapter()


@pytest.fixture
def unavailable_llm() -> object:
    from app.classify.llm.fake import FailingLLMAdapter

    return FailingLLMAdapter()


@pytest.fixture
def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
