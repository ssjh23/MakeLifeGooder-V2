"""FastAPI application.

A modular monolith with a worker pool beside it. Not microservices: at the
sizing this system was designed for, peak load is a fraction of a request per
second, and four network hops between four services would buy nothing but four
more things that can be down.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routers import account, auth, cards, dashboard, health, review, rules, statements
from app.config import get_settings
from app.telemetry import configure_telemetry, get_logger

API_PREFIX = "/api/v1"

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_telemetry(
        log_level=settings.log_level,
        log_hash_salt=settings.log_hash_salt.get_secret_value(),
        # Human-readable locally, JSON everywhere else. The processor chain and
        # the redaction are identical either way, so what a developer reads is
        # the same content the log backend receives.
        json_output=settings.environment != "local",
    )
    logger.info("api.started", reason=settings.environment)
    yield
    logger.info("api.stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Ledger",
        version="0.1.0",
        summary="Bank statement classifier for Singapore",
        description=(
            "Row level security scopes every endpoint to the authenticated user, "
            "so no path carries a user_id. 404 covers both absent and "
            "belonging-to-someone-else. 409 is a conflict a person must settle. "
            "422 is a valid request in an invalid state."
        ),
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )

    # Order matters. Request context is outermost, so a CORS rejection is still
    # logged with a request id.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )

    register_exception_handlers(app)

    # Operational probes sit outside the versioned prefix: an orchestrator's
    # health check should not have to know about API versions.
    app.include_router(health.router)

    for router in (
        auth.router,
        cards.router,
        statements.router,
        review.router,
        dashboard.router,
        rules.router,
        account.categories_router,
        account.account_router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.middleware("http")
    async def _rate_limit_headers(request: Request, call_next):  # noqa: ANN202
        response = await call_next(request)
        verdict = getattr(request.state, "rate_limit", None)
        if verdict is not None:
            response.headers["X-RateLimit-Limit"] = str(verdict.limit)
            response.headers["X-RateLimit-Remaining"] = str(verdict.remaining)
            response.headers["X-RateLimit-Reset"] = str(verdict.reset_epoch)
        return response

    return app


app = create_app()
