"""Liveness and readiness.

Two probes, and conflating them causes an outage rather than reporting one.

``/health`` answers "is this process alive". It must not touch the database. A
liveness probe that checks dependencies turns a thirty-second database blip
into a restart loop across every container, which takes a brief degradation and
makes it a total outage at exactly the moment the database is under stress.

``/health/ready`` answers "should this instance receive traffic", and that one
does check dependencies, because an instance that cannot reach Postgres should
be taken out of rotation rather than restarted.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import UnscopedSession
from app.schemas.common import Schema

router = APIRouter(tags=["operational"])


class Health(Schema):
    status: Literal["ok"]


class Readiness(Schema):
    status: Literal["ready", "degraded"]
    database: bool
    queue: bool


@router.get("/health", response_model=Health)
async def health() -> Health:
    """No dependency checks. See the module docstring (TC-PERF-005)."""
    return Health(status="ok")


@router.get("/health/ready", response_model=Readiness)
async def readiness(session: UnscopedSession, response: Response) -> Readiness:
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - a failure here is the answer, not an error
        database_ok = False

    # The queue lives in the same Postgres, so its reachability is the
    # database's reachability. Kept as a separate field because that stops
    # being true the moment the queue moves.
    queue_ok = database_ok

    ready = database_ok and queue_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(
        status="ready" if ready else "degraded", database=database_ok, queue=queue_ok
    )
