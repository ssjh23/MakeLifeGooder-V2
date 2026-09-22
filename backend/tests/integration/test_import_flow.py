"""BUILD STEPS 4.1 to 4.4: the whole import flow up to a committed statement.

  4.1  upload url + register     TestUploadUrl, TestRegister
  4.2  the extract worker task   TestExtract
  4.3  row mutations, screen 03b TestRows
  4.4  the commit gate           TestCommit

Extraction is driven with a stub parser throughout (``StubParser`` below),
never a real PDF: step 2.2 (real bank statements) has nothing to do with
whether the extract *handler* -- the reconcile-dedupe-write plumbing around
whatever a parser returns -- is correct. Calling ``extract_statement``
directly with an injected ``registry`` is exactly what its own docstring
describes as the point of taking one as an argument.
"""

from __future__ import annotations

import uuid
from datetime import date

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.extract.base import ExtractionFailure, ParsedRow, ParsedStatement, StatementParser
from app.extract.registry import ParserRegistry
from app.storage import build_object_store
from app.worker.tasks import extract_statement
from tests.conftest import TEST_MIGRATOR_URL

pytestmark = pytest.mark.integration

PDF_BYTES = b"%PDF-1.7\nnot a real statement\n"


class StubParser:
    """A :class:`~app.extract.base.StatementParser` a test controls entirely.

    Returns a fixed :class:`ParsedStatement`, or raises a fixed
    :class:`ExtractionFailure` -- whichever the test is asserting the extract
    handler reacts to correctly. Never touches a PDF library.
    """

    name = "stub"

    def __init__(
        self,
        *,
        result: ParsedStatement | None = None,
        failure: ExtractionFailure | None = None,
    ) -> None:
        self._result = result
        self._failure = failure

    def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
        return True

    def parse(self, pdf_bytes: bytes, *, password: str | None = None) -> ParsedStatement:
        if self._failure is not None:
            raise self._failure
        assert self._result is not None
        return self._result


def _registry(parser: StatementParser) -> ParserRegistry:
    return ParserRegistry(parsers=[parser])


def _parsed(
    *,
    rows: list[ParsedRow],
    printed_total_minor: int | None,
    detected_last4: str | None = "4429",
) -> ParsedStatement:
    return ParsedStatement(
        rows=rows,
        printed_total_minor=printed_total_minor,
        currency="SGD",
        period_start=date(2026, 7, 15),
        period_end=date(2026, 8, 14),
        detected_last4=detected_last4,
        has_text_layer=True,
        parser="stub",
    )


async def _unscoped_engine() -> object:
    """A connection as the owning role, bypassing row level security.

    Only for the test's own assertions -- confirming a row does or does not
    exist regardless of tenant -- never for anything the application does.
    """
    return create_async_engine(
        TEST_MIGRATOR_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://")
    )


async def _seed_card(user_id: str, *, last4: str = "4429") -> str:
    """A card and its account, inserted directly.

    Card creation is BUILD STEP 9.1, not written. Every build step past it
    seeds one directly instead of waiting, same as this one does.
    """
    engine = await _unscoped_engine()
    try:
        async with engine.begin() as connection:
            # ``accounts``/``cards`` carry FORCE ROW LEVEL SECURITY, so even
            # the owning role needs app.user_id set to satisfy the insert
            # policy's WITH CHECK.
            await connection.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": user_id},
            )
            account_id = await connection.scalar(
                text(
                    "INSERT INTO accounts (user_id, institution, account_kind) "
                    "VALUES (:user_id, 'DBS', 'credit') RETURNING id"
                ),
                {"user_id": user_id},
            )
            card_id = await connection.scalar(
                text(
                    "INSERT INTO cards (user_id, account_id, nickname, last4) "
                    "VALUES (:user_id, :account_id, 'Test Card', :last4) RETURNING id"
                ),
                {"user_id": user_id, "account_id": account_id, "last4": last4},
            )
    finally:
        await engine.dispose()
    return str(card_id)


