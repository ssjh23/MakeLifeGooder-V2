"""BUILD STEP 9.1: cards. Screens 02b to 02d."""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient

from app.extract.base import ParsedRow
from tests.integration.test_cascade import DecliningLLMAdapter
from tests.integration.test_import_flow import StubParser, _extract, _parsed, _register_a_statement
from tests.integration.test_review import _prepare_statement

pytestmark = pytest.mark.integration


def _card_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "nickname": "My Card",
        "institution": "DBS",
        "type": "credit",
        "last4": "1234",
    }
    payload.update(overrides)
    return payload


class TestCreate:
    @pytest.mark.p0
    async def test_creates_a_card_with_no_statements(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["nickname"] == "My Card"
        assert body["institution"] == "DBS"
        assert body["last4"] == "1234"
        assert body["statement_count"] == 0
        assert body["archived"] is False
        assert body["status"] == "active"

    @pytest.mark.p0
    @pytest.mark.security
    async def test_TC_CARD_002_a_full_card_number_is_rejected_not_silently_dropped(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """There is no field anywhere for a full number. Sending one must be
        a 400, not a 201 that quietly discarded it."""
        response = await client.post(
            "/api/v1/cards",
            json=_card_payload(full_number="4111111111111111"),
            headers=auth,
        )
        assert response.status_code == 400, response.text

    @pytest.mark.p0
    async def test_TC_CARD_002_a_cvv_field_is_rejected(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.post("/api/v1/cards", json=_card_payload(cvv="123"), headers=auth)
        assert response.status_code == 400, response.text

    @pytest.mark.p0
    async def test_TC_CARD_003_a_second_active_card_with_the_same_last4_is_a_409(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        first = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/cards", json=_card_payload(nickname="Second Card"), headers=auth
        )
        assert second.status_code == 409, second.text


class TestUpdate:
    @pytest.mark.p0
    async def test_TC_CARD_005_update_only_renames_and_recolours(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        created = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        card_id = created.json()["id"]

        response = await client.patch(
            f"/api/v1/cards/{card_id}",
            json={"nickname": "Renamed", "colour": "#112233"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["nickname"] == "Renamed"
        assert body["colour"] == "#112233"
        assert body["last4"] == "1234"
        assert body["statement_count"] == 0

    @pytest.mark.p0
    async def test_last4_and_institution_can_be_corrected(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        created = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        card_id = created.json()["id"]

        response = await client.patch(
            f"/api/v1/cards/{card_id}",
            json={"last4": "9999", "institution": "UOB"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["last4"] == "9999"
        assert body["institution"] == "UOB"
        # Untouched fields survive the partial update.
        assert body["nickname"] == "My Card"

        refetched = await client.get("/api/v1/cards", headers=auth)
        assert refetched.json()[0]["institution"] == "UOB"
        assert refetched.json()[0]["last4"] == "9999"

    async def test_correcting_institution_groups_onto_an_existing_account(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """Moving a card to an institution another of the tenant's cards
        already uses groups it onto that same account, the same rule
        creating a card under that institution follows."""
        dbs_card = await client.post(
            "/api/v1/cards", json=_card_payload(last4="1111"), headers=auth
        )
        uob_card = await client.post(
            "/api/v1/cards", json=_card_payload(institution="UOB", last4="2222"), headers=auth
        )

        response = await client.patch(
            f"/api/v1/cards/{uob_card.json()['id']}",
            json={"institution": "DBS"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        assert response.json()["institution"] == "DBS"

        cards = (await client.get("/api/v1/cards", headers=auth)).json()
        dbs_cards = [c for c in cards if c["institution"] == "DBS"]
        assert {c["id"] for c in dbs_cards} == {dbs_card.json()["id"], uob_card.json()["id"]}

    @pytest.mark.p0
    async def test_correcting_last4_to_one_already_in_use_is_a_409(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        await client.post("/api/v1/cards", json=_card_payload(last4="1111"), headers=auth)
        second = await client.post(
            "/api/v1/cards", json=_card_payload(last4="2222"), headers=auth
        )

        response = await client.patch(
            f"/api/v1/cards/{second.json()['id']}",
            json={"last4": "1111"},
            headers=auth,
        )
        assert response.status_code == 409, response.text


class TestArchive:
    @pytest.mark.p0
    async def test_TC_CARD_007_archiving_refuses_a_statement_with_no_disposition(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        created = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        card_id = created.json()["id"]

        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth)
        assert commit.status_code == 200, commit.text

        preview = await client.get(f"/api/v1/cards/{card_id}/archive-preview", headers=auth)
        assert preview.status_code == 200, preview.text
        assert preview.json()["statements"] == [statement_id]

        response = await client.post(
            f"/api/v1/cards/{card_id}/archive", json={"statement_dispositions": []}, headers=auth
        )
        assert response.status_code == 400, response.text

        with_disposition = await client.post(
            f"/api/v1/cards/{card_id}/archive",
            json={
                "statement_dispositions": [{"statement_id": statement_id, "action": "keep"}]
            },
            headers=auth,
        )
        assert with_disposition.status_code == 200, with_disposition.text
        assert with_disposition.json()["archived"] is True

    async def test_archive_then_restore_round_trips(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        created = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        card_id = created.json()["id"]

        archive = await client.post(
            f"/api/v1/cards/{card_id}/archive", json={"statement_dispositions": []}, headers=auth
        )
        assert archive.status_code == 200, archive.text
        assert archive.json()["status"] == "archived"

        restore = await client.post(f"/api/v1/cards/{card_id}/restore", headers=auth)
        assert restore.status_code == 200, restore.text
        assert restore.json()["status"] == "active"
        assert restore.json()["archived"] is False


class TestDeleteStatements:
    @pytest.mark.p0
    async def test_TC_CARD_010_refuses_without_confirm(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        created = await client.post("/api/v1/cards", json=_card_payload(), headers=auth)
        card_id = created.json()["id"]

        response = await client.request(
            "DELETE",
            f"/api/v1/cards/{card_id}/statements",
            json={"confirm": False},
            headers=auth,
        )
        assert response.status_code == 400, response.text

    async def test_confirmed_delete_removes_the_statements(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = None
        statement_id = await _prepare_statement(
            client,
            auth,
            user_a["id"],
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A SHOP", amount_minor=1000)],
            printed_total_minor=1000,
            llm=DecliningLLMAdapter(answers={}),
        )
        statement = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        card_id = statement.json()["card_id"]

        response = await client.request(
            "DELETE",
            f"/api/v1/cards/{card_id}/statements",
            json={"confirm": True},
            headers=auth,
        )
        assert response.status_code == 204, response.text

        after = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert after.status_code == 404
