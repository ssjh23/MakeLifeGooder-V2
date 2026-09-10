"""Redaction, enforced at emit time by an allow-list.

An unrecognised field is dropped. That direction matters: a deny-list fails
open, so the first time someone logs a new field carrying a merchant name it
ships to the log backend and nobody notices. An allow-list fails closed, so the
same mistake shows up as a missing field in a dashboard, which somebody chases.

Why merchant descriptors are the hard case. A descriptor can be an individual's
name, because a person-to-person transfer looks like any other line on the
statement. The privacy criterion forbids storing those as merchants; logging one
would breach the same rule by a different route. So the raw string never leaves,
and a salted hash goes in its place, which still correlates the same merchant
across lines without being readable.

Presigned URLs are the other one worth naming. They are bearer credentials. A
logged presigned URL is a leaked file.

This module is written rather than stubbed. A redactor that is absent, partial
or permissive is a privacy defect that looks finished, and unlike the business
logic there is no version of it that is instructive to get wrong. The parts
that are genuinely yours are the *policy* choices, marked below: which fields
belong in the allow-list, and how much of the hash to keep.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Final

#: Fields permitted in a log event. Anything absent is dropped.
#:
#: POLICY: this set is the contract. Widening it is a deliberate act, and the
#: question to ask of a candidate is not "is it useful?" but "could this field
#: ever hold a person's name, an amount, or a credential?"
ALLOWED: Final[frozenset[str]] = frozenset(
    {
        # structlog and stdlib machinery
        "event",
        "level",
        "logger",
        "timestamp",
        "ts",
        "exc_info",
        "exception",
        # Correlation
        "request_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "job_id",
        "statement_id",
        "user_id",
        "card_id",
        "category_id",
        "merchant_id",
        "rule_id",
        "transaction_id",
        "stage",
        # Safe derivations of unsafe values
        "descriptor_key_hash",
        # Counts and booleans, never the values behind them
        "row_count",
        "merchant_count",
        "unresolved_count",
        "batch_size",
        "changed",
        "overrides_preserved",
        "overrides_discarded",
        "duplicates_removed",
        "statements_reassigned",
        "objects_deleted",
        "rows_deleted",
        "reconciled",
        "difference_nonzero",
        "has_text_layer",
        # Operational
        "parser",
        "prompt_version",
        "rung",
        "classified_by",
        "reason",
        "status",
        "attempt",
        "duration_ms",
        "outcome",
        "http_method",
        "http_path",
        "http_status",
    }
)

#: Never permitted, listed explicitly so the reason survives.
#:
#: The allow-list already excludes these. They are named because the request
#: bodies in the API reference are themselves a source of them: ``/unlock``
#: carries a password, ``/rows`` carries a description and an amount, ``/cards``
#: carries last4. This is also why request-body logging is off by default, and
#: why auto-instrumenting APM was rejected in ADR-014.
DENIED_WITH_REASON: Final[dict[str, str]] = {
    "description_raw": "may contain an individual's name",
    "descriptor_key": "may contain an individual's name",
    "raw_descriptors": "may contain an individual's name",
    "merchant_name": "may contain an individual's name",
    "amount": "financial data, identifying combined with user_id",
    "amount_minor": "financial data, identifying combined with user_id",
    "total": "financial data",
    "printed_total": "financial data",
    "extracted_total": "financial data",
    "difference": "financial data; log difference_nonzero instead",
    "last4": "identifying",
    "nickname": "identifying",
    "filename": "identifying",
    "email": "personal data",
    "name": "personal data",
    "password": "credential, used once in memory and never persisted",
    "presigned_url": "bearer credential; a logged URL is a leaked file",
    "url": "may be a presigned URL",
    "cookie": "credential",
    "authorization": "credential",
}

#: Length of the retained hash prefix. Long enough that two merchants colliding
#: in one tenant's logs is not a practical concern, short enough that the log
#: line stays readable. POLICY: yours to change.
HASH_PREFIX_LENGTH: Final[int] = 12


def hash_descriptor(descriptor: str, *, salt: str) -> str:
    """Return a salted, truncated hash of a merchant descriptor.

    Keyed rather than plain: an unsalted hash of a short, low-entropy string is
    reversible by anyone willing to hash a merchant list, which would defeat the
    entire point of not logging the descriptor.
    """
    digest = hmac.new(salt.encode("utf-8"), descriptor.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:HASH_PREFIX_LENGTH]


class RedactionProcessor:
    """structlog processor applying :data:`ALLOWED` at emit time.

    Placed last in the processor chain, so it sees the event exactly as it would
    have been written and nothing can add a field afterwards.
    """

    def __init__(self, *, salt: str, allowed: frozenset[str] = ALLOWED) -> None:
        self._salt = salt
        self._allowed = allowed

    def __call__(
        self,
        logger: Any,  # noqa: ANN401 - structlog's processor signature
        method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        # Convert the one unsafe value we have a safe form for, before the
        # allow-list drops it. The caller passes `descriptor` and gets
        # `descriptor_key_hash`; the raw string never reaches the output.
        descriptor = event_dict.pop("descriptor", None)
        if isinstance(descriptor, str) and descriptor:
            event_dict["descriptor_key_hash"] = hash_descriptor(descriptor, salt=self._salt)

        return {key: value for key, value in event_dict.items() if key in self._allowed}
