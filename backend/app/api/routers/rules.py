"""Rules. Screens 07 to 07e."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession
from app.schemas.rules import (
    ReapplyPreview,
    ReapplyPreviewRequest,
    ReapplyRequest,
    ResolveConflictRequest,
    RuleCreate,
    RuleConflictSummary,
    RulePreview,
    RulePreviewRequest,
    RuleResponse,
    RuleUpdate,
)
from app.services.rules import RuleService

router = APIRouter(prefix="/rules", tags=["rules"])


def _service(session: DbSession) -> RuleService:
    return RuleService(session)


ServiceDep = Annotated[RuleService, Depends(_service)]


@router.get("", response_model=list[RuleResponse])
async def list_rules(service: ServiceDep) -> list[RuleResponse]:
    return await service.list_rules()  # type: ignore[return-value]


@router.post("/preview", response_model=RulePreview)
async def preview_rule(payload: RulePreviewRequest, service: ServiceDep) -> RulePreview:
    """POST despite being read-only: patterns contain characters that a query
    string mangles, and a preview of a mangled pattern describes a different
    rule than the one about to be saved."""
    return await service.preview(  # type: ignore[return-value]
        pattern=payload.pattern, match_type=payload.match_type, options=payload.options
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RuleResponse)
async def create_rule(payload: RuleCreate, service: ServiceDep) -> RuleResponse:
    """409 on collision, with a suggested narrowing."""
    return await service.create(  # type: ignore[return-value]
        pattern=payload.pattern,
        match_type=payload.match_type,
        category_id=payload.category_id,
        scope=payload.scope,
        options=payload.options,
    )


@router.patch("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: uuid.UUID, payload: RuleUpdate, service: ServiceDep
) -> RuleResponse:
    return await service.update(rule_id, **payload.model_dump(exclude_unset=True))  # type: ignore[return-value]


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID, service: ServiceDep) -> None:
    await service.delete(rule_id)


@router.get("/conflicts", response_model=list[RuleConflictSummary])
async def list_conflicts(service: ServiceDep) -> list[RuleConflictSummary]:
    return await service.conflicts()  # type: ignore[return-value]


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    conflict_id: uuid.UUID, payload: ResolveConflictRequest, service: ServiceDep
) -> dict[str, str]:
    """The effect on totals is stated before the change is committed."""
    await service.resolve_conflict(
        conflict_id, action=payload.action, apply_to_history=payload.apply_to_history
    )
    return {"status": "ok"}


@router.post("/reapply/preview", response_model=ReapplyPreview)
async def reapply_preview(
    payload: ReapplyPreviewRequest, service: ServiceDep
) -> ReapplyPreview:
    """Separate from the rerun on purpose. Screen 07e is a preview with a
    button, not a button that reruns."""
    return await service.reapply_preview(payload.months)  # type: ignore[return-value]


@router.post("/reapply", status_code=status.HTTP_202_ACCEPTED)
async def reapply(payload: ReapplyRequest, service: ServiceDep) -> dict[str, str]:
    """Enqueued, because a reapply across every imported month is not a request
    that should be held open.

    ``keep_overrides=False`` discards manual decisions irrecoverably.
    """
    await service.reapply(keep_overrides=payload.keep_overrides, months=payload.months)
    return {"status": "accepted"}
