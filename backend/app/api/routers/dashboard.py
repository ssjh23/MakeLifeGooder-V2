"""Dashboard. Screens 06, 06b and 06c."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession
from app.schemas.dashboard import (
    CategoryDetail,
    DashboardResponse,
    OverrideRequest,
    OverrideResponse,
    TransactionResponse,
)
from app.services.dashboard import DashboardService

router = APIRouter(tags=["dashboard"])


def _service(session: DbSession) -> DashboardService:
    return DashboardService(session)


ServiceDep = Annotated[DashboardService, Depends(_service)]


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    service: ServiceDep,
    range: Annotated[Literal["month", "6m", "year"], Query()] = "month",
    month: date | None = None,
) -> DashboardResponse:
    """Five bands, served from precomputed monthly totals."""
    return await service.bands(range_=range, month=month)  # type: ignore[return-value]


@router.get("/dashboard/categories/{category_id}", response_model=CategoryDetail)
async def category_detail(category_id: uuid.UUID, service: ServiceDep) -> CategoryDetail:
    return await service.category_detail(category_id)  # type: ignore[return-value]


@router.get("/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    service: ServiceDep,
    category_id: uuid.UUID | None = None,
    card_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    search: str | None = None,
    sort: Annotated[
        Literal["date_desc", "date_asc", "amount_desc", "amount_asc"], Query()
    ] = "date_desc",
) -> list[TransactionResponse]:
    """Screen 06's individual-transactions band (05): search, sort, and every
    filter the wireframe's dropdowns need, all server-side."""
    return await service.list_transactions(
        category_id=category_id,
        card_id=card_id,
        merchant_id=merchant_id,
        from_=from_,
        to=to,
        search=search,
        sort=sort,
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: uuid.UUID, service: ServiceDep
) -> TransactionResponse:
    """The only endpoint that answers *why is this in this category*."""
    return await service.transaction(transaction_id)  # type: ignore[return-value]


@router.post("/transactions/{transaction_id}/override", response_model=OverrideResponse)
async def override_transaction(
    transaction_id: uuid.UUID, payload: OverrideRequest, service: ServiceDep
) -> OverrideResponse:
    """This row only. The row becomes protected from future rule runs, money
    moves between categories, and the statement total is unchanged."""
    return await service.override(transaction_id, category_id=payload.category_id)  # type: ignore[return-value]
