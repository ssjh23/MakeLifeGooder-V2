"""Errors, and the status codes that carry meaning.

Status codes here are part of the interface, not decoration:

  404  not found, **or** owned by another user. One code for both, because
       distinguishing them tells an attacker which ids exist. Row level
       security produces this for free: another tenant's row is invisible
       rather than forbidden, so the handler does not have to remember.

  409  a conflict a person has to settle. Screens 03d, 07b and 07d are
       conflict-resolution interfaces, so the payload is structured for the
       client to render directly rather than being prose. Never resolved
       silently.

  422  a valid request in an invalid state. Committing an unreconciled
       statement, or finishing a review with money unassigned. This is the
       server-side gate; the disabled button in the UI is not the enforcement.

Every response carries ``X-Request-ID`` and every error body repeats it, so a
bug report arrives with its own correlation id already in it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.telemetry import get_logger

logger = get_logger(__name__)


class LedgerError(Exception):
    """Base for errors that map to a documented status code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(LedgerError):
    """Absent, or belonging to somebody else. Deliberately indistinguishable."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Conflict(LedgerError):
    """A collision a person must resolve. Carries the options in ``details``."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class AlreadyImported(Conflict):
    """One statement per card per period (screen 03d).

    ``details`` carries ``existing_statement_id`` and the available
    ``resolutions``: replace, keep_both, cancel.
    """

    code = "statement_already_imported"


class RuleConflict(Conflict):
    """Two rules match the same rows (screens 07b, 07d).

    Same payload shape as a duplicate statement on purpose, so one client
    component renders both and the two stay aligned.
    """

    code = "rule_conflict"


class DuplicateFileUpload(Conflict):
    """The exact same file was already uploaded (screen 03's warning).

    ``details`` carries ``existing_statement_id`` and ``resolutions``:
    replace or cancel. Unlike ``AlreadyImported``'s ``keep_both``, there is
    no legitimate reason to keep two statements built from byte-identical
    files, so that option doesn't exist here.
    """

    code = "duplicate_file_upload"


class CategoryAlreadyExists(Conflict):
    """The tenant already has a category at this slug (screen 04b's "+ New
    category").

    ``uq_categories_user_id_slug`` would catch this at the database level
    regardless, but as a raw ``IntegrityError`` rather than a response a
    client can act on -- this is the checked-first version, with
    ``existing_category_id`` in ``details`` so the client can offer to use
    it instead of asking the person to rename theirs.
    """

    code = "category_already_exists"


class DescriptorAlreadySplit(Conflict):
    """This raw descriptor already has its own override (screen 04's
    evidence list).

    ``details`` carries the ``descriptor_key`` the earlier split already
    produced, checked first rather than caught after because a second split
    is meaningless, not a database collision to translate.
    """

    code = "descriptor_already_split"


class DescriptorNotInGroup(LedgerError):
    """The raw text named in a split request isn't actually part of the
    merchant group ``descriptor_key`` names. Right shape, wrong pairing."""

    code = "descriptor_not_in_group"


class CannotMergeIntoSelf(LedgerError):
    """A merge's source and target named the same merchant group."""

    code = "cannot_merge_into_self"


class InvalidState(LedgerError):
    """Right shape, wrong state."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "invalid_state"


class NotReconciled(InvalidState):
    """Rows do not sum to the printed total and no gap was accepted.

    The gate. ``details`` carries the difference and currency so screen 03b can
    show what is missing without a second request.
    """

    code = "statement_not_reconciled"


class ReviewIncomplete(InvalidState):
    """Review cannot finish while any money is unassigned.

    "Skip for now" is allowed during review and does not unlock the dashboard.
    """

    code = "review_incomplete"


class RateLimited(LedgerError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class OidcNotConfigured(LedgerError):
    """No Auth0 application is configured for this environment (ADR-008).

    Every local dev box and the test suite run this way by design -- the
    point of the seam is that the whole product works with no external
    identity provider. A clear 503 here beats the SDK's own
    ``ConfigurationError`` surfacing as an unexplained 500.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "oidc_not_configured"


class OidcUpstreamError(LedgerError):
    """Auth0 itself failed or refused the request.

    Distinct from :class:`OidcNotConfigured`: the application *is*
    configured, but the identity provider could not complete this attempt
    (network failure, bad credentials, a rejected authorization request). 502
    rather than 503, because the fault is the upstream's, not a gap in this
    deployment's setup.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "oidc_upstream_error"


class Unauthenticated(LedgerError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


def _envelope(
    *, code: str, message: str, details: dict[str, Any], request_id: str | None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LedgerError)
    async def _ledger_error(request: Request, exc: LedgerError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        # Logged at warning, not error: these are expected outcomes of a
        # documented interface, not faults. Reserving ERROR for genuine faults
        # is what keeps an error rate meaningful.
        logger.warning(
            "http.client_error",
            http_status=exc.status_code,
            reason=exc.code,
            http_path=request.url.path,
            http_method=request.method,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=request_id,
            ),
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default for a validation failure is 422, which this API
        # reserves for invalid *state*. A malformed body is 400, so the two
        # stay distinguishable by a client.
        request_id = getattr(request.state, "request_id", None)
        # A custom validator (e.g. Schema's "exactly one of X or Y") can put
        # the raw exception it raised into an error's `ctx`, which json.dumps
        # cannot serialise. The message already states the problem in words;
        # dropping `ctx` loses nothing a client could act on.
        errors = [{k: v for k, v in error.items() if k != "ctx"} for error in exc.errors()]
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_envelope(
                code="malformed_request",
                message="The request body or parameters are not valid.",
                details={"errors": errors},
                request_id=request_id,
            ),
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    @app.exception_handler(NotImplementedError)
    async def _not_implemented(request: Request, exc: NotImplementedError) -> JSONResponse:
        # The scaffold's honest answer. Every unwritten service method reaches
        # here, and it is a 501 rather than a 500 so an unimplemented endpoint
        # is distinguishable from a broken one while the logic is being written.
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            content=_envelope(
                code="not_implemented",
                message=f"Not written yet: {exc}",
                details={},
                request_id=request_id,
            ),
            headers={"X-Request-ID": request_id} if request_id else None,
        )
