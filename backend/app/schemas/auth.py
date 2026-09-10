"""Authentication payloads. Screens 00 and 01."""

from __future__ import annotations

import uuid

from pydantic import EmailStr, Field, SecretStr

from app.schemas.common import Schema


class RegisterRequest(Schema):
    email: EmailStr
    #: Twelve is the floor, and the boundary is inclusive (TC-AUTH-002, 003).
    #: No composition rules: length beats character-class theatre, and rules
    #: that annoy people produce predictable passwords.
    password: SecretStr = Field(min_length=12)
    name: str | None = Field(default=None, max_length=200)


class LoginRequest(Schema):
    email: EmailStr
    password: SecretStr


class PasswordResetRequest(Schema):
    email: EmailStr


class PasswordResetConfirm(Schema):
    token: str
    new_password: SecretStr = Field(min_length=12)


class MeResponse(Schema):
    id: uuid.UUID
    email: EmailStr
    name: str | None = None
    #: Gates the locked navigation on screen 02. Flips on the first successful
    #: **commit**, not the first upload: an upload that fails to reconcile
    #: leaves the ledger empty, and unlocking a dashboard that would show
    #: nothing is worse than leaving it locked (TC-AUTH-012).
    #:
    #: The client reads this rather than fetching statements and checking for
    #: an empty array, because those two answers differ during an import.
    has_statements: bool
