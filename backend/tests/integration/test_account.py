"""BUILD STEP 9.3: inventory, export and account deletion. Screens 08 to 08c."""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.extract.base import ParsedRow
from app.worker.tasks import purge_account
from tests.conftest import TEST_MIGRATOR_URL
from tests.integration.test_review import _prepare_statement

pytestmark = pytest.mark.integration


class TestInventory:
    async def test_counts_match_what_was_actually_created(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await client.post("/api/v1/categories", json={"name": "My Category"}, headers=auth)

        response = await client.get("/api/v1/account", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["statements"] == 1
        assert body["transactions"] == 1
        assert body["cards"] == 1
        assert body["categories"] == 1


class TestExport:
    @pytest.mark.p0
    async def test_TC_ACCT_005_export_declares_columns_and_count_before_download(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
        )

        response = await client.post("/api/v1/exports", json={"scope": "all"}, headers=auth)
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "ready"
        assert body["row_count"] == 1
        assert "posted_on" in body["columns"]
        assert body["download_url"]

        fetched = await client.get(f"/api/v1/exports/{body['export_id']}", headers=auth)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "ready"

    async def test_a_scoped_export_requires_a_scope_id(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.post("/api/v1/exports", json={"scope": "card"}, headers=auth)
        assert response.status_code == 400, response.text


class TestDeleteAccount:
    @pytest.mark.p0
    @pytest.mark.security
    async def test_TC_ACCT_007_confirmation_must_be_the_exact_literal(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.request(
            "DELETE", "/api/v1/account", json={"confirmation": "delete"}, headers=auth
        )
        assert response.status_code == 400, response.text

    @pytest.mark.p0
    async def test_confirmed_deletion_marks_the_account_and_enqueues_the_purge(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        response = await client.request(
            "DELETE", "/api/v1/account", json={"confirmation": "DELETE"}, headers=auth
        )
        assert response.status_code == 202, response.text

        engine = create_async_engine(
            TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        )
        try:
            async with engine.connect() as connection:
                deleted_at = await connection.scalar(
                    text("SELECT deleted_at FROM users WHERE id = CAST(:uid AS uuid)"),
                    {"uid": user_a["id"]},
                )
                job_count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'account.purge' "
                        "AND args ->> 'user_id' = :uid"
                    ),
                    {"uid": user_a["id"]},
                )
        finally:
            await engine.dispose()

        assert deleted_at is not None
        assert job_count == 1

    @pytest.mark.p0
    async def test_TC_ACCT_010_the_purge_job_removes_everything_and_is_safe_to_rerun(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await client.request("DELETE", "/api/v1/account", json={"confirmation": "DELETE"}, headers=auth)

        await purge_account(user_id=user_a["id"])
        # Re-running after the row (and thus the statement list) is already
        # gone must not raise -- the whole point of TC-ACCT-010.
        await purge_account(user_id=user_a["id"])

        engine = create_async_engine(
            TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://")
        )
        try:
            async with engine.connect() as connection:
                user_count = await connection.scalar(
                    text("SELECT count(*) FROM users WHERE id = CAST(:uid AS uuid)"),
                    {"uid": user_a["id"]},
                )
                await connection.execute(
                    text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_a["id"]}
                )
                statement_count = await connection.scalar(
                    text("SELECT count(*) FROM statements WHERE id = CAST(:sid AS uuid)"),
                    {"sid": statement_id},
                )
        finally:
            await engine.dispose()

        assert user_count == 0
        assert statement_count == 0
