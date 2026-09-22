"""Rules. Screens 07 to 07e.

===========================================================================
BUILD STEPS 8.1 and 8.2 live in this file.

  8.1 preview, create, conflicts   depends on: 6.3 review classify
  8.2 reapply                      depends on: 7.1 aggregates, 8.1

Verify: uv run pytest tests/integration/test_rules.py
===========================================================================
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aggregate.refresh import AggregateRefresher
from app.api.errors import LedgerError, NotFound, RuleConflict
from app.classify.rules import matches_row
from app.db.models import ClassifiedBy, Rule, Transaction
from app.db.repositories import CategoryRepository, RuleRepository, TransactionRepository
from app.db.session import current_tenant_id
from app.money import DEFAULT_CURRENCY, to_decimal_string
from app.schemas.rules import (
    MonthReapplyPreview,
    ReapplyPreview,
    RuleConflictSummary,
    RuleOptions,
    RulePreview,
    RuleResponse,
)
from app.telemetry import current_traceparent, events, get_logger
from app.worker.queue import enqueue

logger = get_logger(__name__)


def _next_month(month: date) -> date:
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


class RuleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._rules = RuleRepository(session)
        self._categories = CategoryRepository(session)
        self._transactions = TransactionRepository(session)

    # -- Step 8.1 ----------------------------------------------------------

    async def list_rules(self) -> list[RuleResponse]:
        """The tenant's rules with the category name and how many rows each
        currently matches."""
        rules_with_counts = await self._rules.list_for_user()
        responses = []
        for rule, count in rules_with_counts:
            category = await self._categories.get(rule.category_id)
            responses.append(self._response(rule, count, category_name=category.name if category else "Unknown"))
        return responses

    async def preview(self, *, pattern: str, match_type: str, options: RuleOptions) -> RulePreview:
        """Dry-run a pattern while the user types. Changes nothing."""
        matches_count, already_in_category, would_relabel_manual = await self._rules.matching_row_stats(
            pattern=pattern,
            match_type=match_type,
            ignore_case=options.ignore_case,
            match_negative=options.match_negative,
        )
        conflicts = await self._rules.find_conflicts(
            pattern=pattern,
            match_type=match_type,
            ignore_case=options.ignore_case,
            match_negative=options.match_negative,
        )
        return RulePreview(
            matches=matches_count,
            already_in_category=already_in_category,
            would_relabel_manual=would_relabel_manual,
            conflicts=[
                RuleConflictSummary(rule_id=rule.id, pattern=rule.pattern, overlap_rows=overlap)
                for rule, overlap in conflicts
            ],
        )

    async def create(
        self,
        *,
        pattern: str,
        match_type: str,
        category_id: uuid.UUID,
        scope: str,
        options: RuleOptions,
    ) -> RuleResponse:
        """Create a rule.

        Checks for a colliding rule before writing anything: a conflict is
        never settled silently, because two rules disagreeing about the
        same rows is a question only the user can answer.
        """
        category = await self._categories.get(category_id)
        if category is None:
            raise NotFound("Category not found.")

        await self._raise_on_conflict(
            pattern=pattern, match_type=match_type, options=options, exclude_rule_id=None
        )

        rule = await self._rules.create(
            pattern=pattern,
            match_type=match_type,
            category_id=category_id,
            scope=scope,
            ignore_case=options.ignore_case,
            match_negative=options.match_negative,
        )
        logger.info(events.RULE_CREATED, descriptor=pattern, category_id=str(category_id))

        touched = await self._apply_rule(rule, backfill=scope == "backfill")
        return self._response(rule, len(touched), category_name=category.name)

    async def update(self, rule_id: uuid.UUID, **changes: object) -> RuleResponse:
        """Apply the change, re-run the conflict check, and re-apply."""
        rule = await self._require_rule(rule_id)

        pattern = changes.get("pattern")
        if isinstance(pattern, str):
            rule.pattern = pattern
        match_type = changes.get("match_type")
        if isinstance(match_type, str):
            rule.match_type = match_type
        category_id = changes.get("category_id")
        if isinstance(category_id, uuid.UUID):
            rule.category_id = category_id
        options = changes.get("options")
        if isinstance(options, RuleOptions):
            rule.ignore_case = options.ignore_case
            rule.match_negative = options.match_negative
        elif isinstance(options, dict):
            # payload.model_dump(exclude_unset=True) recurses into nested
            # models, so the router hands this method a plain dict here,
            # not the RuleOptions instance create()'s router path passes.
            if "ignore_case" in options:
                rule.ignore_case = options["ignore_case"]
            if "match_negative" in options:
                rule.match_negative = options["match_negative"]
        rule.updated_at = datetime.now(UTC)

        await self._raise_on_conflict(
            pattern=rule.pattern,
            match_type=rule.match_type,
            options=RuleOptions(ignore_case=rule.ignore_case, match_negative=rule.match_negative),
            exclude_rule_id=rule.id,
        )
        await self._session.flush()

        touched = await self._apply_rule(rule, backfill=(rule.scope == "backfill"))
        category = await self._categories.get(rule.category_id)
        return self._response(rule, len(touched), category_name=category.name if category else "Unknown")

    async def delete(self, rule_id: uuid.UUID) -> None:
        """Delete the rule.

        Rows it classified revert to genuinely unclassified rather than
        keeping a category with no rule and no cascade decision behind it --
        stale provenance is worse than a row that visibly needs a look.
        """
        rule = await self._require_rule(rule_id)
        rows = await self._transactions_with_rule(rule.id)
        for row in rows:
            row.category_id = None
            row.rule_id = None
        await self._rules.delete(rule)
        await self._refresh(rows)

    async def conflicts(self) -> list[RuleConflictSummary]:
        """Every flagged overlap, one entry per rule that has one."""
        rules = await self._rules.all_for_user()
        summaries = []
        for rule in rules:
            found = await self._rules.find_conflicts(
                pattern=rule.pattern,
                match_type=rule.match_type,
                ignore_case=rule.ignore_case,
                match_negative=rule.match_negative,
                exclude_rule_id=rule.id,
            )
            if found:
                _other, overlap = found[0]
                summaries.append(RuleConflictSummary(rule_id=rule.id, pattern=rule.pattern, overlap_rows=overlap))
        return summaries

    async def resolve_conflict(self, rule_id: uuid.UUID, *, action: str, apply_to_history: bool) -> None:
        """Settle one flagged overlap.

        Only two overlapping patterns are a defined conflict (CLAUDE.md's own
        open question -- three or more is not attempted). "More specific"
        means the longer pattern: it matches a narrower set of descriptors,
        which is the sense in which ``narrow_broader`` and ``specific_wins``
        produce the same row-level outcome here -- this does not rewrite the
        broader rule's own pattern text, only which rule's category wins on
        the rows the two currently share.
        """
        rule = await self._require_rule(rule_id)
        conflicts = await self._rules.find_conflicts(
            pattern=rule.pattern,
            match_type=rule.match_type,
            ignore_case=rule.ignore_case,
            match_negative=rule.match_negative,
            exclude_rule_id=rule.id,
        )
        if not conflicts:
            return
        other, _overlap = conflicts[0]
        specific = rule if len(rule.pattern) >= len(other.pattern) else other

        if action == "delete_specific":
            await self.delete(specific.id)
            return
        if action in ("specific_wins", "narrow_broader"):
            if apply_to_history:
                await self._apply_rule(specific, backfill=True)
            return
        raise LedgerError(f"Unknown action: {action!r}")

    async def apply_all_rules(
        self, *, months: list[date] | None, keep_overrides: bool
    ) -> tuple[int, int, int]:
        """Re-run every rule across ``months`` (or every imported month).

        Rules apply broadest first, most specific last, so a specific
        pattern's category is the one left standing on a row two rules both
        match -- "specific patterns beat broad ones" (CLAUDE.md).

        Returns ``(rows_changed, overrides_preserved, overrides_discarded)``.
        ``keep_overrides=False`` is irrecoverable: those rows' own decisions
        are gone once this returns, which is why this method exists
        separately from :meth:`_apply_rule`, whose live create/update path
        can never discard one.
        """
        month_dates = sorted({m.replace(day=1) for m in months}) if months else sorted(
            {m.replace(day=1) for m in await self._all_imported_months()}
        )
        rules = sorted(await self._rules.all_for_user(), key=lambda r: len(r.pattern))

        rows_changed = overrides_preserved = overrides_discarded = 0
        touched_months: set[date] = set()

        for rule in rules:
            rows = await self._rules.matching_transactions(
                pattern=rule.pattern,
                match_type=rule.match_type,
                ignore_case=rule.ignore_case,
                match_negative=rule.match_negative,
                keep_overrides=False,  # fetch everything in scope; decide per row below
            )
            for row in rows:
                month = row.posted_on.replace(day=1)
                if month not in month_dates:
                    continue
                if row.classified_by == ClassifiedBy.OVERRIDE:
                    if keep_overrides:
                        overrides_preserved += 1
                        continue
                    overrides_discarded += 1
                row.category_id = rule.category_id
                row.rule_id = rule.id
                rows_changed += 1
                touched_months.add(month)

        await self._session.flush()
        if touched_months:
            await AggregateRefresher(self._session).refresh_months(sorted(touched_months))

        return rows_changed, overrides_preserved, overrides_discarded

    # -- Step 8.2 ------------------------------------------------------------

    async def reapply_preview(self, months: list[str] | None) -> ReapplyPreview:
        """Dry-run every rule across the requested months, or every imported
        month when omitted. Changes nothing."""
        month_dates = (
            [date.fromisoformat(f"{m}-01") for m in months]
            if months
            else await self._all_imported_months()
        )
        rules = await self._rules.all_for_user()

        per_month = []
        overrides_at_risk = 0
        for month in sorted({m.replace(day=1) for m in month_dates}):
            rows = await self._rows_in_month(month)
            retested = changing = 0
            effect_minor = 0
            for row in rows:
                if row.descriptor_key is None:
                    continue
                retested += 1
                matching_rule = self._first_matching_rule(rules, row)
                if matching_rule is None or matching_rule.category_id == row.category_id:
                    continue
                if row.classified_by == ClassifiedBy.OVERRIDE:
                    overrides_at_risk += 1
                    continue
                changing += 1
                effect_minor += abs(row.amount_minor)
            per_month.append(
                MonthReapplyPreview(
                    month=month,
                    rows_retested=retested,
                    rows_changing=changing,
                    effect_on_totals=to_decimal_string(effect_minor, DEFAULT_CURRENCY),
                )
            )

        return ReapplyPreview(per_month=per_month, overrides_at_risk=overrides_at_risk)

    async def reapply(self, *, keep_overrides: bool, months: list[str] | None) -> None:
        """Enqueue the reapply job. It can touch every row a tenant has, so
        it runs as a job, not inline in the request."""
        from app.worker.tasks import reapply_rules

        user_id = await current_tenant_id(self._session)
        await enqueue(
            self._session,
            reapply_rules,
            user_id=str(user_id),
            keep_overrides=keep_overrides,
            months=months,
            traceparent=current_traceparent(),
        )

    # -- Shared --------------------------------------------------------------

    async def _raise_on_conflict(
        self, *, pattern: str, match_type: str, options: RuleOptions, exclude_rule_id: uuid.UUID | None
    ) -> None:
        conflicts = await self._rules.find_conflicts(
            pattern=pattern,
            match_type=match_type,
            ignore_case=options.ignore_case,
            match_negative=options.match_negative,
            exclude_rule_id=exclude_rule_id,
        )
        if conflicts:
            other, overlap = conflicts[0]
            logger.info(events.RULE_CONFLICT_DETECTED, rule_id=str(other.id))
            raise RuleConflict(
                "A rule already covers part of this pattern.",
                details={
                    "kind": "rule_collision",
                    "existing_id": str(other.id),
                    "resolutions": ["specific_wins", "narrow_broader", "delete_specific"],
                    "affected_rows": overlap,
                    "message": f"{overlap} row(s) already match an existing rule.",
                },
            )

    async def _apply_rule(self, rule: Rule, *, backfill: bool) -> list[Transaction]:
        """Apply a rule's category to its matching rows.

        Only ``category_id`` and ``rule_id`` move; ``classified_by`` is left
        exactly as the cascade (or an earlier rule) set it, since a rule
        credits itself through ``rule_id``, not by inventing a "rule" rung
        the cascade never produces. Never touches a row whose
        ``classified_by`` is ``override`` -- enforced by
        :meth:`~app.db.repositories.RuleRepository.matching_transactions`
        itself, not repeated here.
        """
        rows = await self._rules.matching_transactions(
            pattern=rule.pattern,
            match_type=rule.match_type,
            ignore_case=rule.ignore_case,
            match_negative=rule.match_negative,
            only_unclassified=not backfill,
        )
        for row in rows:
            row.category_id = rule.category_id
            row.rule_id = rule.id
        await self._session.flush()
        await self._refresh(rows)
        return rows

    async def _refresh(self, rows: list[Transaction]) -> None:
        months = sorted({row.posted_on.replace(day=1) for row in rows})
        if months:
            await AggregateRefresher(self._session).refresh_months(months)

    async def _require_rule(self, rule_id: uuid.UUID) -> Rule:
        rule = await self._rules.get(rule_id)
        if rule is None:
            raise NotFound("Rule not found.")
        return rule

    async def _transactions_with_rule(self, rule_id: uuid.UUID) -> list[Transaction]:
        result = await self._session.execute(select(Transaction).where(Transaction.rule_id == rule_id))
        return list(result.scalars().all())

    async def _rows_in_month(self, month: date) -> list[Transaction]:
        result = await self._session.execute(
            select(Transaction).where(
                Transaction.posted_on >= month,
                Transaction.posted_on < _next_month(month),
                Transaction.excluded_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def _all_imported_months(self) -> list[date]:
        result = await self._session.execute(select(Transaction.posted_on).distinct())
        return [posted_on.replace(day=1) for posted_on in result.scalars().all()]

    @staticmethod
    def _first_matching_rule(rules: list[Rule], row: Transaction) -> Rule | None:
        """Checked against both descriptor_key and description_raw (see
        RuleRepository._condition()), so this preview never undercounts what
        apply_all_rules() -- built on the same RuleRepository -- will
        actually do.
        """
        return next(
            (
                rule
                for rule in rules
                if matches_row(
                    rule, descriptor_key=row.descriptor_key, description_raw=row.description_raw
                )
            ),
            None,
        )

    def _response(self, rule: Rule, rows_matched: int, *, category_name: str) -> RuleResponse:
        return RuleResponse(
            id=rule.id,
            pattern=rule.pattern,
            match_type=rule.match_type,
            category_id=rule.category_id,
            category_name=category_name,
            rows_matched=rows_matched,
            options=RuleOptions(ignore_case=rule.ignore_case, match_negative=rule.match_negative),
        )
