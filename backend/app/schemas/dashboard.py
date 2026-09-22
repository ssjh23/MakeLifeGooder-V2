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


class CompanyRow(Schema):
    merchant_id: uuid.UUID
    name: str
    total: MoneyStr
    share: float
    transaction_count: int


class CategoryTotal(Schema):
    """A category name and amount, without the ranking fields ``CategoryBand``
    carries -- just enough to say "the largest one was this"."""

    category_id: uuid.UUID
    name: str
    total: MoneyStr


class CategoryBand(Schema):
    category_id: uuid.UUID
    name: str
    total: MoneyStr
    share: float
    #: Versus the immediately preceding month in the requested range.
    #: ``None`` when that month isn't available to compare against (the
    #: range's very first month, or "month" range with only one point).
    change: float | None = None
    #: Row count for the whole range, from ``CategoryMonthlyTotal.txn_count``
    #: -- no second query, the aggregate refresher already counted these.
    rows: int = 0
    #: Top two merchants across the whole displayed range (same scope as
    #: ``total``/``share`` above), for the category card's inline "where the
    #: money went" list (screen 06, band 02). The full ranked list, plus a
    #: six-month trend, lives at screen 06b instead.
    top_merchants: list[CompanyRow] = []
    other_merchants_count: int = 0
    other_merchants_total: MoneyStr | None = None


class CardBand(Schema):
    card_id: uuid.UUID
    nickname: str
    colour: str | None = None
    total: MoneyStr
    rows: int = 0
    largest_category: CategoryTotal | None = None
    #: Mirrors ``MonthBand.by_category``'s shape, scoped to this card instead
    #: of a month -- what screen 06's "by card" split bar (band 03) segments.
    by_category: dict[str, MoneyStr] = {}


class DashboardResponse(Schema):
    banner: UnclassifiedBanner
    months: list[MonthBand]
    categories: list[CategoryBand]
    cards: list[CardBand]
    range: RangeLiteral


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
    #: The rule's own pattern, so screen 06c can show *what matched*
    #: ("rule SQ* → Travel"), not just that a rule was involved. ``None``
    #: whenever ``rule_id`` is, and also if the rule was since deleted.
    rule_pattern: str | None = None
    prompt_version: str | None = None


class TransactionResponse(Schema):
    id: uuid.UUID
    posted_on: date
    description: str
    amount: MoneyStr
    currency: str
    category_id: uuid.UUID | None = None
    merchant_id: uuid.UUID | None = None
    #: Denormalised onto the response so the individual-transactions table
    #: (screen 06, band 05) has a "Company" column without a client-side
    #: join against ``/dashboard/categories/{id}`` or a second request.
    merchant_name: str | None = None
    #: The cascade's join key. Screen 06c uses it to offer "every row from
    #: this company" as an override scope distinct from "this row only".
    descriptor_key: str | None = None
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
