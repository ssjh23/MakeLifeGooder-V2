"""Review. Screens 04, 04b, 04c and 05."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession
from app.schemas.review import (
    ClassifyRequest,
    ConfirmAllRequest,
    DuplicatePair,
    ImportSummary,
    MergeDescriptorRequest,
    ResolveDuplicateRequest,
    ReviewBoard,
    SplitDescriptorRequest,
)
from app.services.review import ReviewService

router = APIRouter(tags=["review"])


def _service(session: DbSession) -> ReviewService:
    return ReviewService(session)


ServiceDep = Annotated[ReviewService, Depends(_service)]


@router.get("/statements/{statement_id}/review", response_model=ReviewBoard)
async def review_board(statement_id: uuid.UUID, service: ServiceDep) -> ReviewBoard:
    """Grouped by merchant. Four rows from one shop are one decision."""
    return await service.board(statement_id)  # type: ignore[return-value]


@router.get(
    "/statements/{statement_id}/review/duplicates", response_model=list[DuplicatePair]
)
async def review_duplicates(
    statement_id: uuid.UUID, service: ServiceDep
) -> list[DuplicatePair]:
    return await service.duplicates(statement_id)  # type: ignore[return-value]


@router.post("/statements/{statement_id}/duplicates/{pair_id}/resolve")
async def resolve_duplicate(
    statement_id: uuid.UUID,
    pair_id: uuid.UUID,
    payload: ResolveDuplicateRequest,
    service: ServiceDep,
) -> dict[str, str]:
    """Removal is undoable and never alters the statement record."""
    await service.resolve_duplicate(
        statement_id,
        pair_id,
        action=payload.action,
        remove_row_id=payload.remove_row_id,
    )
    return {"status": "ok"}


@router.post("/statements/{statement_id}/duplicates/{pair_id}/undo")
async def undo_duplicate(
    statement_id: uuid.UUID, pair_id: uuid.UUID, service: ServiceDep
) -> dict[str, str]:
    await service.undo_duplicate(statement_id, pair_id)
    return {"status": "ok"}


@router.post("/review/merchants/{descriptor_key}/classify")
async def classify_merchant(
    descriptor_key: str, payload: ClassifyRequest, service: ServiceDep
) -> dict[str, str]:
    """One decision for one merchant.

    409 when creating the rule would collide with an existing one, carrying the
    same payload shape as the rules endpoints.
    """
    await service.classify(
        descriptor_key,
        category_id=payload.category_id,
        new_category_name=payload.new_category_name,
        create_rule=payload.create_rule,
        scope=payload.scope,
        pattern=payload.pattern,
        match_type=payload.match_type,
        options=payload.options,
    )
    return {"status": "ok"}


@router.post("/review/merchants/{descriptor_key}/split")
async def split_merchant_descriptor(
    descriptor_key: str, payload: SplitDescriptorRequest, service: ServiceDep
) -> dict[str, str]:
    """Eject one exact raw line out of a wrongly-merged group.

    409 if this raw text was already split before; 400 if it doesn't
    actually belong to the named group.
    """
    new_key = await service.split_descriptor(
        descriptor_key, description_raw=payload.description_raw
    )
    return {"status": "ok", "descriptor_key": new_key}


@router.post("/review/merchants/{descriptor_key}/merge")
async def merge_merchant_descriptor(
    descriptor_key: str, payload: MergeDescriptorRequest, service: ServiceDep
) -> dict[str, str]:
    """Fold a merchant group into another, existing one -- the inverse of
    split, for two descriptors normalise() failed to unify.

    400 if the source and target name the same group; 404 if either group
    has no rows.
    """
    await service.merge_descriptors(
        descriptor_key, target_descriptor_key=payload.target_descriptor_key
    )
    return {"status": "ok"}


@router.post("/statements/{statement_id}/review/confirm-all")
async def confirm_all(
    statement_id: uuid.UUID, payload: ConfirmAllRequest, service: ServiceDep
) -> dict[str, str]:
    await service.confirm_all(statement_id, payload.descriptor_keys)
    return {"status": "ok"}


@router.post("/statements/{statement_id}/review/finish", response_model=ImportSummary)
async def finish_review(statement_id: uuid.UUID, service: ServiceDep) -> ImportSummary:
    """422 while any money is unassigned."""
    return await service.finish(statement_id)  # type: ignore[return-value]


@router.get("/statements/{statement_id}/summary", response_model=ImportSummary)
async def import_summary(statement_id: uuid.UUID, service: ServiceDep) -> ImportSummary:
    return await service.summary(statement_id)  # type: ignore[return-value]
