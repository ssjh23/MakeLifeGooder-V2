"""Classification-in-progress signal: ``ProcessingJob`` and
``StatementResponse.classification_status``.

``status`` cannot carry this: it already means something else during
extraction (``processing``/``needs_review``, set in
``app/extract/pipeline.py``, before commit). ``classification_status`` tracks
the separate post-commit classify/aggregate pipeline instead, backed by the
``ProcessingJob`` row the worker writes.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.classify.llm.fake import FakeLLMAdapter
from app.db.models import JobStage, JobStatus
from app.db.repositories import ProcessingJobRepository, StatementRepository
from app.db.session import tenant_session
from app.extract.base import ParsedRow
from app.worker.tasks import aggregate_statement, classify_statement
from tests.integration.test_import_flow import (
    StubParser,
    _extract,
    _parsed,
    _register_a_statement,
    _seed_card,
)

pytestmark = pytest.mark.integration


async def _seed_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, 'Test User')"),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )


async def _seed_statement(session: AsyncSession) -> uuid.UUID:
    """A minimal statement row: ``processing_jobs.statement_id`` has a real
    foreign key, so a repository-level test needs an actual statement to
    point at, not just a random id."""
    statement = await StatementRepository(session).create(object_key="test-object", card_id=None)
    return statement.id


class TestProcessingJobRepository:
    async def test_start_attempt_creates_a_row_on_first_call(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session)
            repo = ProcessingJobRepository(session)

            job = await repo.start_attempt(statement_id=statement_id, stage=JobStage.CLASSIFY)

            assert job.status == JobStatus.RUNNING
            assert job.attempts == 1
            assert job.started_at is not None

    async def test_a_retry_updates_the_same_row_rather_than_duplicating_it(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """No unique constraint backs ``(statement_id, stage)`` -- the
        repository itself is what stops a retry from creating a second row."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session)
            repo = ProcessingJobRepository(session)

            first = await repo.start_attempt(statement_id=statement_id, stage=JobStage.CLASSIFY)
            await repo.mark_failed(
                statement_id=statement_id, stage=JobStage.CLASSIFY, error="boom"
            )
            second = await repo.start_attempt(statement_id=statement_id, stage=JobStage.CLASSIFY)

            assert first.id == second.id
            assert second.attempts == 2
            assert second.status == JobStatus.RUNNING
            assert second.last_error is None  # cleared on a fresh attempt

            count = await session.scalar(
                text(
                    "SELECT count(*) FROM processing_jobs "
                    "WHERE statement_id = :sid AND stage = 'classify'"
                ),
                {"sid": statement_id},
            )
            assert count == 1

    async def test_mark_succeeded_and_mark_failed_transition_status(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session)
            repo = ProcessingJobRepository(session)

            await repo.start_attempt(statement_id=statement_id, stage=JobStage.CLASSIFY)
            await repo.mark_succeeded(statement_id=statement_id, stage=JobStage.CLASSIFY)
            succeeded = await repo.latest_status(statement_id=statement_id)
            assert succeeded is not None
            assert succeeded.status == JobStatus.SUCCEEDED
            assert succeeded.finished_at is not None

            await repo.start_attempt(statement_id=statement_id, stage=JobStage.AGGREGATE)
            await repo.mark_failed(
                statement_id=statement_id, stage=JobStage.AGGREGATE, error="refresh exploded"
            )
            failed = await repo.latest_status(statement_id=statement_id)
            assert failed is not None
            assert failed.status == JobStatus.FAILED
            assert failed.last_error == "refresh exploded"

    async def test_latest_status_prefers_aggregate_once_it_exists(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Aggregate runs last, so once its job exists it is the one that
        decides ``classification_status`` -- classify's own row is stale."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            statement_id = await _seed_statement(session)
            repo = ProcessingJobRepository(session)

            await repo.start_attempt(statement_id=statement_id, stage=JobStage.CLASSIFY)
            await repo.mark_succeeded(statement_id=statement_id, stage=JobStage.CLASSIFY)
            await repo.start_attempt(statement_id=statement_id, stage=JobStage.AGGREGATE)

            latest = await repo.latest_status(statement_id=statement_id)

            assert latest is not None
            assert latest.stage == JobStage.AGGREGATE
            assert latest.status == JobStatus.RUNNING

    @pytest.mark.p0
    @pytest.mark.security
    async def test_is_scoped_to_the_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        owner = uuid.uuid4()
        stranger = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            statement_id = await _seed_statement(session)
            await ProcessingJobRepository(session).start_attempt(
                statement_id=statement_id, stage=JobStage.CLASSIFY
            )

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            found = await ProcessingJobRepository(session).latest_status(
                statement_id=statement_id
            )
            assert found is None


class TestClassificationStatusOnStatementResponse:
    """End-to-end through the API, mirroring
    ``TestClassifyAndAggregateWorkerTasks`` in ``test_cascade.py``."""

    async def _statement(
        self, client: AsyncClient, auth: dict[str, str], statement_id: str
    ) -> dict[str, object]:
        response = await client.get(f"/api/v1/statements/{statement_id}", headers=auth)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    async def test_none_before_commit(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        statement_id = await _register_a_statement(client, auth)

        body = await self._statement(client, auth, statement_id)

        assert body["classification_status"] is None

    @pytest.mark.p0
    async def test_in_progress_right_after_commit_before_the_worker_runs(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        """No ``ProcessingJob`` row exists yet at this instant -- the worker
        hasn't picked the job up -- so this must not read as ``None``/done."""
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="FAIRPRICE FINEST NEX",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        body = await self._statement(client, auth, statement_id)

        assert body["classification_status"] == "in_progress"

    @pytest.mark.p0
    async def test_done_once_classify_and_aggregate_both_succeed(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="FAIRPRICE FINEST NEX",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        await classify_statement(
            statement_id=statement_id, user_id=user_a["id"], llm=FakeLLMAdapter()
        )
        await aggregate_statement(statement_id=statement_id, user_id=user_a["id"])

        body = await self._statement(client, auth, statement_id)

        assert body["classification_status"] == "done"

    @pytest.mark.p0
    async def test_failed_when_a_stage_job_is_marked_failed(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        """A provider outage never raises out of ``classify()`` -- an
        unresolved descriptor is a normal outcome, left for review (BUILD
        STEP 6.x), not a stage failure. So the ``failed`` mapping is exercised
        directly against a ``ProcessingJob`` row, the same signal a genuine
        aggregate crash (e.g. a database error mid-refresh) would leave
        behind, rather than by trying to make the fake LLM raise."""
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="FAIRPRICE FINEST NEX",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            repo = ProcessingJobRepository(session)
            await repo.start_attempt(
                statement_id=uuid.UUID(statement_id), stage=JobStage.CLASSIFY
            )
            await repo.mark_failed(
                statement_id=uuid.UUID(statement_id),
                stage=JobStage.CLASSIFY,
                error="provider outage",
            )

        body = await self._statement(client, auth, statement_id)

        assert body["classification_status"] == "failed"
