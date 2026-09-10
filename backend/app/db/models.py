"""The data model.

Follows the DB Schema Diagram page. Two tables here are *not* on that page and
are marked below: ``rules`` and ``exports``. Both are required by endpoints and
screens that are specified (07 to 07e, 08b) and by test cases that are written
(TC-RULE-*, TC-ACCT-002 to 006), so leaving them out would mean a schema that
cannot serve its own API. Worth reconciling in Notion rather than leaving as a
quiet divergence.

Conventions that carry weight:

  * Amounts are ``amount_minor``: signed integers in minor units. Never a float.
  * ``transactions.user_id`` is denormalised, so isolation and the hot indexes
    need no join.
  * ``description_raw`` is written once and never edited. ``descriptor_key`` is
    the normalised join key for the whole cascade.
  * A null ``category_id`` means genuinely unclassified, not "other".
  * ``audit_log.actor_user_id`` deliberately carries no foreign key, so records
    survive the deletion of the account they describe.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ---------------------------------------------------------------------------
# Enumerations. Stored as VARCHAR with a CHECK constraint rather than a native
# Postgres enum: adding a value to a native enum is a migration that cannot run
# inside a transaction, which is a poor trade for a fixed vocabulary.
# ---------------------------------------------------------------------------


class AccountKind(StrEnum):
    CREDIT = "credit"
    CURRENT = "current"
    SAVINGS = "savings"


class CardStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    REPLACED = "replaced"
    ARCHIVED = "archived"


class CardKind(StrEnum):
    PRIMARY = "primary"
    SUPPLEMENTARY = "supplementary"
    VIRTUAL = "virtual"


class CardSource(StrEnum):
    """Provenance of a statement's card tag, so weak matches can be re-prompted
    without re-asking about confident ones."""

    USER = "user"
    DETECTED = "detected"
    INHERITED = "inherited"


class StatementStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    FAILED = "failed"


class FailureReason(StrEnum):
    PASSWORD_PROTECTED = "password_protected"
    NO_TEXT_LAYER = "no_text_layer"
    NOT_A_STATEMENT = "not_a_statement"
    PARSER_ERROR = "parser_error"


class JobStage(StrEnum):
    EXTRACT = "extract"
    CLASSIFY = "classify"
    AGGREGATE = "aggregate"
    PURGE = "purge"
    REAPPLY = "reapply"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ClassifiedBy(StrEnum):
    """The cascade rungs, in cost order.

    Stored per transaction so that a prompt change can reprocess only the rows
    a model produced, leaving overrides and aliases untouched.
    """

    OVERRIDE = "override"
    ALIAS = "alias"
    MERCHANT_DEFAULT = "merchant_default"
    LLM = "llm"


class CategoryKind(StrEnum):
    """Without this a credit card payment counts twice and doubles the total."""

    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class AliasSource(StrEnum):
    RULE = "rule"
    LLM = "llm"
    MANUAL = "manual"


class MatchType(StrEnum):
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    EXACT = "exact"


class RuleScope(StrEnum):
    FUTURE = "future"
    BACKFILL = "backfill"


class ExportStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    #: citext, so casing cannot create two accounts for one person.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    #: Null for OIDC-only accounts; set for password accounts.
    password_hash: Mapped[str | None] = mapped_column(Text)
    #: Subject id from the identity provider. Keeps auth swappable without
    #: migrating user data (ADR-008).
    auth_provider_id: Mapped[str | None] = mapped_column(Text, index=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Selects which parser strategy extraction uses.
    institution: Mapped[str] = mapped_column(Text, nullable=False)
    #: Determines the sign convention for amounts and whether a closing balance
    #: is meaningful.
    account_kind: Mapped[AccountKind] = mapped_column(
        _enum(AccountKind, "account_kind"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'SGD'"))
    nickname: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Card(Base):
    """A label, not a payment instrument.

    Last four digits are the only fragment of a card number stored anywhere.
    No full number, expiry, CVV, PIN or bank credential is accepted by any
    endpoint (TC-CARD-002, TC-CARD-003).
    """

    __tablename__ = "cards"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    nickname: Mapped[str] = mapped_column(Text, nullable=False)
    network: Mapped[str | None] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    card_kind: Mapped[CardKind] = mapped_column(
        _enum(CardKind, "card_kind"), nullable=False, server_default=text("'primary'")
    )
    statement_day: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'SGD'"))
    expires_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[CardStatus] = mapped_column(
        _enum(CardStatus, "card_status"), nullable=False, server_default=text("'active'")
    )
    #: Reissue lineage, so history does not fracture when a card is replaced.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cards.id", ondelete="SET NULL")
    )
    colour_hex: Mapped[str | None] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("last4 ~ '^[0-9]{4}$'", name="last4_is_four_digits"),
        CheckConstraint(
            "statement_day IS NULL OR (statement_day BETWEEN 1 AND 28)",
            name="statement_day_in_range",
        ),
        # Scoped to active cards: a replaced card legitimately shares its last4
        # with the card that replaced it.
        Index(
            "uq_cards_user_id_last4_active",
            "user_id",
            "last4",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class Statement(Base):
    __tablename__ = "statements"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    #: Nullable until resolved. Becomes the default for its transactions.
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cards.id", ondelete="SET NULL")
    )
    #: Pointer into object storage. The file itself never enters the database.
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: Upload idempotency. Re-uploading the same document is a no-op.
    file_sha256: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    detected_last4: Mapped[str | None] = mapped_column(String(4))
    card_source: Mapped[CardSource | None] = mapped_column(_enum(CardSource, "card_source"))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    printed_total_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'SGD'"))
    status: Mapped[StatementStatus] = mapped_column(
        _enum(StatementStatus, "statement_status"),
        nullable=False,
        server_default=text("'pending'"),
    )
    failure_reason: Mapped[FailureReason | None] = mapped_column(
        _enum(FailureReason, "failure_reason")
    )
    #: Permanent mark that this statement was committed with a stated gap. The
    #: gap is then carried in every view of that month.
    reconciled: Mapped[bool | None] = mapped_column(Boolean)
    gap_reason: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str | None] = mapped_column(Text)
    has_text_layer: Mapped[bool | None] = mapped_column(Boolean)
    #: Exposed to the client so a stuck statement can be traced without
    #: database access.
    trace_id: Mapped[str | None] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "file_sha256", name="uq_statements_user_id_file_sha256"),
        # One statement per card per period. Enforced by the database, because
        # TC-DUP-007 bypasses the service layer and inserts directly.
        Index(
            "uq_statements_card_id_period",
            "card_id",
            "period_start",
            "period_end",
            unique=True,
            postgresql_where=text("card_id IS NOT NULL AND period_start IS NOT NULL"),
        ),
    )


class ProcessingJob(Base):
    """Stage tracking, so a retry resumes at the failed stage rather than
    re-parsing the PDF."""

    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[JobStage] = mapped_column(_enum(JobStage, "job_stage"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), nullable=False, server_default=text("'queued'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    #: W3C traceparent, so the worker reopens the span the API started and the
    #: trace survives the async hop (ADR-014).
    traceparent: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    #: Denormalised so isolation and the hot indexes need no join.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cards.id", ondelete="SET NULL")
    )
    #: Beats the statement-level tag when present.
    row_last4: Mapped[str | None] = mapped_column(String(4))
    posted_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: Signed minor units. Never a float.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'SGD'"))
    #: Immutable source text. Written once, never edited.
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    #: Normalised merchant string. The join key for the entire cascade.
    descriptor_key: Mapped[str | None] = mapped_column(Text, index=True)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("merchants.id", ondelete="SET NULL")
    )
    #: Null means genuinely unclassified, not "other".
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    classified_by: Mapped[ClassifiedBy | None] = mapped_column(_enum(ClassifiedBy, "classified_by"))
    prompt_version: Mapped[str | None] = mapped_column(Text)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("rules.id", ondelete="SET NULL")
    )
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Catches the same transaction arriving in two overlapping statements.
    dedupe_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    #: A removed duplicate leaves the counted total but stays on the statement
    #: record, and the removal is undoable.
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page: Mapped[int | None] = mapped_column(Integer)
    line: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_hash", name="uq_transactions_user_id_dedupe_hash"),
        Index("ix_transactions_user_id_posted_on", "user_id", "posted_on"),
        Index("ix_transactions_user_id_category_id", "user_id", "category_id"),
        Index("ix_transactions_statement_id", "statement_id"),
    )


# ---------------------------------------------------------------------------
# Merchants and categories
# ---------------------------------------------------------------------------


class Merchant(Base):
    """Cross-tenant. Shared identity is what lets the alias cache warm once for
    everybody, so the LLM bill tracks distinct new merchants rather than
    transaction volume."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    default_category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class MerchantAlias(Base):
    """The messy real-world string to canonical merchant mapping.

    ``descriptor_key`` is globally unique, which is what makes classification a
    single indexed read rather than a search.
    """

    __tablename__ = "merchant_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    descriptor_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: Lets a bad batch be purged without touching verified aliases.
    source: Mapped[AliasSource] = mapped_column(_enum(AliasSource, "alias_source"), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class MerchantOverride(Base):
    """A user's own ruling. Top of the cascade; beats everything else."""

    __tablename__ = "merchant_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    descriptor_key: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("merchants.id", ondelete="SET NULL")
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "descriptor_key", name="uq_merchant_overrides_user_id_descriptor_key"
        ),
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    #: Two-level taxonomy by adjacency list.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    #: Null marks a system category, visible to everyone.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    #: Stable machine name, safe to reference in code unlike the display name.
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[CategoryKind] = mapped_column(
        _enum(CategoryKind, "category_kind"), nullable=False, server_default=text("'expense'")
    )
    colour_hex: Mapped[str | None] = mapped_column(String(7))
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        Index(
            "uq_categories_user_id_slug",
            "user_id",
            "slug",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_categories_system_slug",
            "slug",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
    )


