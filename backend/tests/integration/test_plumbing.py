"""The seams. GREEN - this suite is what makes the scaffold worth handing over.

Nothing here tests business logic. Every case asserts that one architectural
joint works, so that when the logic is written it can be written against a
system already known to hold together.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


class TestHealth:
    async def test_TC_PERF_005_liveness_does_not_touch_the_database(
        self, client: AsyncClient
    ) -> None:
        """Liveness must not depend on Postgres.

        A probe that checks the database turns a thirty-second blip into a
        restart loop across every container, which converts a brief degradation
        into a total outage at exactly the worst moment.
        """
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_TC_PERF_006_readiness_does_check_dependencies(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["database"] is True


class TestRequestContext:
    async def test_TC_TEL_001_every_response_carries_a_request_id(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/health")
        assert response.headers.get("X-Request-ID")

    async def test_request_ids_are_unique_per_request(self, client: AsyncClient) -> None:
        first = await client.get("/health")
        second = await client.get("/health")
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]

    async def test_TC_FAIL_011_errors_carry_the_request_id_in_the_body(
        self, client: AsyncClient
    ) -> None:
        """So a bug report arrives with its own correlation handle."""
        response = await client.get("/api/v1/me")
        assert response.status_code == 401
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


class TestErrorEnvelope:
    async def test_unauthenticated_is_401_not_500(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/dashboard")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    async def test_malformed_body_is_400_not_422(self, client: AsyncClient) -> None:
        """422 is reserved for a valid request in an invalid state.

        Keeping the two apart is what lets a client tell "you sent nonsense"
        from "you cannot do that yet", which are different problems with
        different fixes.
        """
        response = await client.post("/api/v1/auth/register", json={"email": "not-an-email"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "malformed_request"

    async def test_unwritten_logic_is_501_not_500(self, client: AsyncClient) -> None:
        """The scaffold's honest answer.

        An endpoint whose service method is not written yet returns 501, so it
        is distinguishable from one that is broken. Password reset delivery is
        deliberately deferred rather than stubbed with fake business logic
        (see `AuthService.request_password_reset`'s docstring: no mail
        transport is chosen and no screen is drawn), which is what keeps this
        example evergreen -- it is not part of the numbered build order, so
        unlike the product endpoints it is never expected to "land" and this
        test does not need to move to a new endpoint as each step is finished.

        OIDC (also ADR-008) used to be this test's example instead: it is
        implemented now (`app/api/routers/auth.py`), which is exactly the
        situation this docstring describes -- a deferred seam moving to a new
        endpoint once one implementation lands.
        """
        response = await client.post(
            "/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"}
        )
        assert response.status_code == 501


class TestAuthentication:
    async def test_TC_AUTH_001_register_then_use_the_session(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        response = await client.get(
            "/api/v1/me", headers={"Cookie": f"ledger_session={user_a['cookie']}"}
        )
        assert response.status_code == 200
        assert response.json()["email"] == "alice@example.com"

    async def test_TC_AUTH_002_password_under_twelve_characters_is_rejected(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "short@example.com", "password": "elevenchar", "name": "Short"},
        )
        assert response.status_code == 400

    async def test_TC_AUTH_003_twelve_characters_is_accepted(self, client: AsyncClient) -> None:
        """The boundary is inclusive."""
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "twelve@example.com", "password": "a" * 12, "name": "Twelve"},
        )
        assert response.status_code == 201

    async def test_TC_AUTH_004_duplicate_email_is_rejected(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "alice@example.com", "password": "another-long-one", "name": "Alice"},
        )
        assert response.status_code == 409

    async def test_email_casing_does_not_create_a_second_account(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        """citext on users.email. Without it one person gets two ledgers and
        neither is complete."""
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "ALICE@example.com", "password": "another-long-one", "name": "Alice"},
        )
        assert response.status_code == 409

    async def test_TC_AUTH_006_wrong_password_and_unknown_email_are_indistinguishable(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        """Otherwise the login form is an account enumeration oracle."""
        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "wrong-password-here"},
        )
        unknown = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password-here"},
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    async def test_TC_AUTH_010_tampered_cookie_is_rejected(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        tampered = user_a["cookie"][:-1] + ("a" if user_a["cookie"][-1] != "a" else "b")
        response = await client.get("/api/v1/me", headers={"Cookie": f"ledger_session={tampered}"})
        assert response.status_code == 401

    async def test_TC_AUTH_011_has_statements_is_false_for_a_new_account(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """Gates the locked navigation on screen 02. It flips on the first
        commit, not the first upload."""
        response = await client.get("/api/v1/me", headers=auth)
        assert response.json()["has_statements"] is False

    async def test_TC_AUTH_014_logout_clears_the_session(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        headers = {"Cookie": f"ledger_session={user_a['cookie']}"}
        logout = await client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 204
        assert "ledger_session=" in logout.headers.get("set-cookie", "")


class TestOpenAPI:
    async def test_schema_is_generated_and_covers_every_area(
        self, client: AsyncClient
    ) -> None:
        """The frontend client is generated from this document, so an empty or
        partial schema is a broken build rather than a missing nicety."""
        response = await client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        for expected in (
            "/api/v1/auth/login",
            "/api/v1/statements",
            "/api/v1/statements/{statement_id}/commit",
            "/api/v1/dashboard",
            "/api/v1/rules",
            "/api/v1/account",
        ):
            assert expected in paths, f"{expected} missing from the OpenAPI document"

    async def test_no_endpoint_takes_a_user_id_path_parameter(
        self, client: AsyncClient
    ) -> None:
        """Row level security scopes every query, so a user id in a path would
        be either redundant or a way to ask for someone else's data."""
        paths = (await client.get("/api/v1/openapi.json")).json()["paths"]
        assert not [p for p in paths if "{user_id}" in p]
