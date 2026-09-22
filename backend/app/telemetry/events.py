"""Event names and pipeline stages.

One event per stage boundary. Dot-namespaced, so a log backend can filter by
prefix. The names are fixed here rather than written inline at call sites, so
the set stays closed and a typo is an import error instead of a gap in a
filter.

Three questions have to be answerable from logs alone (ADR-014):

    which request is this?      filter on trace_id
    where in the flow is it?    the last event for that trace_id, read stage
    what happened?              the ordered events for that trace_id

That only works if every boundary emits, so adding a stage means adding its
events here first.
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """Pipeline position. Written to every event as ``stage``."""

    UPLOAD = "upload"
    EXTRACT = "extract"
    CLASSIFY = "classify"
    AGGREGATE = "aggregate"
    REVIEW = "review"
    COMMIT = "commit"


# -- Authentication ---------------------------------------------------------
AUTH_REGISTER_SUCCEEDED = "auth.register.succeeded"
AUTH_LOGIN_SUCCEEDED = "auth.login.succeeded"
AUTH_LOGIN_FAILED = "auth.login.failed"

# -- Cards ------------------------------------------------------------------
CARD_ARCHIVED = "card.archived"
CARD_STATEMENTS_DELETED = "card.statements_deleted"

# -- Import -----------------------------------------------------------------
STATEMENT_UPLOAD_REGISTERED = "statement.upload.registered"
DUPLICATE_FILE_DETECTED = "duplicate.file.detected"
STATEMENT_EXTRACT_STARTED = "statement.extract.started"
STATEMENT_EXTRACT_SUCCEEDED = "statement.extract.succeeded"
STATEMENT_EXTRACT_FAILED = "statement.extract.failed"
STATEMENT_RECONCILE_CHECKED = "statement.reconcile.checked"
STATEMENT_COMMIT_SUCCEEDED = "statement.commit.succeeded"
STATEMENT_DELETED = "statement.deleted"

# -- Classification ---------------------------------------------------------
STATEMENT_CLASSIFY_STARTED = "statement.classify.started"
STATEMENT_CLASSIFY_SUCCEEDED = "statement.classify.succeeded"
MERCHANT_CLASSIFY_RESOLVED = "merchant.classify.resolved"
LLM_BATCH_DISPATCHED = "llm.batch.dispatched"
LLM_UNAVAILABLE = "llm.unavailable"

# -- Aggregate ----------------------------------------------------------------
STATEMENT_AGGREGATE_SUCCEEDED = "statement.aggregate.succeeded"

# -- Review -----------------------------------------------------------------
STATEMENT_REVIEW_FINISHED = "statement.review.finished"
DUPLICATE_REMOVED = "duplicate.removed"
DUPLICATE_RESTORED = "duplicate.restored"
DESCRIPTOR_SPLIT = "descriptor.split"
DESCRIPTOR_MERGED = "descriptor.merged"

# -- Dashboard and rules ----------------------------------------------------
TRANSACTION_OVERRIDDEN = "transaction.overridden"
RULE_CREATED = "rule.created"
RULE_CONFLICT_DETECTED = "rule.conflict.detected"
RULES_REAPPLY_COMPLETED = "rules.reapply.completed"

# -- Jobs -------------------------------------------------------------------
JOB_RETRY_SCHEDULED = "job.retry.scheduled"
JOB_DEAD_LETTERED = "job.dead_lettered"

# -- Account ----------------------------------------------------------------
ACCOUNT_DELETE_REQUESTED = "account.delete.requested"
ACCOUNT_DELETE_COMPLETED = "account.delete.completed"
