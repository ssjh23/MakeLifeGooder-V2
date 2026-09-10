"""Trace context, and the one hop that makes it hard.

The HTTP request ends when the job is enqueued. The work happens later, in a
different process, possibly on a different machine. Instrument each side
naively and you get two unrelated traces, and the first of the three questions
ADR-014 requires answering, *which request is this*, has no answer.

So the W3C ``traceparent`` is serialised into the job payload when the API
enqueues, and the worker reopens the span the API started. One trace spans both
processes, and a statement can be located by ``trace_id`` alone.

A note on where the traceparent lives. CLAUDE.md says "stored on the job row".
procrastinate owns its own table schema, so it travels as a task argument,
which lands in that row's ``args`` JSONB. Same guarantee, same test, no forked
dependency. Promoting it to a dedicated column later is an additive migration.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from app.telemetry.redact import RedactionProcessor

TRACEPARENT_KEY = "traceparent"

_tracer_provider: TracerProvider | None = None


def configure_telemetry(*, log_level: str, log_hash_salt: str, json_output: bool = True) -> None:
    """Install the tracer provider and the structlog pipeline.

    Called once at process start, by both the API and the worker, so the two
    emit the same shape.

    No exporter is configured. Spans still carry real trace and span ids, which
    is all the logs need, and adding an OTLP exporter later is configuration
    rather than re-instrumentation. That was the argument for emitting
    OTel-compatible spans now while running no collector (ADR-014).
    """
    global _tracer_provider
    if _tracer_provider is None:
        _tracer_provider = TracerProvider()
        trace.set_tracer_provider(_tracer_provider)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _add_trace_identifiers,
            # Last. Nothing may add a field after the allow-list has run.
            RedactionProcessor(salt=log_hash_salt),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def _add_trace_identifiers(
    logger: Any,  # noqa: ANN401 - structlog's processor signature
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Stamp the active span's ids onto every event.

    Read from the span rather than passed by callers, so a log line cannot
    disagree with the trace it belongs to.
    """
    span = trace.get_current_span()
    context = span.get_span_context()
    if context.is_valid:
        event_dict.setdefault("trace_id", format(context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(context.span_id, "016x"))
    return event_dict


def current_traceparent() -> str | None:
    """Serialise the active span as a W3C traceparent header value.

    Returns ``None`` when there is no active span, which is the honest answer
    for a job enqueued outside a request, such as by a scheduled task.
    """
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return (
        f"00-{format(context.trace_id, '032x')}-"
        f"{format(context.span_id, '016x')}-"
        f"{format(context.trace_flags, '02x')}"
    )


def context_from_traceparent(traceparent: str | None) -> trace.Context | None:
    """Rebuild a span context from a traceparent produced by the API.

    Returns ``None`` for anything malformed rather than raising. A job whose
    trace cannot be reconstructed must still run: losing the correlation is a
    degraded log, while refusing the work would turn an observability problem
    into an outage.
    """
    if not traceparent:
        return None
    parts = traceparent.split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    try:
        trace_id = int(parts[1], 16)
        span_id = int(parts[2], 16)
        flags = int(parts[3], 16)
    except ValueError:
        return None
    if trace_id == 0 or span_id == 0:
        return None

    span_context = SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=True,
        trace_flags=TraceFlags(flags),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def get_tracer(name: str = "ledger") -> trace.Tracer:
    return trace.get_tracer(name)


def bind(**values: Any) -> None:  # noqa: ANN401
    """Bind values onto every subsequent log line in this context."""
    structlog.contextvars.bind_contextvars(**values)


def clear() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str = "ledger") -> Any:  # noqa: ANN401
    return structlog.get_logger(name)
