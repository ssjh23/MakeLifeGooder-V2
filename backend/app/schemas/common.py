"""Shared response shapes.

These types generate the OpenAPI document, which generates the TypeScript
client. A field renamed here becomes a compile error in the frontend rather
than a runtime surprise, which is the payoff for having chosen two languages
(ADR-001, ADR-011).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Money crosses the wire as a decimal string, always paired with a currency.
#: Never a float: a JSON number would reintroduce binary floating point at the
#: boundary and undo the integer arithmetic the reconciler depends on.
MoneyStr = Annotated[str, Field(pattern=r"^-?\d+\.\d{2}$", examples=["1234.56"])]

CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, examples=["SGD"])]

Last4 = Annotated[str, Field(pattern=r"^\d{4}$", examples=["4429"])]


class Schema(BaseModel):
    # extra="forbid": a request carrying a field the schema does not declare
    # is rejected (400) rather than silently dropped. CardCreate is the
    # sharpest case -- there is no field anywhere for a full number, an
    # expiry or a CVV, so accepting one and ignoring it would look like
    # success to the caller while storing nothing (TC-CARD-002, TC-CARD-003).
    # Harmless for response models built with model_validate(some_orm_row):
    # from_attributes reads only the fields it knows about, so this only
    # ever bites untrusted request bodies, which is the point.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")


class ErrorDetail(Schema):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(Schema):
    """The envelope every failure uses."""

    error: ErrorDetail


class Period(Schema):
    from_: date = Field(alias="from")
    to: date


class Page(Schema):
    """Cursor pagination. Offsets drift when rows are inserted mid-scroll."""

    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class Money(Schema):
    amount: MoneyStr
    currency: CurrencyCode


class Reconciliation(Schema):
    """Returned by every row mutation during import.

    Carried on the mutation response rather than requiring a second request, so
    screen 03b's countdown updates from the response the user's keystroke
    already produced.
    """

    extracted_total: MoneyStr
    printed_total: MoneyStr
    difference: MoneyStr
    currency: CurrencyCode
    reconciled: bool


class Conflict(Schema):
    """The 409 payload.

    One shape for a duplicate statement (03d) and a rule collision (07b, 07d),
    so a single client component renders both. Sharing it is a standing check
    that the two stay aligned.
    """

    kind: Literal["duplicate_statement", "rule_collision"]
    existing_id: uuid.UUID
    resolutions: list[str]
    affected_rows: int | None = None
    message: str


class Timestamped(Schema):
    created_at: datetime
