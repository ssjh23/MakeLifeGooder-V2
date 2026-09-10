"""Categories and account payloads. Screens 08 to 08c, plus category management.

Category rename, merge and delete are flagged as an open question in User
Flows: categories are created inline during review but the wireframe gives them
no management surface. Specified here so the gap is visible in the API rather
than discovered mid-build.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import Period, Schema


class CategoryCreate(Schema):
    name: str = Field(max_length=100)
    colour: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    kind: Literal["income", "expense", "transfer"] = "expense"


class CategoryUpdate(Schema):
    name: str | None = Field(default=None, max_length=100)
    colour: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CategoryResponse(Schema):
    id: uuid.UUID
    name: str
    slug: str
    kind: Literal["income", "expense", "transfer"]
    colour: str | None = None
    is_system: bool
    row_count: int


class CategoryMergeRequest(Schema):
    """Moves rows, then deletes the source.

    A rule pointing at the merged-away category has to follow it or be flagged.
    A dangling rule silently stops classifying anything (TC-CAT-007).
    """

    into_category_id: uuid.UUID


class AccountInventory(Schema):
    """The counts shown on screen 08, and reused verbatim by the deletion
    screen. One source, so what a person is told they are deleting matches what
    they were told they have."""

    statements: int
    transactions: int
    rules: int
    categories: int
    cards: int


class ExportRequest(Schema):
    scope: Literal["all", "category", "card", "statement"] = "all"
    #: Required unless the scope is everything.
    scope_id: uuid.UUID | None = None
    period: Period | None = None
    format: Literal["csv"] = "csv"
    grouping: Literal["by_month", "by_category", "flat"] = "by_month"


class ExportResponse(Schema):
    """Columns and counts declared before the file is written.

    A person about to take their data elsewhere should know what they are
    getting before they commit to it.
    """

    export_id: uuid.UUID
    status: Literal["pending", "ready", "failed"]
    columns: list[str]
    row_count: int
    estimated_size_bytes: int
    filename: str
    download_url: str | None = None
    created_at: datetime


class DeleteAccountRequest(Schema):
    """Literal, case-sensitive.

    The deletion spans the database and the bucket and is not recoverable, so
    the confirmation is something a person has to type rather than a button
    they can hit by reflex (TC-ACCT-007).
    """

    confirmation: Literal["DELETE"]
