"""Review payloads. Screens 04, 04b, 04c and 05.

Grouped by merchant, not by row. One decision per merchant is the core
interaction of the product, and it is what makes each month cheaper than the
last.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import MoneyStr, Schema
from app.schemas.rules import MatchTypeLiteral, RuleOptions

ClassifiedByLiteral = Literal["override", "alias", "merchant_default", "llm"]
MerchantStatusLiteral = Literal["rule_matched", "overridden", "new"]


class SuggestedCategory(Schema):
    id: uuid.UUID
    name: str


class MerchantGroup(Schema):
    descriptor_key: str
    display_name: str
    #: Shown to the user as the evidence behind a grouping. Exposing the raw
    #: strings is what makes the normaliser legible rather than magic: a person
    #: can see that two lines were treated as one merchant and disagree.
    #:
    #: Never written to a log. A descriptor can be an individual's name, and
    #: the privacy rule that forbids storing those as merchants is breached
    #: just as thoroughly by logging one (ADR-014).
    raw_descriptors: list[str]
    row_count: int
    total: MoneyStr
    status: MerchantStatusLiteral
    suggested_category: SuggestedCategory | None = None
    classified_by: ClassifiedByLiteral | None = None
    #: Set together, only when status == "rule_matched" -- which standing
    #: rule decided this group's category, so the board can link to it
    #: instead of just naming the fact.
    rule_id: uuid.UUID | None = None
    rule_pattern: str | None = None


class ReviewFooter(Schema):
    """Always reconciles back to the statement.

    ``classified`` plus ``unassigned`` equals ``statement_total``. The footer is
    present on every review screen so the sum a user is working towards is
    never off-screen.
    """

    statement_total: MoneyStr
    classified: MoneyStr
    unassigned: MoneyStr
    can_finish: bool


class ReviewFilters(Schema):
    needs_category: int
    new_merchants: int
    overridden: int
    duplicates: int


class ReviewBoard(Schema):
    footer: ReviewFooter
    filters: ReviewFilters
    merchants: list[MerchantGroup]


class ClassifyRequest(Schema):
    """One decision, for one merchant.

    Exactly one of ``category_id`` or ``new_category_name``. Accepting both
    would leave the server guessing which the user meant, and accepting neither
    is a request with no content (TC-REV-017).
    """

    category_id: uuid.UUID | None = None
    new_category_name: str | None = Field(default=None, max_length=100)
    #: False is the "skip for now" path: the rows are categorised, no rule is
    #: created, and next month asks again.
    create_rule: bool = True
    #: ``backfill`` restates history, and the dialog states how many past rows
    #: change before it happens.
    scope: Literal["future", "backfill"] = "future"
    #: The same rule-authoring options the Rules screen's "New rule" form
    #: offers (BUILD STEP 8.1), so a person isn't limited to an exact match
    #: on this one descriptor just because they're creating the rule from
    #: here instead of there. ``pattern`` defaults to the descriptor_key
    #: being classified when omitted -- today's only behaviour, preserved.
    pattern: str | None = Field(default=None, min_length=1, max_length=200)
    match_type: MatchTypeLiteral = "exact"
    options: RuleOptions = RuleOptions()

    @model_validator(mode="after")
    def _exactly_one_category(self) -> ClassifyRequest:
        if bool(self.category_id) == bool(self.new_category_name):
            raise ValueError("Provide exactly one of category_id or new_category_name.")
        return self


class SplitDescriptorRequest(Schema):
    """One exact raw line, picked out of a merchant group's evidence list."""

    description_raw: str = Field(min_length=1)


class MergeDescriptorRequest(Schema):
    """Fold the named merchant group into an existing one -- the inverse of
    a split, for a normaliser under-merge the split flow can't fix."""

    target_descriptor_key: str = Field(min_length=1)


class ConfirmAllRequest(Schema):
    """Bulk confirm. Touches only rule-matched merchants.

    Omit ``descriptor_keys`` to confirm every rule-matched merchant. An
    unclassified merchant is never swept up by this: confirming a decision
    nobody made is how a bulk action destroys trust (TC-REV-018).
    """

    descriptor_keys: list[str] | None = None


class DuplicatePair(Schema):
    """Same merchant, same amount, within a day.

    Flagged rather than removed, because a genuine repeat purchase looks
    identical and only the person who made it knows which it was.
    """

    pair_id: uuid.UUID
    row_ids: list[uuid.UUID]
    merchant: str
    amount: MoneyStr
    hours_apart: float


class ResolveDuplicateRequest(Schema):
    action: Literal["keep_both", "remove"]
    remove_row_id: uuid.UUID | None = None


class ImportSummary(Schema):
    """Screen 04c. A receipt, not a confirmation step.

    The work is already done when this is shown; it exists so a person can see
    what happened to their money rather than being asked to approve it again.
    """

    statement_id: uuid.UUID
    rows_imported: int
    duplicates_removed: int
    counted: int
    classified_by_rule: int
    classified_by_user: int
    unclassified: int
