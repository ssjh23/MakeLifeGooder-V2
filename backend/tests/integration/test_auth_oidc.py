"""OIDC (Auth0), ADR-008.

Two things are tested here rather than a full mocked Authorization Code
exchange: the config gate every request through `/auth/oidc/*` passes
through first, and `AuthService.login_with_oidc`, which is where an Auth0
identity actually becomes (or finds) a local account. That method is the
part with real branches to get wrong -- everything upstream of it is the SDK
running a standard, unmodified OIDC flow against Auth0's own servers, which
is not this codebase's to re-verify.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import Unauthenticated
from app.db.session import unscoped_session
from app.services.auth import AuthService

pytestmark = pytest.mark.integration


class TestOidcNotConfigured:
    """The default state of every local dev box and the test suite."""

    async def test_start_is_503_when_unconfigured(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/oidc/start")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "oidc_not_configured"

    async def test_callback_is_503_when_unconfigured(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/oidc/callback?code=x&state=y")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "oidc_not_configured"


class TestLoginWithOidc:
    """`AuthService.login_with_oidc`: the Auth0-identity-to-account mapping."""

    async def test_creates_an_account_for_a_new_verified_email(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        async with unscoped_session(sessionmaker_for_app) as session:
            user_id = await AuthService(session).login_with_oidc(
                subject="auth0|new-user",
                email="new-oidc-user@example.com",
                email_verified=True,
                name="New User",
            )
            resolved_id, email, name, _ = await AuthService(session).me(user_id)
            assert resolved_id == user_id
            assert email == "new-oidc-user@example.com"
            assert name == "New User"

    async def test_links_an_existing_password_account_by_verified_email(
        self, client: AsyncClient, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """One person, two paths in, one account -- not two."""
        register = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "both-paths@example.com",
                "password": "a-long-enough-password",
                "name": "Both Paths",
            },
        )
        assert register.status_code == 201
        password_account_id = register.json()["id"]

        async with unscoped_session(sessionmaker_for_app) as session:
            oidc_user_id = await AuthService(session).login_with_oidc(
                subject="auth0|both-paths",
                email="both-paths@example.com",
                email_verified=True,
                name="Both Paths",
            )

        assert str(oidc_user_id) == password_account_id

    async def test_rejects_an_unverified_email(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        async with unscoped_session(sessionmaker_for_app) as session:
            with pytest.raises(Unauthenticated):
                await AuthService(session).login_with_oidc(
                    subject="auth0|unverified",
                    email="unverified@example.com",
                    email_verified=False,
                    name=None,
                )

    async def test_rejects_a_missing_email(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        async with unscoped_session(sessionmaker_for_app) as session:
            with pytest.raises(Unauthenticated):
                await AuthService(session).login_with_oidc(
                    subject="auth0|no-email", email=None, email_verified=True, name=None
                )
