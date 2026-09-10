"""Cards. Screens 02b to 02d."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession
from app.schemas.cards import (
    ArchivePreview,
    ArchiveRequest,
    CardCreate,
    CardResponse,
    CardUpdate,
    DeleteStatementsRequest,
)
from app.services.account import CardService

router = APIRouter(prefix="/cards", tags=["cards"])


def _service(session: DbSession) -> CardService:
    return CardService(session)


ServiceDep = Annotated[CardService, Depends(_service)]


@router.get("", response_model=list[CardResponse])
async def list_cards(service: ServiceDep) -> list[CardResponse]:
    return await service.list_cards()  # type: ignore[return-value]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CardResponse)
async def create_card(payload: CardCreate, service: ServiceDep) -> CardResponse:
    """The request schema is the complete set of fields the system accepts
    about a card. There is no full number, expiry, CVV or PIN in it."""
    return await service.create(**payload.model_dump())  # type: ignore[return-value]


@router.patch("/{card_id}", response_model=CardResponse)
async def update_card(
    card_id: uuid.UUID, payload: CardUpdate, service: ServiceDep
) -> CardResponse:
    return await service.update(card_id, **payload.model_dump(exclude_unset=True))  # type: ignore[return-value]


@router.get("/{card_id}/archive-preview", response_model=ArchivePreview)
async def archive_preview(card_id: uuid.UUID, service: ServiceDep) -> ArchivePreview:
    """Server-computed, because the client showing a different effect line than
    the one that actually happens is worse than showing none."""
    return await service.archive_preview(card_id)  # type: ignore[return-value]


@router.post("/{card_id}/archive", response_model=CardResponse)
async def archive_card(
    card_id: uuid.UUID, payload: ArchiveRequest, service: ServiceDep
) -> CardResponse:
    """Reversible. Every statement must be given a destination or the whole
    request is refused."""
    return await service.archive(card_id, payload.statement_dispositions)  # type: ignore[return-value]


@router.post("/{card_id}/restore", response_model=CardResponse)
async def restore_card(card_id: uuid.UUID, service: ServiceDep) -> CardResponse:
    """Returns the card with its statements attached.

    Described in screen 02d's copy but with no entry point drawn on 02b. Open
    question in User Flows; the endpoint exists so the behaviour is settled
    even though the route into it is not.
    """
    return await service.restore(card_id)  # type: ignore[return-value]


@router.delete("/{card_id}/statements", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card_statements(
    card_id: uuid.UUID, payload: DeleteStatementsRequest, service: ServiceDep
) -> None:
    """Destructive and not recoverable, unlike archiving."""
    await service.delete_statements(card_id, confirm=payload.confirm)
