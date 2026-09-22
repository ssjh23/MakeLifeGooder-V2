"""BUILD STEPS 6.1, 6.2 and 6.3: the review board, duplicates and classify.

Statements reach review the same way the product does: register, extract
(with a stub parser), commit, then the worker's own classify and aggregate
handlers, called directly with no queue -- the same pattern
``test_import_flow.py`` and ``test_cascade.py`` already use.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.classify.llm.fake import FakeLLMAdapter
from app.db.repositories import RuleRepository
from app.db.session import tenant_session
from app.extract.base import ParsedRow
from app.worker.tasks import aggregate_statement, classify_statement
from tests.conftest import TEST_MIGRATOR_URL
from tests.integration.test_cascade import DecliningLLMAdapter
from tests.integration.test_import_flow import (
    StubParser,
    _extract,
    _parsed,
    _register_a_statement,
    _seed_card,
)

pytestmark = pytest.mark.integration


async def _prepare_statement(
    client: AsyncClient,
    auth: dict[str, str],
    user_id: str,
    *,
    rows: list[ParsedRow],
    printed_total_minor: int,
    llm: object | None = None,
    last4: str = "4429",
) -> str:
    """Register, extract, commit, classify and aggregate one statement."""
    card_id = await _seed_card(user_id, last4=last4)
    statement_id = await _register_a_statement(client, auth, card_id=card_id)
    parsed = _parsed(rows=rows, printed_total_minor=printed_total_minor)
    await _extract(statement_id, user_id, StubParser(result=parsed))

    commit = await client.post(f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth)
    assert commit.status_code == 200, commit.text

    await classify_statement(
        statement_id=statement_id, user_id=user_id, llm=llm or FakeLLMAdapter()
    )
    await aggregate_statement(statement_id=statement_id, user_id=user_id)
    return statement_id


async def _row_ids(engine_user_id: str, statement_id: str) -> list[dict[str, object]]:
    engine = create_async_engine(TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :uid, true)"), {"uid": engine_user_id}
            )
            result = await connection.execute(
                text(
                    "SELECT id, description_raw, descriptor_key, category_id, merchant_id, "
                    "classified_by, rule_id FROM transactions "
                    "WHERE statement_id = CAST(:sid AS uuid) ORDER BY posted_on"
                ),
                {"sid": statement_id},
            )
            return [dict(row._mapping) for row in result]
    finally:
        await engine.dispose()


class TestReviewBoard:
    async def test_groups_rows_by_descriptor_and_reconciles_the_footer(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="FAIRPRICE FINEST NEX", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="FAIRPRICE FINEST NEX", amount_minor=500),
            ],
            printed_total_minor=1500,
            llm=FakeLLMAdapter(overrides={}),
        )

        response = await client.get(f"/api/v1/statements/{statement_id}/review", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()

        assert len(body["merchants"]) == 1
        group = body["merchants"][0]
        assert group["row_count"] == 2
        assert group["total"] == "15.00"
        assert group["classified_by"] == "llm"

        footer = body["footer"]
        assert footer["statement_total"] == "15.00"
        assert footer["classified"] == footer["statement_total"]
        assert footer["unassigned"] == "0.00"
        assert footer["can_finish"] is True

    async def test_a_descriptor_the_model_declines_needs_a_category(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="UNRECOGNISABLE SHOP", amount_minor=1000)
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )

        response = await client.get(f"/api/v1/statements/{statement_id}/review", headers=auth)
        body = response.json()

        group = body["merchants"][0]
        assert group["status"] == "new"
        assert group["suggested_category"] is None
        assert body["filters"]["needs_category"] == 1
        assert body["footer"]["can_finish"] is False

    async def test_a_row_a_rule_matched_only_via_raw_text_shows_as_rule_matched(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        """The board's status badge, not just the row's own classification,
        has to see a rule matching via raw text too -- otherwise a merchant
        the pipeline already correctly classified via a rule still shows as
        "new" here, which is exactly as confusing as never having classified
        it at all."""
        categories = (await client.get("/api/v1/categories", headers=auth)).json()
        category_id = categories[0]["id"]

        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            rule = await RuleRepository(session).create(
                pattern="old tea hut",
                match_type="contains",
                category_id=uuid.UUID(category_id),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )

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

        response = await client.get(f"/api/v1/statements/{statement_id}/review", headers=auth)
        assert response.status_code == 200, response.text
        group = response.json()["merchants"][0]
        assert group["status"] == "rule_matched"
        assert group["rule_id"] == str(rule.id)
        assert group["rule_pattern"] == "old tea hut"


class TestDuplicates:
    async def test_a_same_day_same_amount_pair_is_flagged_and_removable(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB RIDE NEX", amount_minor=1200),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB RIDE ORCHARD", amount_minor=1200),
            ],
            printed_total_minor=2400,
        )

        dupes = await client.get(f"/api/v1/statements/{statement_id}/review/duplicates", headers=auth)
        assert dupes.status_code == 200, dupes.text
        pairs = dupes.json()
        assert len(pairs) == 1
        pair = pairs[0]
        assert pair["hours_apart"] == 0.0
        row_to_remove = pair["row_ids"][0]

        resolve = await client.post(
            f"/api/v1/statements/{statement_id}/duplicates/{pair['pair_id']}/resolve",
            json={"action": "remove", "remove_row_id": row_to_remove},
            headers=auth,
        )
        assert resolve.status_code == 200, resolve.text

        after = await client.get(f"/api/v1/statements/{statement_id}/review/duplicates", headers=auth)
        assert after.json() == []

        rows = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        skipped = [r for r in rows.json()["rows"] if r["skipped"]]
        assert len(skipped) == 1
        assert skipped[0]["id"] == row_to_remove

    @pytest.mark.p0
    async def test_TC_TDUP_004_removal_is_undoable_and_the_row_stays_on_the_statement(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB RIDE NEX", amount_minor=1200),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB RIDE ORCHARD", amount_minor=1200),
            ],
            printed_total_minor=2400,
        )
        pairs = (
            await client.get(f"/api/v1/statements/{statement_id}/review/duplicates", headers=auth)
        ).json()
        pair_id = pairs[0]["pair_id"]
        remove_row_id = pairs[0]["row_ids"][0]

        await client.post(
            f"/api/v1/statements/{statement_id}/duplicates/{pair_id}/resolve",
            json={"action": "remove", "remove_row_id": remove_row_id},
            headers=auth,
        )

        undo = await client.post(
            f"/api/v1/statements/{statement_id}/duplicates/{pair_id}/undo", headers=auth
        )
        assert undo.status_code == 200, undo.text

        rows = (await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)).json()
        assert len(rows["rows"]) == 2
        assert all(not r["skipped"] for r in rows["rows"])

    async def test_keep_both_leaves_the_pair_countable(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB RIDE NEX", amount_minor=1200),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB RIDE ORCHARD", amount_minor=1200),
            ],
            printed_total_minor=2400,
        )
        pairs = (
            await client.get(f"/api/v1/statements/{statement_id}/review/duplicates", headers=auth)
        ).json()

        resolve = await client.post(
            f"/api/v1/statements/{statement_id}/duplicates/{pairs[0]['pair_id']}/resolve",
            json={"action": "keep_both"},
            headers=auth,
        )
        assert resolve.status_code == 200, resolve.text

        rows = (await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)).json()
        assert all(not r["skipped"] for r in rows["rows"])


class TestClassify:
    @pytest.mark.p0
    async def test_classifying_a_new_category_writes_an_override_and_categorises_the_row(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A NEW SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]
        assert rows[0]["category_id"] is None

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"new_category_name": "My New Category", "create_rule": False},
            headers=auth,
        )
        assert response.status_code == 200, response.text

        after = await _row_ids(user_a["id"], statement_id)
        assert after[0]["category_id"] is not None

        board = (
            await client.get(f"/api/v1/statements/{statement_id}/review", headers=auth)
        ).json()
        assert board["footer"]["can_finish"] is True

    async def test_exactly_one_of_category_id_or_new_category_name_is_required(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/review/merchants/some-shop/classify",
            json={},
            headers=auth,
        )
        assert response.status_code == 400, response.text

    @pytest.mark.p0
    async def test_a_second_rule_for_the_same_pattern_is_a_409_conflict(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="RULE SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        first = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"new_category_name": "First Category", "create_rule": True},
            headers=auth,
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"new_category_name": "Second Category", "create_rule": True},
            headers=auth,
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "rule_conflict"

    @pytest.mark.p0
    async def test_naming_a_new_category_that_already_exists_is_a_409_not_a_500(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """``uq_categories_user_id_slug`` rejects a second row with the same
        (user_id, slug) at the database level. Two *different* merchants each
        typed into the "+ New category" field with the same name (or two
        names that slugify the same way, e.g. "Coffee" and "COFFEE!") hit it
        on the second attempt -- reproduces the reported 500."""
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="FIRST SHOP", amount_minor=500),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SECOND SHOP", amount_minor=500),
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        first_descriptor = rows[0]["descriptor_key"]
        second_descriptor = rows[1]["descriptor_key"]

        first = await client.post(
            f"/api/v1/review/merchants/{first_descriptor}/classify",
            json={"new_category_name": "Coffee", "create_rule": False},
            headers=auth,
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"/api/v1/review/merchants/{second_descriptor}/classify",
            json={"new_category_name": "Coffee", "create_rule": False},
            headers=auth,
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "category_already_exists"

    async def test_future_scope_does_not_relabel_an_already_classified_row(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Only rows still unclassified pick up a future-scoped decision;
        history stays exactly as the cascade left it."""
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="ALREADY DONE", amount_minor=1000)],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={}),
        )
        before = await _row_ids(user_a["id"], statement_id)
        descriptor_key = before[0]["descriptor_key"]
        original_category = before[0]["category_id"]
        assert original_category is not None

        await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"new_category_name": "Different Category", "create_rule": False, "scope": "future"},
            headers=auth,
        )

        after = await _row_ids(user_a["id"], statement_id)
        assert after[0]["category_id"] == original_category

    @pytest.mark.p0
    async def test_can_create_a_broader_rule_than_an_exact_match(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Same authoring options as the Rules screen's own "New rule" form:
        a person classifying from Review isn't limited to an exact match on
        this one descriptor."""
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB *TRANSPORT SG", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={
                "new_category_name": "Transport",
                "create_rule": True,
                "scope": "future",
                "pattern": "grab",
                "match_type": "contains",
                "options": {"ignore_case": True, "match_negative": False},
            },
            headers=auth,
        )
        assert response.status_code == 200, response.text

        rules = (await client.get("/api/v1/rules", headers=auth)).json()
        rule = next(r for r in rules if r["category_name"] == "Transport")
        assert rule["pattern"] == "grab"
        assert rule["match_type"] == "contains"

    async def test_broader_rule_conflict_check_honours_the_chosen_match_type(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """The conflict check on this new path must test the pattern the
        person actually chose, not silently fall back to an exact match on
        the descriptor -- otherwise it would miss collisions a broader
        pattern creates and let two rules quietly disagree."""
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="GRAB *TRANSPORT SG", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        existing = await client.post(
            "/api/v1/rules",
            json={
                "pattern": "grab",
                "match_type": "contains",
                "category_id": (await client.get("/api/v1/categories", headers=auth)).json()[0]["id"],
                "scope": "future",
                "options": {"ignore_case": True, "match_negative": False},
            },
            headers=auth,
        )
        assert existing.status_code == 201, existing.text

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={
                "new_category_name": "Transport",
                "create_rule": True,
                "scope": "future",
                "pattern": "grab",
                "match_type": "contains",
                "options": {"ignore_case": True, "match_negative": False},
            },
            headers=auth,
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "rule_conflict"

    async def test_omitting_the_new_options_keeps_the_old_exact_match_behaviour(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Backward compatibility: a request with none of the new fields
        behaves exactly as this endpoint always did."""
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={"new_category_name": "Shopping", "create_rule": True, "scope": "future"},
            headers=auth,
        )
        assert response.status_code == 200, response.text

        rules = (await client.get("/api/v1/rules", headers=auth)).json()
        rule = next(r for r in rules if r["category_name"] == "Shopping")
        assert rule["pattern"] == descriptor_key
        assert rule["match_type"] == "exact"


