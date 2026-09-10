"""Dashboard payloads. Screens 06, 06b and 06c.

Coarse to fine: how much, then who, then why. Served from precomputed monthly
totals, so no request aggregates transactions.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from app.schemas.common import MoneyStr, Schema
from app.schemas.review import ClassifiedByLiteral

RangeLiteral = Literal["month", "6m", "year"]


class UnclassifiedBanner(Schema):
    """statement total = classified + unclassified.

    Shown whenever anything is unassigned. Dashboard totals stay locked while
    this is non-zero, because a total that silently omits money is worse than
    no total.
    """

    classified: MoneyStr
    unclassified: MoneyStr
    locked: bool


class MonthBand(Schema):
    month: date
    total: MoneyStr
    by_category: dict[str, MoneyStr]


class CategoryBand(Schema):
    category_id: uuid.UUID
    name: str
    total: MoneyStr
    share: float
    change: float | None = None


class CardBand(Schema):
    card_id: uuid.UUID
    nickname: str
    colour: str | None = None
    total: MoneyStr


class DashboardResponse(Schema):
    banner: UnclassifiedBanner
    months: list[MonthBand]
    categories: list[CategoryBand]
    cards: list[CardBand]
    range: RangeLiteral


class CompanyRow(Schema):
    merchant_id: uuid.UUID
    name: str
    total: MoneyStr
    share: float
    transaction_count: int


class CategoryDetail(Schema):
    category_id: uuid.UUID
    name: str
    six_month_totals: list[MonthBand]
    companies: list[CompanyRow]
    cards_used: list[CardBand]


class Provenance(Schema):
    """Why this row is in this category.

    Screen 06c is the only place that answers it, which is the reason the
    cascade rung is stored per transaction rather than inferred. When
    ``classified_by`` is ``llm`` the prompt version is populated, and that is
    what allows a prompt change to reprocess only model-derived rows while
    leaving a person's own decisions alone.
    """

    statement_id: uuid.UUID
    page: int | None = None
    line: int | None = None
    classified_by: ClassifiedByLiteral | None = None
    rule_id: uuid.UUID | None = None
    prompt_version: str | None = None


class TransactionResponse(Schema):
    id: uuid.UUID
    posted_on: date
    description: str
    amount: MoneyStr
    currency: str
    category_id: uuid.UUID | None = None
    merchant_id: uuid.UUID | None = None
    card_id: uuid.UUID | None = None
    provenance: Provenance | None = None
    flags: list[str] = []


class OverrideRequest(Schema):
    category_id: uuid.UUID


class OverrideResponse(Schema):
    """Both totals, echoed deliberately.

    Reclassification moves money between categories and never changes a
    statement total. Returning both makes the invariant visible in the response
    instead of asking the client to trust it.
    """

    transaction_id: uuid.UUID
    category_totals: dict[str, MoneyStr]
    statement_total: MoneyStr
