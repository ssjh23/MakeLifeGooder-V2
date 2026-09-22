"""BUILD STEP 9.2: categories. Category management has no drawn screen in
User Flows -- specified here so the gap is visible in the API rather than
found mid-build."""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.extract.base import ParsedRow
from tests.conftest import TEST_MIGRATOR_URL
from tests.integration.test_dashboard import _system_category_id
from tests.integration.test_review import _prepare_statement, _row_ids

pytestmark = pytest.mark.integration


async def _set_row_category(user_id: str, statement_id: str, category_id: str) -> None:
    """Force a row onto a tenant-created category directly: the cascade
    only ever resolves system categories (5.5's taxonomy), so exercising a
    tenant category's own delete/merge rules needs a row pointed at one by
    hand rather than through classification."""
    engine = create_async_engine(TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_id}
            )
            await connection.execute(
                text(
                    "UPDATE transactions SET category_id = CAST(:cid AS uuid) "
                    "WHERE statement_id = CAST(:sid AS uuid)"
                ),
                {"cid": category_id, "sid": statement_id},
            )
    finally:
        await engine.dispose()


class TestListAndCreate:
    async def test_lists_system_categories_by_default(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/categories", headers=auth)
        assert response.status_code == 200, response.text
        slugs = {c["slug"] for c in response.json()}
        assert "groceries" in slugs
        assert all(c["is_system"] for c in response.json() if c["slug"] == "groceries")

    async def test_creates_a_tenant_category_with_a_derived_slug(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/categories", json={"name": "Side Hustle Income", "kind": "income"}, headers=auth
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["slug"] == "side-hustle-income"
        assert body["is_system"] is False
        assert body["row_count"] == 0

    @pytest.mark.p0
    async def test_a_repeat_name_is_a_409_not_a_500(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """``uq_categories_user_id_slug`` would otherwise reject the second
        insert as a raw, unhandled ``IntegrityError``."""
        first = await client.post(
            "/api/v1/categories", json={"name": "Coffee", "kind": "expense"}, headers=auth
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/categories", json={"name": "Coffee", "kind": "expense"}, headers=auth
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "category_already_exists"


class TestUpdate:
    async def test_renames_without_changing_the_slug(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        created = await client.post(
            "/api/v1/categories", json={"name": "Pets"}, headers=auth
        )
        category_id = created.json()["id"]

        response = await client.patch(
            f"/api/v1/categories/{category_id}", json={"name": "Pet Supplies"}, headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Pet Supplies"
        assert response.json()["slug"] == "pets"

    async def test_a_system_category_cannot_be_renamed(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        category_id = await _system_category_id("groceries")
        response = await client.patch(
            f"/api/v1/categories/{category_id}", json={"name": "Hacked"}, headers=auth
        )
        assert response.status_code == 400, response.text


class TestDeleteAndMerge:
    async def test_deleting_an_empty_category_succeeds(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        created = await client.post("/api/v1/categories", json={"name": "Unused"}, headers=auth)
        category_id = created.json()["id"]

        response = await client.delete(f"/api/v1/categories/{category_id}", headers=auth)
        assert response.status_code == 204, response.text

    @pytest.mark.p0
    async def test_TC_CAT_007_deleting_a_category_with_rows_is_refused(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
        )
        created = await client.post("/api/v1/categories", json={"name": "Tenant Own"}, headers=auth)
        category_id = created.json()["id"]
        await _set_row_category(user_a["id"], statement_id, category_id)

        response = await client.delete(f"/api/v1/categories/{category_id}", headers=auth)
        assert response.status_code == 409, response.text

    async def test_TC_CAT_007_merge_moves_rows_before_the_source_is_deleted(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
        )
        source = await client.post("/api/v1/categories", json={"name": "Source Category"}, headers=auth)
        source_category_id = source.json()["id"]
        target = await client.post("/api/v1/categories", json={"name": "Target Category"}, headers=auth)
        target_category_id = target.json()["id"]
        await _set_row_category(user_a["id"], statement_id, source_category_id)

        response = await client.post(
            f"/api/v1/categories/{source_category_id}/merge",
            json={"into_category_id": target_category_id},
            headers=auth,
        )
        assert response.status_code == 200, response.text

        after = await _row_ids(user_a["id"], statement_id)
        assert str(after[0]["category_id"]) == target_category_id

        gone = await client.delete(f"/api/v1/categories/{source_category_id}", headers=auth)
        # Already gone via the merge; deleting again is a 404, not a 409 --
        # proof the source was actually removed, not merely emptied.
        assert gone.status_code == 404, gone.text
