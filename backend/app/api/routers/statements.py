"""Import and reconciliation. Screens 03 to 03d.

Routers stay thin. No SQL here, and no gate logic here either: the commit gate
lives in the service so that calling the endpoint directly cannot bypass it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, StoreDep, rate_limit
from app.schemas.statements import (
    CommitRequest,
    ManualEntryRequest,
    ResolveDuplicateRequest,
    RowCreate,
    RowMutationResponse,
    RowsResponse,
    RowUpdate,
    StatementRegister,
    StatementResponse,
    StatementTag,
    UnlockRequest,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.services.statement import StatementService

router = APIRouter(prefix="/statements", tags=["import"])


def _service(session: DbSession, store: StoreDep) -> StatementService:
    return StatementService(session, store)


ServiceDep = Annotated[StatementService, Depends(_service)]


@router.post(
    "/upload-url",
    response_model=UploadUrlResponse,
    dependencies=[rate_limit("upload", limit=20, window_seconds=60)],
)
async def create_upload_url(
    payload: UploadUrlRequest, service: ServiceDep
) -> UploadUrlResponse:
    """Issue a presigned PUT. The API never receives the file itself."""
    return await service.create_upload_url(  # type: ignore[return-value]
        content_type=payload.content_type, size_bytes=payload.size_bytes
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=StatementResponse)
async def register_statement(
    payload: StatementRegister, service: ServiceDep
) -> StatementResponse:
    """Record the uploaded object and enqueue extraction, in one transaction.

    202 rather than 201: the row exists but the work has not happened, and the
    client polls. There is no synchronous path (ADR-005).
    """
    return await service.register(  # type: ignore[return-value]
        upload_id=payload.upload_id, card_id=payload.card_id
    )


@router.get("", response_model=list[StatementResponse])
async def list_statements(
    service: ServiceDep, status_filter: str | None = None, card_id: uuid.UUID | None = None
) -> list[StatementResponse]:
    raise NotImplementedError("Statement listing is not written yet.")


@router.get("/{statement_id}", response_model=StatementResponse)
async def get_statement(statement_id: uuid.UUID, service: ServiceDep) -> StatementResponse:
    """What the client polls. Terminal states are ready and failed."""
    return await service.get(statement_id)  # type: ignore[return-value]


@router.patch("/{statement_id}", response_model=StatementResponse)
async def tag_statement(
    statement_id: uuid.UUID, payload: StatementTag, service: ServiceDep
) -> StatementResponse:
    raise NotImplementedError("Retagging is not written yet.")


@router.get("/{statement_id}/rows", response_model=RowsResponse)
async def get_rows(statement_id: uuid.UUID, service: ServiceDep) -> RowsResponse:
    return await service.rows(statement_id)  # type: ignore[return-value]


# -- Reconciliation, screen 03b --------------------------------------------
# All four mutations return live reconciliation state, so the countdown updates
# from the response rather than a refetch per keystroke.


@router.post("/{statement_id}/rows", response_model=RowMutationResponse)
async def add_row(
    statement_id: uuid.UUID, payload: RowCreate, service: ServiceDep
) -> RowMutationResponse:
    return await service.add_row(  # type: ignore[return-value]
        statement_id,
        posted_on=payload.posted_on,
        description=payload.description,
        amount=payload.amount,  # type: ignore[arg-type]
    )


@router.patch("/{statement_id}/rows/{row_id}", response_model=RowMutationResponse)
async def edit_row(
    statement_id: uuid.UUID, row_id: uuid.UUID, payload: RowUpdate, service: ServiceDep
) -> RowMutationResponse:
    return await service.edit_row(  # type: ignore[return-value]
        statement_id, row_id, **payload.model_dump(exclude_unset=True)
    )


@router.post("/{statement_id}/rows/{row_id}/skip", response_model=RowMutationResponse)
async def skip_row(
    statement_id: uuid.UUID, row_id: uuid.UUID, service: ServiceDep
) -> RowMutationResponse:
    return await service.skip_row(statement_id, row_id)  # type: ignore[return-value]


@router.delete("/{statement_id}/rows/{row_id}", response_model=RowMutationResponse)
async def delete_row(
    statement_id: uuid.UUID, row_id: uuid.UUID, service: ServiceDep
) -> RowMutationResponse:
    return await service.delete_row(statement_id, row_id)  # type: ignore[return-value]


# -- Failure recovery, screen 03c ------------------------------------------


@router.post("/{statement_id}/unlock", status_code=status.HTTP_202_ACCEPTED)
async def unlock(
    statement_id: uuid.UUID, payload: UnlockRequest, service: ServiceDep
) -> dict[str, str]:
    """Retry extraction with a password.

    Used once, in memory. Never stored, never logged, never placed in the job
    payload.
    """
    await service.unlock(statement_id, password=payload.password.get_secret_value())
    return {"status": "accepted"}


@router.post("/{statement_id}/rows/manual", status_code=status.HTTP_202_ACCEPTED)
async def open_manual_entry(
    statement_id: uuid.UUID, payload: ManualEntryRequest, service: ServiceDep
) -> dict[str, str]:
    """Hand entry for a scan.

    Flagged as an open question in User Flows: this endpoint is specified but
    the screen is not drawn, and reconciliation against a user-supplied total
    is not yet defined. Present so the gap is visible in the API rather than
    discovered mid-build.
    """
    raise NotImplementedError("Manual row entry has no designed surface yet.")


@router.delete("/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_statement(statement_id: uuid.UUID, service: ServiceDep) -> None:
    """Remove a file that was not a statement. Pre-commit only."""
    raise NotImplementedError("Pre-commit deletion is not written yet.")


# -- Commit -----------------------------------------------------------------


@router.post("/{statement_id}/commit", response_model=StatementResponse)
async def commit(
    statement_id: uuid.UUID, payload: CommitRequest, service: ServiceDep
) -> StatementResponse:
    """The gate.

    422 when the rows do not tie and no gap was accepted. 409 when a statement
    for the same card and period already exists.
    """
    return await service.commit(  # type: ignore[return-value]
        statement_id, accept_gap=payload.accept_gap, gap_reason=payload.gap_reason
    )


@router.post("/{statement_id}/discard", status_code=status.HTTP_204_NO_CONTENT)
async def discard(statement_id: uuid.UUID, service: ServiceDep) -> None:
    await service.discard(statement_id)


@router.post("/{statement_id}/resolve-duplicate", response_model=StatementResponse)
async def resolve_duplicate(
    statement_id: uuid.UUID, payload: ResolveDuplicateRequest, service: ServiceDep
) -> StatementResponse:
    return await service.resolve_duplicate(  # type: ignore[return-value]
        statement_id, action=payload.action, target_card_id=payload.target_card_id
    )


@router.get("/{statement_id}/source-page/{page}")
async def source_page(
    statement_id: uuid.UUID, page: int, user_id: CurrentUser
) -> dict[str, str]:
    """Short-lived signed URL for one page of the source PDF.

    Bearer credential. Never logged.
    """
    raise NotImplementedError("Source page viewing is not written yet.")
