"""Rules payloads. Screens 07 to 07e.

Every confirmed category becomes a rule. Specific patterns beat broad ones, and
a conflict is never resolved silently.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import Field

from app.schemas.common import MoneyStr, Schema

MatchTypeLiteral = Literal["contains", "starts_with", "ends_with", "exact"]


class RuleOptions(Schema):
    ignore_case: bool = True
    match_negative: bool = False


class RulePreviewRequest(Schema):
    """POST despite being read-only.

    Patterns contain characters that are awkward in a query string, and a
    preview that mangles the pattern would report matches for something other
    than the rule about to be created.
    """

    pattern: str = Field(min_length=1, max_length=200)
    match_type: MatchTypeLiteral
    options: RuleOptions = RuleOptions()


class RuleConflictSummary(Schema):
    rule_id: uuid.UUID
    pattern: str
    overlap_rows: int


class RulePreview(Schema):
    matches: int
    already_in_category: int
    #: The number that stops somebody silently destroying their own work.
    #: Shown while typing, before anything is saved.
    would_relabel_manual: int
    conflicts: list[RuleConflictSummary] = []


class RuleCreate(Schema):
    pattern: str = Field(min_length=1, max_length=200)
    match_type: MatchTypeLiteral
    category_id: uuid.UUID
    scope: Literal["future", "backfill"] = "future"
    options: RuleOptions = RuleOptions()


class RuleUpdate(Schema):
    pattern: str | None = Field(default=None, min_length=1, max_length=200)
    match_type: MatchTypeLiteral | None = None
    category_id: uuid.UUID | None = None
    options: RuleOptions | None = None


class RuleResponse(Schema):
    id: uuid.UUID
    pattern: str
    match_type: MatchTypeLiteral
    category_id: uuid.UUID
    category_name: str
    rows_matched: int
    options: RuleOptions


class ResolveConflictRequest(Schema):
    action: Literal["specific_wins", "narrow_broader", "delete_specific"]
    apply_to_history: bool = False


class ReapplyPreviewRequest(Schema):
    #: Omit for every imported month.
    months: list[str] | None = None


class MonthReapplyPreview(Schema):
    month: date
    rows_retested: int
    rows_changing: int
    effect_on_totals: MoneyStr


class ReapplyPreview(Schema):
    """A preview screen with a rerun button, not a button that reruns.

    Preview and execution are separate endpoints because a rerun can overwrite
    decisions a person made by hand, and ``overrides_at_risk`` is the number
    they need before choosing.
    """

    per_month: list[MonthReapplyPreview]
    overrides_at_risk: int


class ReapplyRequest(Schema):
    #: False discards manual overrides irrecoverably, so it is never the
    #: default. The preview states how many are at risk before this is sent.
    keep_overrides: bool = True
    months: list[str] | None = None