class TestSplitDescriptor:
    """The generic processor-asterisk rule in ``normalise()`` folds anything
    before its first bare ``*`` together, so ``SMP*OLD TEA HUT`` and
    ``SMP*GOMGOM`` -- two unrelated merchants -- both collapse to ``smp``.
    That's a real, currently-unfixed over-merge (deliberately out of scope
    for the normaliser itself), and exactly the shape this feature exists
    to let a person correct by hand.
    """

    @pytest.mark.p0
    async def test_split_moves_only_the_named_descriptor_and_clears_its_classification(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SMP*OLD TEA HUT", amount_minor=500),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SMP*GOMGOM", amount_minor=500),
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        before = await _row_ids(user_a["id"], statement_id)
        shared_key = before[0]["descriptor_key"]
        assert before[0]["descriptor_key"] == before[1]["descriptor_key"] == "smp"

        response = await client.post(
            f"/api/v1/review/merchants/{shared_key}/split",
            json={"description_raw": "SMP*GOMGOM"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        new_key = response.json()["descriptor_key"]
        assert new_key != shared_key

        after = await _row_ids(user_a["id"], statement_id)
        by_raw = {row["description_raw"]: row for row in after}

        split_row = by_raw["SMP*GOMGOM"]
        assert split_row["descriptor_key"] == new_key
        assert split_row["category_id"] is None
        assert split_row["merchant_id"] is None
        assert split_row["classified_by"] is None

        untouched_row = by_raw["SMP*OLD TEA HUT"]
        assert untouched_row["descriptor_key"] == shared_key

    async def test_a_later_statement_with_the_same_raw_text_lands_on_the_new_key(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        first_statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SMP*OLD TEA HUT", amount_minor=500),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SMP*GOMGOM", amount_minor=500),
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        before = await _row_ids(user_a["id"], first_statement_id)
        shared_key = before[0]["descriptor_key"]

        split = await client.post(
            f"/api/v1/review/merchants/{shared_key}/split",
            json={"description_raw": "SMP*GOMGOM"},
            headers=auth,
        )
        new_key = split.json()["descriptor_key"]

        second_statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 8, 22), description_raw="SMP*GOMGOM", amount_minor=700)],
            printed_total_minor=700,
            llm=DecliningLLMAdapter(answers={}),
            last4="9999",
        )
        after = await _row_ids(user_a["id"], second_statement_id)
        assert after[0]["descriptor_key"] == new_key

    async def test_a_later_statement_with_a_different_reference_number_still_lands_on_the_new_key(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Real bank statements print a fresh 'Ref No.' on every single
        transaction, so a naive split key derived straight from the raw text
        would mint a brand-new key every month for the same real merchant --
        exactly the instability this test exists to rule out.

        This only tests the key `split_descriptor` computes
        (strip_reference_suffix before slugify). Whether a *later* statement's
        differently-referenced raw text is recognised as the same override at
        all is a separate, still-open question -- see the class docstring.
        """
        first_statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP*OLD TEA HUT Singapore Ref No. : 24107626097151107442146",
                    amount_minor=500,
                ),
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP*GOMGOM Singapore Ref No. : 24575436044612140700973",
                    amount_minor=500,
                ),
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        before = await _row_ids(user_a["id"], first_statement_id)
        shared_key = before[0]["descriptor_key"]

        split = await client.post(
            f"/api/v1/review/merchants/{shared_key}/split",
            json={"description_raw": "SMP*GOMGOM Singapore Ref No. : 24575436044612140700973"},
            headers=auth,
        )
        assert split.status_code == 200, split.text
        assert split.json()["descriptor_key"] == "smp-gomgom"

    async def test_splitting_a_descriptor_not_in_the_named_group_is_rejected(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A LONE SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/split",
            json={"description_raw": "SOMETHING ELSE ENTIRELY"},
            headers=auth,
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "descriptor_not_in_group"

    async def test_splitting_the_same_raw_text_twice_is_rejected(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SMP*OLD TEA HUT", amount_minor=500),
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SMP*GOMGOM", amount_minor=500),
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        shared_key = rows[0]["descriptor_key"]

        first = await client.post(
            f"/api/v1/review/merchants/{shared_key}/split",
            json={"description_raw": "SMP*GOMGOM"},
            headers=auth,
        )
        assert first.status_code == 200, first.text
        first_new_key = first.json()["descriptor_key"]

        second = await client.post(
            f"/api/v1/review/merchants/{shared_key}/split",
            json={"description_raw": "SMP*GOMGOM"},
            headers=auth,
        )
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "descriptor_already_split"
        assert second.json()["error"]["details"]["descriptor_key"] == first_new_key


class TestMergeDescriptor:
    """The inverse of a split: two raw descriptors that look nothing alike
    (no shared processor prefix for the generic asterisk rule to even try
    unifying) but are the same real merchant -- an under-merge split can't
    touch, since split only ever pulls a raw line *out* of a group.
    """

    @pytest.mark.p0
    async def test_merge_folds_the_source_into_the_target_and_adopts_its_classification(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SHOP ONE", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="SHOP TWO", amount_minor=500),
            ],
            printed_total_minor=1500,
            llm=DecliningLLMAdapter(answers={}),
        )
        before = await _row_ids(user_a["id"], statement_id)
        by_raw = {row["description_raw"]: row for row in before}
        target_key = by_raw["SHOP ONE"]["descriptor_key"]
        source_key = by_raw["SHOP TWO"]["descriptor_key"]
        assert target_key != source_key

        category_id = (await client.get("/api/v1/categories", headers=auth)).json()[0]["id"]
        classify = await client.post(
            f"/api/v1/review/merchants/{target_key}/classify",
            json={"category_id": category_id, "create_rule": False},
            headers=auth,
        )
        assert classify.status_code == 200, classify.text

        merge = await client.post(
            f"/api/v1/review/merchants/{source_key}/merge",
            json={"target_descriptor_key": target_key},
            headers=auth,
        )
        assert merge.status_code == 200, merge.text

        after = await _row_ids(user_a["id"], statement_id)
        by_raw = {row["description_raw"]: row for row in after}

        merged_row = by_raw["SHOP TWO"]
        assert merged_row["descriptor_key"] == target_key
        assert str(merged_row["category_id"]) == category_id
        assert merged_row["classified_by"] == "override"

        untouched_row = by_raw["SHOP ONE"]
        assert untouched_row["descriptor_key"] == target_key
        assert str(untouched_row["category_id"]) == category_id

        board = (
            await client.get(f"/api/v1/statements/{statement_id}/review", headers=auth)
        ).json()
        assert len(board["merchants"]) == 1
        assert board["merchants"][0]["row_count"] == 2

    async def test_a_later_statement_with_the_merged_raw_text_lands_on_the_target_key(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        first_statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SHOP ONE", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="SHOP TWO", amount_minor=500),
            ],
            printed_total_minor=1500,
            llm=DecliningLLMAdapter(answers={}),
        )
        before = await _row_ids(user_a["id"], first_statement_id)
        by_raw = {row["description_raw"]: row for row in before}
        target_key = by_raw["SHOP ONE"]["descriptor_key"]
        source_key = by_raw["SHOP TWO"]["descriptor_key"]

        merge = await client.post(
            f"/api/v1/review/merchants/{source_key}/merge",
            json={"target_descriptor_key": target_key},
            headers=auth,
        )
        assert merge.status_code == 200, merge.text

        second_statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 8, 22), description_raw="SHOP TWO", amount_minor=700)],
            printed_total_minor=700,
            llm=DecliningLLMAdapter(answers={}),
            last4="9999",
        )
        after = await _row_ids(user_a["id"], second_statement_id)
        assert after[0]["descriptor_key"] == target_key

    async def test_merge_clears_a_stale_rule_id(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        """A rule matched on the source row's own raw text before the merge.
        Once merged, category_id comes from the target instead, so keeping
        that rule_id around would misattribute a category the rule never
        actually assigned."""
        categories = (await client.get("/api/v1/categories", headers=auth)).json()
        rule_category_id = categories[0]["id"]
        target_category_id = categories[1]["id"]

        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            await RuleRepository(session).create(
                pattern="shop two",
                match_type="contains",
                category_id=uuid.UUID(rule_category_id),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )

        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="SHOP ONE", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="SHOP TWO", amount_minor=500),
            ],
            printed_total_minor=1500,
            llm=DecliningLLMAdapter(answers={}),
        )
        before = await _row_ids(user_a["id"], statement_id)
        by_raw = {row["description_raw"]: row for row in before}
        target_key = by_raw["SHOP ONE"]["descriptor_key"]
        source_key = by_raw["SHOP TWO"]["descriptor_key"]
        assert by_raw["SHOP TWO"]["rule_id"] is not None
        assert str(by_raw["SHOP TWO"]["category_id"]) == rule_category_id

        classify = await client.post(
            f"/api/v1/review/merchants/{target_key}/classify",
            json={"category_id": target_category_id, "create_rule": False},
            headers=auth,
        )
        assert classify.status_code == 200, classify.text

        merge = await client.post(
            f"/api/v1/review/merchants/{source_key}/merge",
            json={"target_descriptor_key": target_key},
            headers=auth,
        )
        assert merge.status_code == 200, merge.text

        after = await _row_ids(user_a["id"], statement_id)
        merged_row = next(row for row in after if row["description_raw"] == "SHOP TWO")
        assert str(merged_row["category_id"]) == target_category_id
        assert merged_row["rule_id"] is None

    async def test_merging_into_itself_is_rejected(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A LONE SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/merge",
            json={"target_descriptor_key": descriptor_key},
            headers=auth,
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "cannot_merge_into_self"

    async def test_merging_a_descriptor_with_no_rows_is_rejected(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A LONE SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        rows = await _row_ids(user_a["id"], statement_id)
        descriptor_key = rows[0]["descriptor_key"]

        response = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/merge",
            json={"target_descriptor_key": "no-such-merchant-anywhere"},
            headers=auth,
        )
        assert response.status_code == 404, response.text


class TestFinish:
    @pytest.mark.p0
    async def test_finish_is_blocked_while_anything_is_unclassified(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="UNCLASSIFIED", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )

        response = await client.post(f"/api/v1/statements/{statement_id}/review/finish", headers=auth)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "review_incomplete"

    async def test_finish_succeeds_once_every_row_is_classified_and_returns_a_summary(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A KNOWN SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={}),
        )

        response = await client.post(f"/api/v1/statements/{statement_id}/review/finish", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["statement_id"] == statement_id
        assert body["rows_imported"] == 1
        assert body["unclassified"] == 0

        summary = await client.get(f"/api/v1/statements/{statement_id}/summary", headers=auth)
        assert summary.status_code == 200, summary.text
        assert summary.json() == body
