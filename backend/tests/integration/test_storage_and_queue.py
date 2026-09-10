"""Object storage and the queue. GREEN.

The two seams that behave differently between a laptop and AWS, and where a
convenient local setup would let a broken configuration pass.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import get_settings
from app.storage import UploadRejected, build_object_store

pytestmark = pytest.mark.integration


@pytest.fixture
def store() -> object:
    return build_object_store(get_settings())


class TestPresignedUpload:
    @pytest.mark.p0
    async def test_TC_IMP_001_upload_url_is_scoped_to_one_object(self, store: object) -> None:
        presigned = store.presign_upload(content_type="application/pdf", size_bytes=250_000)
        assert presigned.key.startswith("uploads/")
        assert presigned.upload_id in presigned.key

    @pytest.mark.p0
    async def test_TC_IMP_003_the_bytes_go_to_storage_not_the_api(
        self, store: object
    ) -> None:
        """The browser PUTs straight to the bucket.

        This is the seam that makes concurrent uploads a storage concern rather
        than an API memory concern, and it is the one most likely to be broken
        by a signing misconfiguration.
        """
        presigned = store.presign_upload(content_type="application/pdf", size_bytes=1024)
        body = b"%PDF-1.7\nnot a real statement\n"

        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.put(
                presigned.url, content=body, headers=presigned.required_headers
            )
        assert response.status_code in (200, 204), response.text
        assert store.exists(key=presigned.key)
        assert store.get_bytes(key=presigned.key) == body

    async def test_presigning_uses_the_browser_reachable_host(self, store: object) -> None:
        """A presigned URL's signature covers the host.

        Sign with an address only this process can resolve, such as a container
        hostname, and the browser cannot use the URL, and it cannot be repaired
        by rewriting the host because that invalidates the signature. This is
        the single most likely local-versus-cloud mistake in the codebase.
        """
        settings = get_settings()
        presigned = store.presign_upload(content_type="application/pdf", size_bytes=1024)
        if settings.signing_endpoint:
            assert presigned.url.startswith(settings.signing_endpoint)

    async def test_TC_SEC_008_encryption_header_is_sent(self, store: object) -> None:
        """Sent locally as well as in production, so the production code path
        runs before the first deploy rather than during it."""
        settings = get_settings()
        presigned = store.presign_upload(content_type="application/pdf", size_bytes=1024)
        if settings.s3_server_side_encryption:
            assert (
                presigned.required_headers.get("x-amz-server-side-encryption")
                == settings.s3_server_side_encryption
            )

    async def test_non_pdf_is_refused_before_a_url_is_issued(self, store: object) -> None:
        with pytest.raises(UploadRejected):
            store.presign_upload(content_type="image/png", size_bytes=1024)

    async def test_TC_IMP_013_oversized_upload_is_refused(self, store: object) -> None:
        """Without a ceiling, a presigned URL is permission to fill the bucket."""
        with pytest.raises(UploadRejected):
            store.presign_upload(content_type="application/pdf", size_bytes=100 * 1024 * 1024)

    async def test_delete_is_idempotent(self, store: object) -> None:
        """Account deletion retries after a partial failure, so removing an
        object that is already gone has to succeed."""
        key = f"uploads/{uuid.uuid4().hex}.pdf"
        store.delete(key=key)
        store.delete(key=key)


class TestQueue:
    @pytest.mark.p0
    async def test_TC_TEL_003_traceparent_survives_the_async_hop(self) -> None:
        """The trace context serialises and rebuilds intact.

        Without this the API and the worker produce two unrelated traces, and
        the first question ADR-014 requires answering - which request is this -
        has no answer. Asserted at the unit level here because it is the part
        that silently degrades rather than failing.
        """
        from app.telemetry import context_from_traceparent, current_traceparent, get_tracer

        tracer = get_tracer("test")
        with tracer.start_as_current_span("api-request"):
            traceparent = current_traceparent()

        assert traceparent is not None
        restored = context_from_traceparent(traceparent)
        assert restored is not None

        from opentelemetry import trace as otel

        span = otel.get_current_span(restored)
        assert format(span.get_span_context().trace_id, "032x") == traceparent.split("-")[1]

    async def test_malformed_traceparent_degrades_rather_than_raising(self) -> None:
        """A job whose trace cannot be rebuilt must still run.

        Losing correlation is a degraded log; refusing the work would turn an
        observability problem into an outage.
        """
        from app.telemetry import context_from_traceparent

        for bad in ("", "garbage", "00-tooshort-x-01", "99-" + "0" * 32 + "-" + "0" * 16 + "-01"):
            assert context_from_traceparent(bad) is None

    async def test_worker_connects_directly_not_through_the_pooler(self) -> None:
        """procrastinate uses LISTEN/NOTIFY, which a transaction-mode pooler
        does not carry. Through the pooler the worker silently falls back to
        polling and jobs simply run late, which is a bad week of debugging."""
        settings = get_settings()
        assert ":6432" not in str(settings.database_worker_url), (
            "The worker must not connect through the transaction-mode pooler."
        )


class TestRateLimiting:
    async def test_TC_RATE_001_limit_is_enforced_within_the_window(self) -> None:
        from app.api.ratelimit.memory import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        verdicts = [await limiter.check("upload:user", 5, 60) for _ in range(6)]
        assert [v.allowed for v in verdicts] == [True, True, True, True, True, False]

    async def test_TC_RATE_004_limits_are_per_user(self) -> None:
        from app.api.ratelimit.memory import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        for _ in range(5):
            await limiter.check("upload:alice", 5, 60)
        assert (await limiter.check("upload:bob", 5, 60)).allowed is True

    async def test_TC_RATE_005_buckets_are_namespaced(self) -> None:
        """Exhausting the upload limit must not block signing in."""
        from app.api.ratelimit.memory import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        for _ in range(5):
            await limiter.check("upload:alice", 5, 60)
        assert (await limiter.check("login:alice", 5, 60)).allowed is True

    @pytest.mark.p1
    async def test_TC_RATE_007_in_memory_limiting_is_per_container(self) -> None:
        """Documents the gap ADR-010 knowingly accepts.

        Two containers give twice the intended limit. Acceptable before public
        exposure and not after, which is precisely the trigger for introducing
        Redis. Asserted so the limitation lives in the test output rather than
        only in a document.
        """
        from app.api.ratelimit.memory import InMemoryRateLimiter

        container_one = InMemoryRateLimiter()
        container_two = InMemoryRateLimiter()

        for _ in range(5):
            await container_one.check("upload:alice", 5, 60)

        assert (await container_one.check("upload:alice", 5, 60)).allowed is False
        assert (await container_two.check("upload:alice", 5, 60)).allowed is True