class Rule(Base):
    """Pattern to category mapping.

    NOT ON THE DB SCHEMA PAGE. Required by screens 07 to 07e, the ``/rules``
    endpoints and TC-RULE-001 to 014. Recorded here rather than invented
    silently; the schema page should gain it.
    """

    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    match_type: Mapped[MatchType] = mapped_column(_enum(MatchType, "match_type"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    ignore_case: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    match_negative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    scope: Mapped[RuleScope] = mapped_column(
        _enum(RuleScope, "rule_scope"), nullable=False, server_default=text("'future'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "user_id", "pattern", "match_type", name="uq_rules_user_id_pattern_match_type"
        ),
    )


# ---------------------------------------------------------------------------
# Reporting and evidence
# ---------------------------------------------------------------------------


class CategoryMonthlyTotal(Base):
    """Precomputed. The dashboard reads this and never aggregates per request."""

    __tablename__ = "category_monthly_totals"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    card_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    #: Truncated to the first of the month.
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Enables averages and outlier detection without a second query.
    txn_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Export(Base):
    """NOT ON THE DB SCHEMA PAGE. Required by ``/exports`` and TC-ACCT-002 to
    006, which state that columns and row counts are declared before the file
    is written. That declaration has to be stored somewhere."""

    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[ExportStatus] = mapped_column(
        _enum(ExportStatus, "export_status"), nullable=False, server_default=text("'pending'")
    )
    object_key: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class AuditLog(Base):
    """Evidence.

    ``actor_user_id`` deliberately carries no foreign key. A record that
    vanishes when the account it describes is deleted is not evidence, and the
    moment it matters most is after a deletion.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


#: Tables carrying a ``user_id`` that scopes them to one tenant. The migration
#: enables row level security on exactly these, and a test asserts the list
#: matches the schema so a new tenant table cannot be added without a policy.
TENANT_TABLES: tuple[str, ...] = (
    "accounts",
    "cards",
    "statements",
    "processing_jobs",
    "transactions",
    "merchant_overrides",
    "rules",
    "category_monthly_totals",
    "exports",
)

#: Readable by every tenant. The shared alias cache is deliberate: it is what
#: makes the tenth Singapore user shopping at FairPrice cost nothing.
SHARED_TABLES: tuple[str, ...] = ("merchants", "merchant_aliases")
