"""Card payloads. Screens 02b to 02d.

A card here is a label. This module is the complete set of fields the system
will accept about one, and that is the point: there is no field for a full
number, an expiry, a CVV, a PIN or a bank credential, so there is nowhere for
one to be stored even by mistake (TC-CARD-002, TC-CARD-003).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import Field

from app.schemas.common import CurrencyCode, Last4, Schema

CardStatusLiteral = Literal["active", "closed", "replaced", "archived"]


class CardCreate(Schema):
    nickname: str = Field(max_length=100)
    institution: str = Field(max_length=100)
    type: Literal["credit", "current", "savings"] = "credit"
    #: The only fragment of a card number the system stores, and the match key
    #: for tagging a statement to a card.
    last4: Last4
    #: With last4, this is what lets an uploaded PDF find its own card.
    statement_day: int | None = Field(default=None, ge=1, le=28)
    currency: CurrencyCode = "SGD"
    colour: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CardUpdate(Schema):
    """Rename and recolour only. Never touches statements (TC-CARD-005)."""

    nickname: str | None = Field(default=None, max_length=100)
    colour: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CardResponse(Schema):
    id: uuid.UUID
    nickname: str
    institution: str
    last4: Last4
    network: str | None = None
    statement_day: int | None = None
    currency: CurrencyCode
    colour: str | None = None
    status: CardStatusLiteral
    expires_on: date | None = None
    statement_count: int
    archived: bool


class StatementDisposition(Schema):
    """Where one statement goes when its card is archived.

    Every statement needs a destination and the archive is refused if one is
    missing, so a card cannot be archived halfway (TC-CARD-007).
    """

    statement_id: uuid.UUID
    action: Literal["keep", "reassign"]
    target_card_id: uuid.UUID | None = None


class ArchivePreview(Schema):
    """What archiving would do, computed by the server.

    Screen 02d states the effect before committing. Computing it in the client
    would duplicate the server's logic and eventually disagree with it, so the
    preview and the action share one implementation.
    """

    statements: list[uuid.UUID]
    totals_that_move: int
    totals_unchanged: int


class ArchiveRequest(Schema):
    statement_dispositions: list[StatementDisposition] = Field(default_factory=list)


class DeleteStatementsRequest(Schema):
    """Destructive and not recoverable, unlike archiving.

    The explicit flag exists so the irreversible path cannot be reached by a
    request that merely forgot a field (TC-CARD-010).
    """

    confirm: bool
