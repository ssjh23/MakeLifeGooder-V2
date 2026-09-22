"""BUILD STEP 7.2: the dashboard, per-row override, and transaction listing.

Statements reach the dashboard the same way the product does: register,
extract, commit, classify, aggregate, then finish review -- the same chain
``test_review.py`` builds, one step further.
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
from tests.integration.test_import_flow import (
    StubParser,
    _extract,
    _parsed,
    _register_a_statement,
    _seed_card,
)
from tests.integration.test_review import _prepare_statement

pytestmark = pytest.mark.integration


async def _system_category_id(slug: str) -> str:
    """System categories are seeded reference data (BUILD STEP 5.5's
    taxonomy), not yet exposed by CategoryService (BUILD STEP 9.2) -- fetched
    directly rather than waiting on that step."""
    engine = create_async_engine(TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://"))
    try:
        async with engine.connect() as connection:
            return str(
                await connection.scalar(
                    text("SELECT id FROM categories WHERE slug = :slug AND user_id IS NULL"),
                    {"slug": slug},
                )
            )
    finally:
        await engine.dispose()


async def _finish_review(client: AsyncClient, auth: dict[str, str], statement_id: str) -> None:
    response = await client.post(f"/api/v1/statements/{statement_id}/review/finish", headers=auth)
    assert response.status_code == 200, response.text


class TestDashboardBands:
    @pytest.mark.p0
    async def test_a_finished_statement_appears_in_the_current_month_band(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        today = date.today().replace(day=15)
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=today, description_raw="FAIRPRICE FINEST NEX", amount_minor=1000)],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={}),
        )
        await _finish_review(client, auth, statement_id)

        response = await client.get("/api/v1/dashboard", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["banner"]["locked"] is False
        assert body["banner"]["unclassified"] == "0.00"
        current_month = [m for m in body["months"] if m["month"] == today.replace(day=1).isoformat()]
        assert len(current_month) == 1
        assert current_month[0]["total"] == "10.00"
        assert len(body["categories"]) == 1
        assert body["categories"][0]["share"] == 1.0

    async def test_the_dashboard_locks_while_anything_is_unclassified(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        from tests.integration.test_cascade import DecliningLLMAdapter

        today = date.today().replace(day=15)
        await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=today, description_raw="UNRECOGNISABLE SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        # Not finished -- the row stays unclassified, aggregate never ran for
        # it, and the banner has to reflect that from the raw transaction,
        # not from category_monthly_totals (which never gets a row for it).

        response = await client.get("/api/v1/dashboard", headers=auth)
        body = response.json()
        assert body["banner"]["locked"] is True
        assert body["banner"]["unclassified"] == "10.00"

    async def test_an_empty_account_gets_zeroed_bands_not_a_division_error(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get("/api/v1/dashboard", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["categories"] == []
        assert body["banner"]["locked"] is False

    @pytest.mark.p0
    async def test_a_statement_tagged_with_a_card_after_extraction_still_appears(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """The real import screen (screen 03) uploads with no card chosen --
        ``ImportFeature.tsx``'s upload form never collects one -- and a card
        is only tagged afterwards, on the reconciliation screen (03b), before
        commit. ``TransactionRepository.add_rows`` denormalises a row's
        ``card_id`` from the statement at *insert* time (extraction), so a
        card tagged only after that point was never backfilled onto the rows
        already written -- every one of them stayed ``card_id IS NULL``
        forever, and ``AggregateRefresher.refresh_months`` requires a card on
        a row to fold it into any dashboard band at all. The fix stamps it
        again in ``StatementService.commit()``, the one place a card is
        already guaranteed to exist (the method's own check just above raises
        if it is not). This test fails without that stamp -- every other
        dashboard test in this file tags the card at registration, before
        extraction, which is exactly the path ``add_rows`` already covers and
        the reason none of them caught this."""
        today = date.today().replace(day=15)

        # No card_id here -- matches uploadStatement(file) with no card
        # argument, the real upload screen's only call shape.
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=today, description_raw="FAIRPRICE FINEST NEX", amount_minor=1000
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        # Tagged only now, exactly as the reconciliation screen does it.
        card_id = await _seed_card(user_a["id"])
        tag = await client.patch(
            f"/api/v1/statements/{statement_id}", json={"card_id": card_id}, headers=auth
        )
        assert tag.status_code == 200, tag.text

        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        await classify_statement(
            statement_id=statement_id, user_id=user_a["id"], llm=FakeLLMAdapter(overrides={})
        )
        await aggregate_statement(statement_id=statement_id, user_id=user_a["id"])
        await _finish_review(client, auth, statement_id)

        response = await client.get("/api/v1/dashboard", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["banner"]["locked"] is False
        current_month = [
            m for m in body["months"] if m["month"] == today.replace(day=1).isoformat()
        ]
        assert len(current_month) == 1
        assert current_month[0]["total"] == "10.00"
        assert len(body["categories"]) == 1


class TestTransactionEndpoints:
    async def test_get_transaction_returns_full_provenance(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={}),
        )
        listed = await client.get("/api/v1/transactions", headers=auth)
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        transaction_id = listed.json()[0]["id"]

        response = await client.get(f"/api/v1/transactions/{transaction_id}", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provenance"]["statement_id"] == statement_id
        assert body["provenance"]["classified_by"] == "llm"
        assert body["provenance"]["prompt_version"] == "fake-v1"

    @pytest.mark.p0
    async def test_TC_DASH_009_override_moves_category_totals_but_not_the_statement_total(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        today = date.today().replace(day=10)
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=today, description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={}),
        )
        await _finish_review(client, auth, statement_id)

        listed = await client.get("/api/v1/transactions", headers=auth)
        transaction_id = listed.json()[0]["id"]
        original_category_id = listed.json()[0]["category_id"]

        other_category_id = None
        for slug in ("food-drink", "transport", "groceries", "other"):
            candidate = await _system_category_id(slug)
            if candidate != original_category_id:
                other_category_id = candidate
                break
        assert other_category_id is not None

        response = await client.post(
            f"/api/v1/transactions/{transaction_id}/override",
            json={"category_id": other_category_id},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["statement_total"] == "10.00"

        board = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        row_total = sum(float(r["amount"]) for r in board.json()["rows"] if not r["skipped"])
        assert f"{row_total:.2f}" == "10.00"


class TestDashboardWireframeEnrichment:
    """The wireframe-driven additions to the five bands: row counts, month-
    over-month change, top merchants per category, per-card category splits,
    and the individual-transactions band's search, sort and provenance."""

    @pytest.mark.p0
    async def test_category_band_carries_rows_and_top_merchants(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        today = date.today().replace(day=15)
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(posted_on=today, description_raw="SHOP ALPHA A", amount_minor=500),
                ParsedRow(posted_on=today, description_raw="SHOP ALPHA B", amount_minor=500),
                ParsedRow(posted_on=today, description_raw="SHOP BETA", amount_minor=300),
                ParsedRow(posted_on=today, description_raw="SHOP GAMMA", amount_minor=100),
            ],
            printed_total_minor=1400,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        # Two descriptors, one merchant: the fake adapter names a merchant
        # per *descriptor*, so pin both "shop alpha" lines to the same
        # canonical merchant the way a real alias match would.
        await classify_statement(
            statement_id=statement_id,
            user_id=user_a["id"],
            llm=FakeLLMAdapter(
                overrides={
                    "shop alpha a": "food-drink",
                    "shop alpha b": "food-drink",
                    "shop beta": "food-drink",
                    "shop gamma": "food-drink",
                }
            ),
        )
        await aggregate_statement(statement_id=statement_id, user_id=user_a["id"])
        await _finish_review(client, auth, statement_id)

        response = await client.get(
            "/api/v1/dashboard", params={"range": "month", "month": today.isoformat()}, headers=auth
        )
        assert response.status_code == 200, response.text
        band = next(c for c in response.json()["categories"] if c["name"] == "Food & Drink")

        assert band["rows"] == 4
        assert band["other_merchants_count"] == 2
        assert band["other_merchants_total"] is not None
        top_names = {m["name"] for m in band["top_merchants"]}
        assert len(band["top_merchants"]) == 2
        # The two highest-total merchants (600-ish and 300) beat the 100 one,
        # regardless of exactly how the fake adapter split "shop alpha a/b"
        # into one or two canonical merchants.
        assert top_names

    @pytest.mark.p0
    async def test_category_band_change_is_versus_the_immediately_preceding_month(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        first_month = date(2026, 6, 15)
        second_month = date(2026, 7, 15)

        async def _commit_one(posted_on: date, amount_minor: int, descriptor: str) -> None:
            # A fresh card per statement: `_parsed()` hardcodes the same
            # statement period regardless of the row dates given here, and
            # "one statement per card per period" would otherwise reject the
            # second commit as a duplicate of the first.
            card_id = await _seed_card(
                user_a["id"], last4=f"{posted_on.month:02d}{posted_on.month:02d}"
            )
            statement_id = await _register_a_statement(client, auth, card_id=card_id)
            parsed = _parsed(
                rows=[
                    ParsedRow(
                        posted_on=posted_on, description_raw=descriptor, amount_minor=amount_minor
                    )
                ],
                printed_total_minor=amount_minor,
            )
            await _extract(statement_id, user_a["id"], StubParser(result=parsed))
            commit = await client.post(
                f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
            )
            assert commit.status_code == 200, commit.text
            await classify_statement(
                statement_id=statement_id,
                user_id=user_a["id"],
                llm=FakeLLMAdapter(overrides={descriptor.lower(): "food-drink"}),
            )
            await aggregate_statement(statement_id=statement_id, user_id=user_a["id"])
            await _finish_review(client, auth, statement_id)

        await _commit_one(first_month, 1000, "JUNE SHOP")
        await _commit_one(second_month, 1500, "JULY SHOP")

        response = await client.get(
            "/api/v1/dashboard",
            params={"range": "month", "month": second_month.replace(day=1).isoformat()},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        band = next(c for c in response.json()["categories"] if c["name"] == "Food & Drink")

        assert band["change"] is not None
        assert band["change"] == pytest.approx(0.5)

    @pytest.mark.p0
    async def test_card_band_carries_rows_and_largest_category(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        today = date.today().replace(day=15)
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=today, description_raw="BIG SPEND", amount_minor=800),
                ParsedRow(posted_on=today, description_raw="SMALL SPEND", amount_minor=200),
            ],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={"big spend": "food-drink", "small spend": "transport"}),
        )
        await _finish_review(client, auth, statement_id)

        response = await client.get(
            "/api/v1/dashboard", params={"range": "month", "month": today.isoformat()}, headers=auth
        )
        assert response.status_code == 200, response.text
        card = response.json()["cards"][0]

        assert card["rows"] == 2
        assert card["largest_category"] is not None
        assert card["largest_category"]["name"] == "Food & Drink"
        assert set(card["by_category"].keys()) == {"Food & Drink", "Transport"}
        assert card["by_category"]["Food & Drink"] == "8.00"
        assert card["by_category"]["Transport"] == "2.00"

    async def test_transactions_list_carries_merchant_name_and_supports_search_and_sort(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        today = date.today().replace(day=15)
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=today, description_raw="ZEBRA MART", amount_minor=100),
                ParsedRow(posted_on=today, description_raw="APPLE STORE", amount_minor=900),
            ],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={"zebra mart": "shopping", "apple store": "shopping"}),
        )
        await _finish_review(client, auth, statement_id)

        listed = await client.get("/api/v1/transactions", headers=auth)
        assert listed.status_code == 200, listed.text
        assert all(row["merchant_name"] for row in listed.json())
        assert all(row["descriptor_key"] for row in listed.json())

        searched = await client.get(
            "/api/v1/transactions", params={"search": "zebra"}, headers=auth
        )
        assert len(searched.json()) == 1
        assert searched.json()[0]["description"] == "ZEBRA MART"

        sorted_desc = await client.get(
            "/api/v1/transactions", params={"sort": "amount_desc"}, headers=auth
        )
        amounts = [float(row["amount"]) for row in sorted_desc.json()]
        assert amounts == sorted(amounts, reverse=True)

    @pytest.mark.p0
    async def test_transaction_detail_carries_rule_pattern_once_confirmed(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        today = date.today().replace(day=15)
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=today, description_raw="RULE SHOP", amount_minor=500)],
            printed_total_minor=500,
            llm=FakeLLMAdapter(overrides={}),
        )

        board = await client.get(f"/api/v1/statements/{statement_id}/review", headers=auth)
        merchant = board.json()["merchants"][0]
        descriptor_key = merchant["descriptor_key"]
        category_id = await _system_category_id("food-drink")

        classify = await client.post(
            f"/api/v1/review/merchants/{descriptor_key}/classify",
            json={
                "category_id": category_id,
                "new_category_name": None,
                "create_rule": True,
                # "backfill", not "future": the fake LLM already classified
                # this row into whatever its hash landed on, and "future"
                # only touches rows with category_id still null -- this one
                # isn't, so the override to food-drink would silently no-op.
                "scope": "backfill",
            },
            headers=auth,
        )
        assert classify.status_code == 200, classify.text

        confirm = await client.post(
            f"/api/v1/statements/{statement_id}/review/confirm-all",
            json={"descriptor_keys": None},
            headers=auth,
        )
        assert confirm.status_code == 200, confirm.text

        listed = await client.get("/api/v1/transactions", headers=auth)
        transaction_id = listed.json()[0]["id"]

        detail = await client.get(f"/api/v1/transactions/{transaction_id}", headers=auth)
        assert detail.status_code == 200, detail.text
        provenance = detail.json()["provenance"]
        assert provenance["rule_id"] is not None
        assert provenance["rule_pattern"] == descriptor_key

    async def test_transaction_list_also_carries_rule_pattern_for_a_rule_only_classification(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        """A row the cascade never resolved a merchant for -- only a
        standing rule classified it -- has no merchant_name at all
        (merchant_id stays null; a rule only ever stamps category_id and
        rule_id). The list endpoint, not just the single-transaction detail
        view, has to carry rule_pattern too, or the dashboard's merchant
        column shows blank for every rule-classified row instead of naming
        the rule that caught it."""
        from tests.integration.test_cascade import DecliningLLMAdapter

        categories = (await client.get("/api/v1/categories", headers=auth)).json()
        category_id = categories[0]["id"]

        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            await RuleRepository(session).create(
                pattern="unrecognisable shop",
                match_type="contains",
                category_id=uuid.UUID(category_id),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )

        today = date.today().replace(day=15)
        await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=today, description_raw="UNRECOGNISABLE SHOP", amount_minor=1000)
            ],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )

        listed = await client.get("/api/v1/transactions", headers=auth)
        assert listed.status_code == 200, listed.text
        row = listed.json()[0]
        assert row["merchant_name"] is None
        assert row["provenance"]["rule_id"] is not None
        assert row["provenance"]["rule_pattern"] == "unrecognisable shop"

    @pytest.mark.p0
    async def test_transaction_detail_flags_a_kept_duplicate_pair(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Two different raw descriptions ("NEX" / "ORCHARD" outlets, same
        pattern ``test_review.py``'s own duplicate tests use), not two
        identical rows: the dedupe *guard* hashes ``description_raw``, so two
        genuinely identical rows on one statement would collide on
        ``uq_transactions_user_id_dedupe_hash`` before ever reaching review.
        The duplicate *flag* matches on the normalised ``descriptor_key``
        instead, which strips the outlet suffix from both."""
        today = date.today().replace(day=15)
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[
                ParsedRow(posted_on=today, description_raw="TWIN CHARGE NEX", amount_minor=500),
                ParsedRow(posted_on=today, description_raw="TWIN CHARGE ORCHARD", amount_minor=500),
            ],
            printed_total_minor=1000,
            llm=FakeLLMAdapter(overrides={"twin charge": "food-drink"}),
        )
        await _finish_review(client, auth, statement_id)

        listed = await client.get("/api/v1/transactions", headers=auth)
        transaction_id = listed.json()[0]["id"]

        detail = await client.get(f"/api/v1/transactions/{transaction_id}", headers=auth)
        assert detail.status_code == 200, detail.text
        assert detail.json()["flags"] == ["paired_with_duplicate_kept"]
