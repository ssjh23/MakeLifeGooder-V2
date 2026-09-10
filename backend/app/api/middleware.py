"""Request context.

Assigns the two identifiers everything downstream inherits, and returns one of
them to the caller.

  request_id  one HTTP request. Returned as ``X-Request-ID`` and shown on error
              screens, so a user's bug report carries its own correlation id.
  trace_id    the whole lifecycle, including the worker. Survives the async hop
              because the traceparent travels with the job.

Both are bound as structlog contextvars, so every log line emitted during the
request carries them without any call site passing them along. A log line
cannot disagree with the trace it belongs to, because it does not get a say.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.telemetry import bind, clear, get_logger, get_tracer

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # An inbound header is honoured so a proxy or a client retry keeps one
        # id across hops, but it is not trusted for anything: the id is a
        # correlation handle, never an authorisation input.
        request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id

        tracer = get_tracer("ledger.api")
        started = time.perf_counter()

        with tracer.start_as_current_span(f"{request.method} {request.url.path}"):
            clear()
            bind(
                request_id=request_id,
                http_method=request.method,
                http_path=request.url.path,
            )
            try:
                response = await call_next(request)
            except Exception:
                # Logged here because the exception handlers return a response
                # and would otherwise swallow the stack.
                logger.exception(
                    "http.unhandled_error",
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
                raise
            finally:
                clear()

        response.headers[REQUEST_ID_HEADER] = request_id
        return response
