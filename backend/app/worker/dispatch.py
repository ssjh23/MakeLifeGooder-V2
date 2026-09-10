"""Stage dispatch, retries and dead-lettering.

Three stages, separately resumable: extract, classify, aggregate. They
communicate only through the database and the job payload, never by calling each
other. That is what makes a classify failure retry without re-parsing the PDF,
which matters because parsing is the expensive part and the model provider is
the flaky part.

The other thing this module does is reopen the span the API started, so one
trace covers the request and the work it caused.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

from app.telemetry import context_from_traceparent, events, get_logger, get_tracer

logger = get_logger(__name__)

MAX_ATTEMPTS = 3


@contextmanager
def restored_trace(*, traceparent: str | None, stage: str) -> Iterator[None]:
    """Reopen the trace the API began.

    Without this the worker starts a fresh, unrelated trace, and the first
    question ADR-014 requires answering, *which request does this belong to*,
    has no answer. The whole point of carrying a traceparent on the job is
    spent here.
    """
    parent = context_from_traceparent(traceparent)
    tracer = get_tracer("ledger.worker")
    with tracer.start_as_current_span(f"worker.{stage}", context=parent):
        yield


async def run_stage(
    *,
    stage: str,
    statement_id: str,
    traceparent: str | None,
    attempt: int,
    handler: Callable[[], Awaitable[None]],
) -> None:
    """Run one stage with telemetry, retry accounting and dead-lettering.

    Emits a start event and exactly one terminal event, so a trace that stops
    at ``started`` means the worker died mid-stage rather than that the work
    quietly succeeded without saying so.
    """
    started = time.perf_counter()

    with restored_trace(traceparent=traceparent, stage=stage):
        logger.info(
            f"statement.{stage}.started",
            stage=stage,
            statement_id=statement_id,
            attempt=attempt,
        )
        try:
            await handler()
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if attempt < MAX_ATTEMPTS:
                # WARN, not ERROR: a retry that later succeeds is not a fault,
                # and logging it as one makes the error rate meaningless.
                logger.warning(
                    events.JOB_RETRY_SCHEDULED,
                    stage=stage,
                    statement_id=statement_id,
                    attempt=attempt,
                    duration_ms=duration_ms,
                    reason=type(exc).__name__,
                )
            else:
                logger.error(
                    events.JOB_DEAD_LETTERED,
                    stage=stage,
                    statement_id=statement_id,
                    attempt=attempt,
                    duration_ms=duration_ms,
                    reason=type(exc).__name__,
                )
            raise

        logger.info(
            f"statement.{stage}.succeeded",
            stage=stage,
            statement_id=statement_id,
            attempt=attempt,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )


def next_stage(stage: str) -> str | None:
    """The pipeline order. ``None`` means the statement is ready for review."""
    return {"extract": "classify", "classify": "aggregate", "aggregate": None}[stage]


__all__ = ["MAX_ATTEMPTS", "next_stage", "restored_trace", "run_stage"]
