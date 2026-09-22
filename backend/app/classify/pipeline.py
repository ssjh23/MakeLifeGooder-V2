"""Classification pipeline: normalise, resolve, write.

BUILD STEP 5.5's actual work. ``app/worker/tasks.py`` stays thin -- it opens
the tenant session and wraps this in telemetry -- so this function is what a
test calls directly, with an injected :class:`~app.classify.cascade.CascadeResolver`,
no queue involved.

Runs over the statement's *distinct* descriptors, never over the rows one at a
time: a statement with forty McDonald's lines is one decision the cascade has
to make, not forty.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.classify.cascade import CascadeResolver
from app.classify.normalise import normalise
from app.classify.rules import matches_row
from app.db.models import ClassifiedBy, Rule, Transaction
from app.db.repositories import (
    DescriptorKeyOverrideRepository,
    RuleRepository,
    TransactionRepository,
)
from app.telemetry import events, get_logger

logger = get_logger(__name__)


async def classify(
    session: AsyncSession, *, statement_id: uuid.UUID, cascade: CascadeResolver
) -> None:
    """Normalise every row's descriptor, resolve the distinct set, write back.

    A descriptor the cascade could not resolve (excluded as a transfer,
    declined by the model, or lost to a provider outage) simply keeps
    ``category_id`` and ``classified_by`` unset -- genuinely unclassified,
    not a failure of this stage. There is nothing here to retry it into:
    review (BUILD STEP 6.x) is where a person answers what the cascade could
    not.

    A standing rule is then checked against every row -- per row, not per
    distinct descriptor, since two rows can share a descriptor_key but carry
    different description_raw (two different "SMP*..." lines both collapsing
    to "smp") -- and wins over an automated cascade guess (alias,
    merchant_default, llm) or no resolution at all: there is no pre-existing
    classification here to protect, unlike the retroactive reapply path,
    which already gates on its own backfill/only_unclassified flag. It never
    wins over an override resolution, which stays the most specific
    expression of intent there is. Only category_id and rule_id move for a
    rule match, exactly as
    :meth:`~app.services.rules.RuleService._apply_rule` -- a rule credits
    itself through rule_id, never by inventing a rung the cascade never
    produces.
    """
    transaction_repo = TransactionRepository(session)
    overrides_repo = DescriptorKeyOverrideRepository(session)
    rules_repo = RuleRepository(session)
    rows = await transaction_repo.list_for_statement(statement_id)

    raw_texts = {row.description_raw for row in rows}
    overrides = await overrides_repo.find_many(raw_texts)

    for row in rows:
        row.descriptor_key = overrides.get(row.description_raw) or normalise(row.description_raw)

    distinct_keys = sorted({row.descriptor_key for row in rows if row.descriptor_key})
    resolutions = await cascade.resolve_many(distinct_keys)

    # Fetched once for the whole statement, matched in pure Python per row
    # below -- the same shape ReviewService._group_status() already uses.
    rules = await rules_repo.all_for_user()

    now = datetime.now(UTC)
    resolved_count = 0
    rule_resolved_count = 0
    for row in rows:
        resolution = resolutions.get(row.descriptor_key) if row.descriptor_key else None
        if resolution is not None:
            row.merchant_id = resolution.merchant_id
            row.category_id = resolution.category_id
            row.classified_by = ClassifiedBy(resolution.rung)
            row.confidence = resolution.confidence
            row.prompt_version = resolution.prompt_version
            row.classified_at = now
            resolved_count += 1

            if resolution.rung == ClassifiedBy.OVERRIDE.value:
                continue  # a person's own ruling beats everything, always

        rule = _best_matching_rule(rules, row)
        if rule is not None:
            row.category_id = rule.category_id
            row.rule_id = rule.id
            rule_resolved_count += 1

    await session.flush()

    logger.info(
        events.STATEMENT_CLASSIFY_SUCCEEDED,
        statement_id=str(statement_id),
        descriptor_count=len(distinct_keys),
        row_count=len(rows),
        resolved_row_count=resolved_count,
        rule_resolved_row_count=rule_resolved_count,
    )


def _best_matching_rule(rules: list[Rule], row: Transaction) -> Rule | None:
    """Every rule matching this row, tie-broken the same way
    :meth:`~app.services.rules.RuleService.resolve_conflict` already does
    for ``specific_wins``/``narrow_broader``: the longer pattern wins.
    """
    candidates = [
        rule
        for rule in rules
        if matches_row(rule, descriptor_key=row.descriptor_key, description_raw=row.description_raw)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda rule: len(rule.pattern))
