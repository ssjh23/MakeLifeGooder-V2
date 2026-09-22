"""BUILD STEPS 8.1 and 8.2: rule preview, create, conflicts and reapply.

Statements reach here the same way ``test_review.py`` builds them: register,
extract, commit, classify (with a stub or declining adapter, so rows land
unclassified and a rule has something real to do), aggregate.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.extract.base import ParsedRow
from app.worker.tasks import reapply_rules
from tests.conftest import TEST_MIGRATOR_URL
from tests.integration.test_cascade import DecliningLLMAdapter
from tests.integration.test_dashboard import _system_category_id
from tests.integration.test_review import _prepare_statement, _row_ids

pytestmark = pytest.mark.integration


async def _category_id(*, exclude: str | None = None) -> str:
    for slug in ("food-drink", "transport", "groceries", "shopping", "other"):
        cid = await _system_category_id(slug)
        if cid != exclude:
            return cid
    raise AssertionError("no system category found")


async def _row_category(user_id: str, statement_id: str) -> list[dict[str, object]]:
    engine = create_async_engine(TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_id}
            )
            result = await connection.execute(
                text(
                    "SELECT id, category_id, classified_by, rule_id FROM transactions "
                    "WHERE statement_id = CAST(:sid AS uuid)"
                ),
                {"sid": statement_id},
            )
            rows = []
            for row in result:
                data = dict(row._mapping)
                for key in ("id", "category_id", "rule_id"):
                    if data[key] is not None:
                        data[key] = str(data[key])
                rows.append(data)
            return rows
    finally:
        await engine.dispose()


class TestPreview:
    async def test_previews_matches_without_changing_anything(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        response = await client.post(
            "/api/v1/rules/preview",
            json={"pattern": descriptor_key, "match_type": "exact"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["matches"] == 1
        assert body["already_in_category"] == 0
        assert body["would_relabel_manual"] == 0

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] is None  # preview changed nothing


class TestCreate:
    @pytest.mark.p0
    async def test_creating_a_rule_categorises_matching_unclassified_rows(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        category_id = await _category_id()

        response = await client.post(
            "/api/v1/rules",
            json={"pattern": descriptor_key, "match_type": "exact", "category_id": category_id},
            headers=auth,
        )
        assert response.status_code == 201, response.text
        assert response.json()["rows_matched"] == 1

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == category_id
        assert after[0]["rule_id"] == response.json()["id"]

    @pytest.mark.p0
    async def test_a_rule_never_overwrites_a_manual_override(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        chosen_category = await _category_id()

        classify = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"category_id": chosen_category, "create_rule": False},
            headers=auth,
        )
        assert classify.status_code == 200, classify.text

        other_category = await _category_id(exclude=chosen_category)
        rule = await client.post(
            "/api/v1/rules",
            json={"pattern": descriptor_key, "match_type": "exact", "category_id": other_category, "scope": "backfill"},
            headers=auth,
        )
        assert rule.status_code == 201, rule.text
        assert rule.json()["rows_matched"] == 0  # the override row is not one of them

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == chosen_category
        assert after[0]["classified_by"] == "override"

    @pytest.mark.p0
    async def test_a_rule_matching_only_the_raw_line_still_categorises_the_row(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """normalise()'s processor-prefix fallback collapses this raw line
        down to just "smp" -- a rule can only ever reach the merchant name
        by also matching description_raw, not descriptor_key."""
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP**OLD TEA HUT (CHANGI SG Ref No. : 745123456",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        assert rows[0]["descriptor_key"] == "smp"
        category_id = await _category_id()

        response = await client.post(
            "/api/v1/rules",
            json={"pattern": "old tea hut", "match_type": "contains", "category_id": category_id},
            headers=auth,
        )
        assert response.status_code == 201, response.text
        assert response.json()["rows_matched"] == 1

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == category_id
        assert after[0]["rule_id"] == response.json()["id"]

    async def test_two_rules_overlapping_only_via_raw_text_are_a_409_conflict(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP**OLD TEA HUT (CHANGI SG Ref No. : 745123456",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        await _row_ids(user_a["id"], statement_id)
        category_id = await _category_id()

        first = await client.post(
            "/api/v1/rules",
            json={"pattern": "smp", "match_type": "contains", "category_id": category_id},
            headers=auth,
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/rules",
            json={
                "pattern": "old tea hut",
                "match_type": "contains",
                "category_id": await _category_id(exclude=category_id),
            },
            headers=auth,
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "rule_conflict"
        assert second.json()["error"]["details"]["existing_id"] == first.json()["id"]

    @pytest.mark.p0
    async def test_a_second_overlapping_rule_is_a_409_conflict(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        category_id = await _category_id()

        first = await client.post(
            "/api/v1/rules",
            json={"pattern": descriptor_key, "match_type": "exact", "category_id": category_id},
            headers=auth,
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/rules",
            json={
                "pattern": descriptor_key,
                "match_type": "exact",
                "category_id": await _category_id(exclude=category_id),
            },
            headers=auth,
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "rule_conflict"

    async def test_future_scope_does_not_touch_already_classified_rows(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        first_category = await _category_id()

        await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"category_id": first_category, "create_rule": False},
            headers=auth,
        )
        # Undo the override so the row is a plain classified (non-override)
        # row a scope=future rule still must not touch.
        engine = create_async_engine(TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://"))
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.user_id', :uid, true)"), {"uid": user_a["id"]}
                )
                await connection.execute(
                    text(
                        "UPDATE transactions SET classified_by = 'merchant_default' "
                        "WHERE statement_id = CAST(:sid AS uuid)"
                    ),
                    {"sid": statement_id},
                )
        finally:
            await engine.dispose()

        other_category = await _category_id(exclude=first_category)
        rule = await client.post(
            "/api/v1/rules",
            json={
                "pattern": descriptor_key,
                "match_type": "exact",
                "category_id": other_category,
                "scope": "future",
            },
            headers=auth,
        )
        assert rule.status_code == 201, rule.text
        assert rule.json()["rows_matched"] == 0

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == first_category


class TestDelete:
    async def test_deleting_a_rule_reverts_its_rows_to_unclassified(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        category_id = await _category_id()

        rule = await client.post(
            "/api/v1/rules",
            json={"pattern": descriptor_key, "match_type": "exact", "category_id": category_id},
            headers=auth,
        )
        rule_id = rule.json()["id"]

        delete = await client.delete(f"/api/v1/rules/{rule_id}", headers=auth)
        assert delete.status_code == 204, delete.text

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] is None
        assert after[0]["rule_id"] is None


class TestReapply:
    @pytest.mark.p0
    async def test_TC_RULE_009_keep_overrides_true_preserves_manual_decisions(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        chosen_category = await _category_id()

        await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"category_id": chosen_category, "create_rule": False},
            headers=auth,
        )
        other_category = await _category_id(exclude=chosen_category)
        await client.post(
            "/api/v1/rules",
            json={
                "pattern": descriptor_key,
                "match_type": "exact",
                "category_id": other_category,
                "scope": "future",
            },
            headers=auth,
        )

        await reapply_rules(user_id=user_a["id"], keep_overrides=True)

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == chosen_category
        assert after[0]["classified_by"] == "override"

    async def test_keep_overrides_false_discards_manual_decisions(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        chosen_category = await _category_id()

        await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"category_id": chosen_category, "create_rule": False},
            headers=auth,
        )
        other_category = await _category_id(exclude=chosen_category)
        await client.post(
            "/api/v1/rules",
            json={
                "pattern": descriptor_key,
                "match_type": "exact",
                "category_id": other_category,
                "scope": "future",
            },
            headers=auth,
        )

        await reapply_rules(user_id=user_a["id"], keep_overrides=False)

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == other_category
        assert after[0]["classified_by"] == "override"  # stamp is untouched; only category/rule move

    async def test_reapply_preview_counts_without_changing_anything(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="KOUFU FOOD COURT", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        category_id = await _category_id()

        await client.post(
            "/api/v1/rules",
            json={"pattern": descriptor_key, "match_type": "exact", "category_id": category_id},
            headers=auth,
        )

        response = await client.post("/api/v1/rules/reapply/preview", json={}, headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()["overrides_at_risk"] == 0

        after = await _row_category(user_a["id"], statement_id)
        assert after[0]["category_id"] == category_id  # unaffected by the preview call itself
