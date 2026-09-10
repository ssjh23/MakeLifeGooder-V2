"""Categories and account. Screens 08 to 08c."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, StoreDep
from app.schemas.account import (
    AccountInventory,
    CategoryCreate,
    CategoryMergeRequest,
    CategoryResponse,
    CategoryUpdate,
    DeleteAccountRequest,
    ExportRequest,
    ExportResponse,
)
from app.services.account import AccountService, CategoryService

categories_router = APIRouter(prefix="/categories", tags=["categories"])
account_router = APIRouter(tags=["account"])


def _categories(session: DbSession) -> CategoryService:
    return CategoryService(session)


def _account(session: DbSession, store: StoreDep) -> AccountService:
    return AccountService(session, store)


CategoryDep = Annotated[CategoryService, Depends(_categories)]
AccountDep = Annotated[AccountService, Depends(_account)]


# -- Categories -------------------------------------------------------------
# Created inline during review, but User Flows gives them no management screen.
# Specified here so the gap is visible in the API rather than found mid-build.


@categories_router.get("", response_model=list[CategoryResponse])
async def list_categories(service: CategoryDep) -> list[CategoryResponse]:
    return await service.list_categories()  # type: ignore[return-value]


@categories_router.post("", status_code=status.HTTP_201_CREATED, response_model=CategoryResponse)
async def create_category(payload: CategoryCreate, service: CategoryDep) -> CategoryResponse:
    return await service.create(  # type: ignore[return-value]
        name=payload.name, colour=payload.colour, kind=payload.kind
    )


@categories_router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID, payload: CategoryUpdate, service: CategoryDep
) -> CategoryResponse:
    return await service.update(category_id, **payload.model_dump(exclude_unset=True))  # type: ignore[return-value]


@categories_router.post("/{category_id}/merge", response_model=CategoryResponse)
async def merge_category(
    category_id: uuid.UUID, payload: CategoryMergeRequest, service: CategoryDep
) -> CategoryResponse:
    """Moves rows, then deletes the source. A rule pointing at the source must
    follow it or be flagged, never left dangling."""
    return await service.merge(category_id, into_category_id=payload.into_category_id)  # type: ignore[return-value]


@categories_router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: uuid.UUID, service: CategoryDep) -> None:
    """Only when empty."""
    await service.delete(category_id)


# -- Account ----------------------------------------------------------------


@account_router.get("/account", response_model=AccountInventory)
async def inventory(service: AccountDep) -> AccountInventory:
    return await service.inventory()  # type: ignore[return-value]


@account_router.post("/exports", status_code=status.HTTP_202_ACCEPTED, response_model=ExportResponse)
async def start_export(payload: ExportRequest, service: AccountDep) -> ExportResponse:
    """Columns, row count and size are declared before the file is written."""
    return await service.start_export(**payload.model_dump())  # type: ignore[return-value]


@account_router.get("/exports/{export_id}", response_model=ExportResponse)
async def get_export(export_id: uuid.UUID, service: AccountDep) -> ExportResponse:
    return await service.get_export(export_id)  # type: ignore[return-value]


@account_router.delete("/account", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(
    payload: DeleteAccountRequest, service: AccountDep
) -> dict[str, str]:
    """Signs out immediately and purges in the background.

    The purge spans the database and the object store, so it is an idempotent
    job: a partial failure has to be safe to re-run. ``audit_log`` survives by
    design.
    """
    await service.delete_everything(confirmation=payload.confirmation)
    return {"status": "accepted"}