async def _upload_a_statement(
    client: AsyncClient, auth: dict[str, str], *, content: bytes | None = None
) -> str:
    """Presign, PUT the bytes, and return the ``upload_id``. Does not register.

    ``content`` defaults to a fresh value per call (``PDF_BYTES`` plus a
    random suffix) rather than the bare shared constant: real parsing is
    always stubbed out in this file, so nothing here has ever cared that
    every test "upload" was byte-identical -- until the register-time
    duplicate-file check (BUILD STEP 4.1) made that a real 409. Pass an
    explicit ``content`` only when a test deliberately wants two uploads to
    collide.
    """
    body_bytes = content if content is not None else PDF_BYTES + uuid.uuid4().bytes
    presign = await client.post(
        "/api/v1/statements/upload-url",
        json={"content_type": "application/pdf", "size_bytes": len(body_bytes)},
        headers=auth,
    )
    assert presign.status_code == 200, presign.text
    body = presign.json()

    async with httpx.AsyncClient(timeout=30) as http:
        put = await http.put(body["url"], content=body_bytes, headers=body["required_headers"])
    assert put.status_code in (200, 204), put.text

    return body["upload_id"]  # type: ignore[no-any-return]


async def _register_a_statement(
    client: AsyncClient,
    auth: dict[str, str],
    *,
    card_id: str | None = None,
    content: bytes | None = None,
) -> str:
    """Upload and register, returning the new ``statement_id``."""
    upload_id = await _upload_a_statement(client, auth, content=content)
    response = await client.post(
        "/api/v1/statements",
        json={"upload_id": upload_id, "card_id": card_id},
        headers=auth,
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


async def _extract(
    statement_id: str, user_id: str, parser: StatementParser, *, password: str | None = None
) -> None:
    """Run the extract handler directly against ``statement_id``, no queue."""
    store = build_object_store(get_settings())
    await extract_statement(
        statement_id=statement_id,
        user_id=user_id,
        store=store,
        registry=_registry(parser),
        password=password,
    )


class TestUploadUrl:
    async def test_TC_IMP_001_response_carries_a_url_scoped_to_one_object(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """The full router -> service -> store path, not just the store call.

        ``store.presign_upload`` is already covered directly in
        ``test_storage_and_queue.py``; this is the part that only exists once
        the service maps a :class:`~app.storage.base.PresignedUpload` onto
        :class:`~app.schemas.statements.UploadUrlResponse`.
        """
        response = await client.post(
            "/api/v1/statements/upload-url",
            json={"content_type": "application/pdf", "size_bytes": 250_000},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["upload_id"]
        assert body["url"]
        assert body["expires_at"]
        assert "Content-Type" in body["required_headers"]

    async def test_non_pdf_content_type_is_a_400(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """``UploadRejected`` becomes a 400, not a 500."""
        response = await client.post(
            "/api/v1/statements/upload-url",
            json={"content_type": "image/png", "size_bytes": 1024},
            headers=auth,
        )
        assert response.status_code == 400, response.text

    async def test_TC_IMP_013_oversized_upload_is_rejected_at_url_issue(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/statements/upload-url",
            json={"content_type": "application/pdf", "size_bytes": 100 * 1024 * 1024},
            headers=auth,
        )
        assert response.status_code == 400, response.text


class TestRegister:
    @pytest.mark.p1
    async def test_TC_IMP_004_register_an_uploaded_statement(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        upload_id = await _upload_a_statement(client, auth)

        response = await client.post(
            "/api/v1/statements", json={"upload_id": upload_id}, headers=auth
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "pending"
        # Stamped so a stuck statement can be traced without database access
        # (screen 03a); a silently degraded trace would leave this null.
        assert body["trace_id"]
        assert body["request_id"] == response.headers["X-Request-ID"]
        statement_id = body["id"]

        engine = await _unscoped_engine()
        try:
            async with engine.connect() as connection:
                job_count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM procrastinate_jobs "
                        "WHERE task_name = 'statement.extract' "
                        "AND args ->> 'statement_id' = :statement_id"
                    ),
                    {"statement_id": statement_id},
                )
        finally:
            await engine.dispose()
        assert job_count == 1

    async def test_registering_an_object_that_was_never_uploaded_is_rejected(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """A client can call this without having PUT anything to the URL."""
        response = await client.post(
            "/api/v1/statements",
            json={"upload_id": uuid.uuid4().hex},
            headers=auth,
        )
        assert response.status_code == 400, response.text

    @pytest.mark.p0
    async def test_TC_IMP_005_enqueue_is_atomic_with_the_statement_row(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force a crash between the insert and the enqueue.

        Neither the statement row nor the job may survive: a pending statement
        with no job behind it is exactly the state ADR-004 makes
        unrepresentable, and this is the test that would catch a regression
        back into a two-transaction design.
        """
        upload_id = await _upload_a_statement(client, auth)

        class _SimulatedCrash(Exception):
            pass

        async def _boom(*args: object, **kwargs: object) -> int:
            raise _SimulatedCrash("crash injected between insert and enqueue")

        monkeypatch.setattr("app.services.statement.enqueue", _boom)

        with pytest.raises(_SimulatedCrash):
            await client.post("/api/v1/statements", json={"upload_id": upload_id}, headers=auth)

        engine = await _unscoped_engine()
        try:
            async with engine.connect() as connection:
                object_key = f"uploads/{upload_id}.pdf"
                statement_count = await connection.scalar(
                    text("SELECT count(*) FROM statements WHERE object_key = :key"),
                    {"key": object_key},
                )
                job_count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM procrastinate_jobs "
                        "WHERE task_name = 'statement.extract'"
                    )
                )
        finally:
            await engine.dispose()

        assert statement_count == 0
        assert job_count == 0

    @pytest.mark.p0
    async def test_reregistering_the_same_file_is_a_409(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        content = b"%PDF-1.7\nidentical bytes\n"
        first_id = await _register_a_statement(client, auth, content=content)

        upload_id = await _upload_a_statement(client, auth, content=content)
        response = await client.post(
            "/api/v1/statements", json={"upload_id": upload_id}, headers=auth
        )
        assert response.status_code == 409, response.text
        error = response.json()["error"]
        assert error["code"] == "duplicate_file_upload"
        assert error["details"]["existing_statement_id"] == first_id
        assert error["details"]["resolutions"] == ["replace", "cancel"]

    async def test_a_genuinely_different_file_does_not_conflict(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        await _register_a_statement(client, auth, content=b"%PDF-1.7\nfirst file\n")
        second_id = await _register_a_statement(client, auth, content=b"%PDF-1.7\nsecond file\n")
        assert second_id is not None

    async def test_replacing_a_duplicate_deletes_the_old_one_and_registers_the_new(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        content = b"%PDF-1.7\nreplace me\n"
        old_id = await _register_a_statement(client, auth, content=content)

        upload_id = await _upload_a_statement(client, auth, content=content)
        conflict = await client.post(
            "/api/v1/statements", json={"upload_id": upload_id}, headers=auth
        )
        assert conflict.status_code == 409, conflict.text

        delete = await client.request(
            "DELETE",
            f"/api/v1/statements/{old_id}",
            json={"confirm": True},
            headers=auth,
        )
        assert delete.status_code == 204, delete.text

        retry = await client.post(
            "/api/v1/statements", json={"upload_id": upload_id}, headers=auth
        )
        assert retry.status_code == 202, retry.text
        new_id = retry.json()["id"]
        assert new_id != old_id

        old_lookup = await client.get(f"/api/v1/statements/{old_id}", headers=auth)
        assert old_lookup.status_code == 404

    async def test_the_conflict_is_scoped_per_tenant(
        self, client: AsyncClient, auth: dict[str, str], user_b: dict[str, str]
    ) -> None:
        content = b"%PDF-1.7\nshared by two tenants\n"
        auth_b = {"Cookie": f"ledger_session={user_b['cookie']}"}

        await _register_a_statement(client, auth, content=content)

        upload_id = await _upload_a_statement(client, auth_b, content=content)
        response = await client.post(
            "/api/v1/statements", json={"upload_id": upload_id}, headers=auth_b
        )
        assert response.status_code == 202, response.text


class TestExtract:
    async def test_TC_IMP_006_extraction_reports_the_four_facts(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="B", amount_minor=500),
            ],
            printed_total_minor=1500,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        response = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "needs_review"
        extraction = body["extraction"]
        assert extraction["has_text_layer"] is True
        assert extraction["card_identified"] == "•••• 4429"
        assert extraction["period"] == {"from": "2026-07-15", "to": "2026-08-14"}
        assert extraction["printed_total"] == "15.00"
        assert extraction["rows_extracted"] == 2
        assert extraction["parser"] == "stub"
        assert body["reconciliation"]["reconciled"] is True
        assert body["statement_month"] == "2026-08-14"

    async def test_statement_month_is_set_from_the_closing_date_alone(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Most bank formats only ever print a closing date, not an explicit
        period start (monopoly-core's own ``period_start`` is routinely
        ``None`` for this reason) -- so ``period``, which needs both bounds,
        stays null far more often than a statement actually has a month it
        belongs to. ``statement_month`` must not have that gap."""
        statement_id = await _register_a_statement(client, auth)
        parsed = ParsedStatement(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1000,
            currency="SGD",
            period_start=None,
            period_end=date(2026, 8, 14),
            detected_last4="4429",
            has_text_layer=True,
            parser="stub",
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        response = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["period"] is None
        assert body["statement_month"] == "2026-08-14"

    async def test_extraction_writes_rows_even_when_unreconciled(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Screen 03b is exactly where a shortfall gets fixed by hand, which
        only works if the rows it edits already exist."""
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1500,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        rows = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        assert rows.status_code == 200, rows.text
        body = rows.json()
        assert len(body["rows"]) == 1
        assert body["reconciliation"]["reconciled"] is False
        assert body["reconciliation"]["difference"] == "5.00"

    @pytest.mark.parametrize(
        "reason", ["password_protected", "no_text_layer", "not_a_statement", "parser_error"]
    )
    async def test_TC_FAIL_001_each_reason_sets_failed_and_writes_no_rows(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        reason: str,
    ) -> None:
        statement_id = await _register_a_statement(client, auth)
        failure = ExtractionFailure(reason, "synthetic failure")
        await _extract(statement_id, user_a["id"], StubParser(failure=failure))

        response = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure"]["reason"] == reason

        rows = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        # TC-FAIL-007: a failed file is never partially imported.
        assert rows.json()["rows"] == []

    async def test_retry_after_a_partial_write_does_not_duplicate_rows(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """TC-FAIL-010. The same descriptor, amount and day hash the same way
        on the second attempt, so ``existing_dedupe_hashes`` catches them."""
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1000,
        )
        parser = StubParser(result=parsed)

        await _extract(statement_id, user_a["id"], parser)
        await _extract(statement_id, user_a["id"], parser)

        rows = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        assert len(rows.json()["rows"]) == 1

    async def test_TC_FAIL_002_003_unlock_retries_and_the_password_decides_the_outcome(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        class PasswordGated:
            name = "gated"

            def supports(self, *, institution: str | None, pdf_bytes: bytes) -> bool:
                return True

            def parse(self, pdf_bytes: bytes, *, password: str | None = None) -> ParsedStatement:
                if password != "hunter2":
                    raise ExtractionFailure("password_protected", "wrong password")
                return _parsed(
                    rows=[
                        ParsedRow(
                            posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000
                        )
                    ],
                    printed_total_minor=1000,
                )

        parser = PasswordGated()
        statement_id = await _register_a_statement(client, auth)
        await _extract(statement_id, user_a["id"], parser)
        failed = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert failed.json()["status"] == "failed"

        # TC-FAIL-003: wrong password fails cleanly, no lockout of the statement.
        wrong = await client.post(
            f"/api/v1/statements/{statement_id}/unlock",
            json={"password": "not-it"},
            headers=auth,
        )
        assert wrong.status_code == 202, wrong.text
        await _extract(statement_id, user_a["id"], parser, password="not-it")
        still_failed = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert still_failed.json()["status"] == "failed"
        assert still_failed.json()["failure"]["reason"] == "password_protected"

        # TC-FAIL-002: the correct password succeeds.
        ok = await client.post(
            f"/api/v1/statements/{statement_id}/unlock",
            json={"password": "hunter2"},
            headers=auth,
        )
        assert ok.status_code == 202, ok.text
        await _extract(statement_id, user_a["id"], parser, password="hunter2")
        recovered = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert recovered.json()["status"] == "needs_review"

        # TC-FAIL-004: neither password ever lingers in a job payload.
        engine = await _unscoped_engine()
        try:
            async with engine.connect() as connection:
                leaked = await connection.scalar(
                    text(
                        "SELECT count(*) FROM procrastinate_jobs "
                        "WHERE args::text LIKE '%hunter2%' OR args::text LIKE '%not-it%'"
                    )
                )
        finally:
            await engine.dispose()
        assert leaked == 0


class TestRows:
    async def _unreconciled_statement(self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]) -> str:
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1240,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        return statement_id

    @pytest.mark.p0
    async def test_TC_REC_004_difference_is_computed_correctly(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await self._unreconciled_statement(client, auth, user_a)
        response = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        reconciliation = response.json()["reconciliation"]
        assert reconciliation["difference"] == "2.40"
        assert reconciliation["reconciled"] is False

    async def test_TC_REC_005_adding_a_row_updates_reconciliation_in_the_same_response(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await self._unreconciled_statement(client, auth, user_a)
        response = await client.post(
            f"/api/v1/statements/{statement_id}/rows",
            json={"posted_on": "2026-07-23", "description": "MISSING ROW", "amount": "2.40"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reconciliation"]["difference"] == "0.00"
        assert body["reconciliation"]["reconciled"] is True
        assert body["row"]["description"] == "MISSING ROW"

    async def test_TC_REC_006_editing_a_row_recomputes_and_reblocks_commit(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await self._unreconciled_statement(client, auth, user_a)
        add = await client.post(
            f"/api/v1/statements/{statement_id}/rows",
            json={"posted_on": "2026-07-23", "description": "MISSING ROW", "amount": "2.40"},
            headers=auth,
        )
        row_id = add.json()["row"]["id"]
        assert add.json()["reconciliation"]["reconciled"] is True

        edit = await client.patch(
            f"/api/v1/statements/{statement_id}/rows/{row_id}",
            json={"amount": "1.40"},
            headers=auth,
        )
        assert edit.status_code == 200, edit.text
        assert edit.json()["reconciliation"]["reconciled"] is False
        assert edit.json()["reconciliation"]["difference"] == "1.00"

    async def test_TC_REC_007_skipping_a_row_recomputes(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="B", amount_minor=240),
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        rows = (await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)).json()
        assert rows["reconciliation"]["reconciled"] is False
        row_b = next(r for r in rows["rows"] if r["amount"] == "2.40")

        response = await client.post(
            f"/api/v1/statements/{statement_id}/rows/{row_b['id']}/skip", headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["reconciliation"]["reconciled"] is True
        assert response.json()["row"]["skipped"] is True

    async def test_skipping_a_row_is_undoable(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[
                ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000),
                ParsedRow(posted_on=date(2026, 7, 23), description_raw="B", amount_minor=240),
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        rows = (await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)).json()
        row_b = next(r for r in rows["rows"] if r["amount"] == "2.40")

        await client.post(
            f"/api/v1/statements/{statement_id}/rows/{row_b['id']}/skip", headers=auth
        )

        response = await client.post(
            f"/api/v1/statements/{statement_id}/rows/{row_b['id']}/undo", headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["row"]["skipped"] is False
        assert response.json()["reconciliation"]["reconciled"] is False

    async def test_deleting_a_row_excludes_it_and_recomputes(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await self._unreconciled_statement(client, auth, user_a)
        rows = (await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)).json()
        row_id = rows["rows"][0]["id"]

        response = await client.delete(
            f"/api/v1/statements/{statement_id}/rows/{row_id}", headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["row"]["deleted"] is True
        assert response.json()["reconciliation"]["extracted_total"] == "0.00"

        after = await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)
        after_row = next(r for r in after.json()["rows"] if r["id"] == row_id)
        assert after_row["deleted"] is True

    async def test_deleting_a_row_is_undoable(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await self._unreconciled_statement(client, auth, user_a)
        rows = (await client.get(f"/api/v1/statements/{statement_id}/rows", headers=auth)).json()
        row_id = rows["rows"][0]["id"]

        await client.delete(f"/api/v1/statements/{statement_id}/rows/{row_id}", headers=auth)

        response = await client.post(
            f"/api/v1/statements/{statement_id}/rows/{row_id}/undo", headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["row"]["deleted"] is False
        assert response.json()["reconciliation"]["extracted_total"] == "10.00"

    async def test_row_mutations_are_refused_once_committed(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        response = await client.post(
            f"/api/v1/statements/{statement_id}/rows",
            json={"posted_on": "2026-07-23", "description": "TOO LATE", "amount": "1.00"},
            headers=auth,
        )
        assert response.status_code == 400, response.text


class TestCommit:
    async def _reconciled_statement(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str], *, card_id: str
    ) -> str:
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        return statement_id

    @pytest.mark.p0
    async def test_TC_REC_001_reconciled_statement_commits(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        statement_id = await self._reconciled_statement(client, auth, user_a, card_id=card_id)

        response = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready"

    @pytest.mark.p0
    async def test_TC_AUTH_012_has_statements_flips_true_after_commit(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """The other half of TC-AUTH-011: false for a new account, true once
        something is actually committed.

        `UserRepository.has_committed_statements` queries `statements`, which
        carries `FORCE ROW LEVEL SECURITY`, from `AuthService`'s *unscoped*
        session (`/me` and `/auth/login` have to look a user up before a
        tenant is established). Unscoped means no `app.user_id` is set, and
        the tenant policy's `user_id = current_setting('app.user_id')`
        compares against null in that case -- never true -- so this check
        would silently and permanently return zero rows for every account
        if it isn't scoped for this one query specifically. Only a real
        commit exercises that: a stub session or a mocked repository would
        never see RLS reject anything and would pass either way.
        """
        before = await client.get("/api/v1/me", headers=auth)
        assert before.json()["has_statements"] is False

        card_id = await _seed_card(user_a["id"])
        statement_id = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        after = await client.get("/api/v1/me", headers=auth)
        assert after.json()["has_statements"] is True

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["has_statements"] is True

    @pytest.mark.p0
    async def test_TC_REC_002_003_unreconciled_statement_is_blocked_even_called_directly(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """The gate is server-side: this calls the endpoint directly, exactly
        as TC-REC-003 requires, with no UI involved at all."""
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1500,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        response = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "statement_not_reconciled"

    async def test_commit_requires_a_card(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        response = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert response.status_code == 400, response.text

    @pytest.mark.p0
    async def test_TC_REC_010_011_accepted_gap_requires_and_records_a_reason(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1500,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        # TC-REC-011: no reason, no gap accepted.
        no_reason = await client.post(
            f"/api/v1/statements/{statement_id}/commit",
            json={"accept_gap": True},
            headers=auth,
        )
        assert no_reason.status_code == 400, no_reason.text

        # TC-REC-010: with one, it commits and is flagged, permanently.
        response = await client.post(
            f"/api/v1/statements/{statement_id}/commit",
            json={"accept_gap": True, "gap_reason": "bank fee not on the export"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready"
        assert response.json()["reconciliation"]["reconciled"] is False

    async def test_TC_REC_012_discard_leaves_the_ledger_untouched(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await self._unreconciled_helper(client, auth, user_a)

        response = await client.post(
            f"/api/v1/statements/{statement_id}/discard", headers=auth
        )
        assert response.status_code == 204, response.text

        after = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert after.status_code == 404

    async def _unreconciled_helper(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> str:
        statement_id = await _register_a_statement(client, auth)
        parsed = _parsed(
            rows=[ParsedRow(posted_on=date(2026, 7, 22), description_raw="A", amount_minor=1000)],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        return statement_id

    async def test_TC_DUP_003_same_period_different_card_is_allowed(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_one = await _seed_card(user_a["id"], last4="1111")
        card_two = await _seed_card(user_a["id"], last4="2222")

        first = await self._reconciled_statement(client, auth, user_a, card_id=card_one)
        second = await self._reconciled_statement(client, auth, user_a, card_id=card_two)

        first_commit = await client.post(
            f"/api/v1/statements/{first}/commit", json={}, headers=auth
        )
        second_commit = await client.post(
            f"/api/v1/statements/{second}/commit", json={}, headers=auth
        )
        assert first_commit.status_code == 200, first_commit.text
        assert second_commit.status_code == 200, second_commit.text

    @pytest.mark.p0
    async def test_TC_DUP_001_duplicate_card_and_period_is_409(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        first = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        await client.post(f"/api/v1/statements/{first}/commit", json={}, headers=auth)

        second = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        response = await client.post(
            f"/api/v1/statements/{second}/commit", json={}, headers=auth
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "statement_already_imported"
        assert response.json()["error"]["details"]["existing_statement_id"] == first

    async def test_TC_DUP_005_keep_both_requires_a_target_card(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        first = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        await client.post(f"/api/v1/statements/{first}/commit", json={}, headers=auth)
        second = await self._reconciled_statement(client, auth, user_a, card_id=card_id)

        response = await client.post(
            f"/api/v1/statements/{second}/resolve-duplicate",
            json={"action": "keep_both"},
            headers=auth,
        )
        assert response.status_code == 400, response.text

    async def test_TC_DUP_006_cancel_discards_the_new_file_and_leaves_the_original(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        first = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        await client.post(f"/api/v1/statements/{first}/commit", json={}, headers=auth)
        second = await self._reconciled_statement(client, auth, user_a, card_id=card_id)

        response = await client.post(
            f"/api/v1/statements/{second}/resolve-duplicate",
            json={"action": "cancel"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        assert response.json() is None

        gone = await client.get(f"/api/v1/statements/{second}", headers=auth)
        assert gone.status_code == 404
        original = await client.get(f"/api/v1/statements/{first}", headers=auth)
        assert original.status_code == 200
        assert original.json()["status"] == "ready"

    async def test_deleting_an_uncommitted_statement_needs_no_confirmation(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """Same as `/discard`: nothing real has happened yet."""
        card_id = await _seed_card(user_a["id"])
        statement_id = await self._reconciled_statement(client, auth, user_a, card_id=card_id)

        response = await client.request(
            "DELETE", f"/api/v1/statements/{statement_id}", json={}, headers=auth
        )
        assert response.status_code == 204, response.text

        gone = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert gone.status_code == 404

    async def test_deleting_a_committed_statement_without_confirm_is_rejected(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        statement_id = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        response = await client.request(
            "DELETE", f"/api/v1/statements/{statement_id}", json={}, headers=auth
        )
        assert response.status_code == 400, response.text

        still_there = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert still_there.status_code == 200

    @pytest.mark.p0
    async def test_deleting_a_committed_statement_recalculates_the_month(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """The one assertion that matters here: the dashboard agrees with the
        transactions behind it, both while the statement exists and once it's
        gone. A delete that forgot to refresh `category_monthly_totals` would
        leave a committed statement's money on the dashboard forever, which
        is the exact failure `AggregateRefresher` exists to prevent (see its
        own module docstring) -- just reached from a new direction.
        """
        from sqlalchemy import select, update

        from app.aggregate.refresh import AggregateRefresher
        from app.db.models import Category, Transaction
        from app.db.session import tenant_session

        card_id = await _seed_card(user_a["id"])
        statement_id = await self._reconciled_statement(client, auth, user_a, card_id=card_id)
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        # Simulate the worker's classify -> aggregate chain (BUILD STEP 7.1),
        # which a real commit only enqueues rather than running inline.
        # `refresh_months` deliberately excludes unclassified rows (they show
        # up in the dashboard's unclassified banner, not folded into a
        # category), so the row needs a category before it will move the
        # month's total at all.
        engine = await _unscoped_engine()
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            async with tenant_session(sessionmaker, uuid.UUID(user_a["id"])) as session:
                category_id = await session.scalar(select(Category.id).limit(1))
                await session.execute(
                    update(Transaction)
                    .where(Transaction.statement_id == uuid.UUID(statement_id))
                    .values(category_id=category_id)
                )
                await AggregateRefresher(session).refresh_statement(uuid.UUID(statement_id))
        finally:
            await engine.dispose()

        before = await client.get(
            "/api/v1/dashboard", params={"range": "month", "month": "2026-07-01"}, headers=auth
        )
        assert before.status_code == 200, before.text
        assert before.json()["months"][0]["total"] == "10.00"

        delete = await client.request(
            "DELETE",
            f"/api/v1/statements/{statement_id}",
            json={"confirm": True},
            headers=auth,
        )
        assert delete.status_code == 204, delete.text

        gone = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert gone.status_code == 404

        after = await client.get(
            "/api/v1/dashboard", params={"range": "month", "month": "2026-07-01"}, headers=auth
        )
        assert after.status_code == 200, after.text
        assert after.json()["months"][0]["total"] == "0.00"
