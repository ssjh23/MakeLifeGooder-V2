"""Review and classification. Screens 04, 04b, 04c, 05.

===========================================================================
BUILD STEPS 6.1, 6.2 and 6.3 live in this file.

  6.1 review board    depends on: 5.5 cascade
  6.2 duplicates      depends on: 3.2 transaction repository
  6.3 classify+finish depends on: 5.5, 6.1

Verify: uv run pytest tests/integration/test_review.py
===========================================================================
6.2 does not depend on 6.1, so if the cascade is not finished you can build the
duplicate pair handling first and come back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aggregate.refresh import AggregateRefresher
from app.api.errors import (
    CannotMergeIntoSelf,
    CategoryAlreadyExists,
    DescriptorAlreadySplit,
    DescriptorNotInGroup,
    LedgerError,
    NotFound,
    ReviewIncomplete,
    RuleConflict,
)
from app.classify.normalise import strip_reference_suffix
from app.classify.rules import matches_row
from app.db.models import (
    Category,
    ClassifiedBy,
    MatchType,
    MerchantOverride,
    Rule,
    Statement,
    Transaction,
)
from app.db.repositories import (
    CategoryRepository,
    DescriptorKeyOverrideRepository,
    MerchantRepository,
    RuleRepository,
    TransactionRepository,
)
from app.db.session import current_tenant_id
from app.money import sum_minor, to_decimal_string
from app.schemas.review import (
    DuplicatePair,
    ImportSummary,
    MerchantGroup,
    ReviewBoard,
    ReviewFilters,
    ReviewFooter,
    SuggestedCategory,
)
from app.schemas.rules import RuleOptions
from app.services._slug import slugify
from app.telemetry import events, get_logger

logger = get_logger(__name__)


class ReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._transactions = TransactionRepository(session)
        self._merchants = MerchantRepository(session)
        self._categories = CategoryRepository(session)
        self._rules = RuleRepository(session)

    # -- Step 6.1 ------------------------------------------------------------

    async def board(self, statement_id: uuid.UUID) -> ReviewBoard:
        """The review screen, grouped by merchant rather than by row.

        Four McDonald's rows are one decision, not four. That grouping is the
        reason each month costs the user less than the last.
        """
        statement = await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        counted = [row for row in rows if row.excluded_at is None]

        groups: dict[str, list[Transaction]] = {}
        for row in counted:
            key = row.descriptor_key or row.description_raw
            groups.setdefault(key, []).append(row)

        rules = await self._rules.all_for_user()

        merchant_groups: list[MerchantGroup] = []
        needs_category = new_merchants = overridden = 0
        for descriptor_key, group_rows in groups.items():
            category_id = group_rows[0].category_id
            classified_by = group_rows[0].classified_by
            merchant_id = group_rows[0].merchant_id

            matched_rule = self._matching_rule(
                descriptor_key, group_rows[0].description_raw, category_id, rules
            )
            if classified_by == ClassifiedBy.OVERRIDE:
                status = "overridden"
            elif matched_rule is not None:
                status = "rule_matched"
            else:
                status = "new"
            if status == "overridden":
                overridden += 1
            elif status == "new":
                new_merchants += 1
            if category_id is None:
                needs_category += 1

            suggested = None
            if category_id is not None:
                category = await self._categories.get(category_id)
                if category is not None:
                    suggested = SuggestedCategory(id=category.id, name=category.name)

            raw_descriptors = sorted({row.description_raw for row in group_rows})
            display_name = raw_descriptors[0]
            if merchant_id is not None:
                merchant = await self._merchants.get(merchant_id)
                if merchant is not None:
                    display_name = merchant.canonical_name

            merchant_groups.append(
                MerchantGroup(
                    descriptor_key=descriptor_key,
                    display_name=display_name,
                    raw_descriptors=raw_descriptors,
                    row_count=len(group_rows),
                    total=to_decimal_string(
                        sum_minor([row.amount_minor for row in group_rows]), statement.currency
                    ),
                    status=status,
                    suggested_category=suggested,
                    classified_by=classified_by.value if classified_by is not None else None,
                    rule_id=matched_rule.id if matched_rule is not None else None,
                    rule_pattern=matched_rule.pattern if matched_rule is not None else None,
                )
            )

        duplicate_pairs = self._find_pairs(counted)

        statement_total_minor = sum_minor([row.amount_minor for row in counted])
        unassigned_rows = [row for row in counted if row.category_id is None]
        unassigned_minor = sum_minor([row.amount_minor for row in unassigned_rows])
        classified_minor = statement_total_minor - unassigned_minor

        return ReviewBoard(
            footer=ReviewFooter(
                statement_total=to_decimal_string(statement_total_minor, statement.currency),
                classified=to_decimal_string(classified_minor, statement.currency),
                unassigned=to_decimal_string(unassigned_minor, statement.currency),
                can_finish=not unassigned_rows,
            ),
            filters=ReviewFilters(
                needs_category=needs_category,
                new_merchants=new_merchants,
                overridden=overridden,
                duplicates=len(duplicate_pairs),
            ),
            merchants=merchant_groups,
        )

    def _matching_rule(
        self,
        descriptor_key: str,
        description_raw: str,
        category_id: uuid.UUID | None,
        rules: list[Rule],
    ) -> Rule | None:
        if category_id is None:
            return None
        return next(
            (
                rule
                for rule in rules
                if rule.category_id == category_id
                and matches_row(rule, descriptor_key=descriptor_key, description_raw=description_raw)
            ),
            None,
        )

    # -- Step 6.2 --------------------------------------------------------------

    async def duplicates(self, statement_id: uuid.UUID) -> list[DuplicatePair]:
        """Same merchant, same amount, within a day.

        Flagged for a decision, never resolved automatically. A genuine repeat
        purchase looks identical to a double charge from the outside.
        """
        await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        active = [row for row in rows if row.excluded_at is None]
        return [self._pair_response(t1, t2) for t1, t2 in self._find_pairs(active)]

    async def resolve_duplicate(
        self,
        statement_id: uuid.UUID,
        pair_id: uuid.UUID,
        *,
        action: str,
        remove_row_id: uuid.UUID | None,
    ) -> None:
        """Settle one flagged pair.

        ``keep_both`` marks nothing: both rows are already counted, and
        recomputing the same pair on a later visit to this screen is the
        correct behaviour, not a bug, until one row is actually removed.

        ``remove`` sets ``excluded_at`` rather than deleting. The statement
        is a record of a document that exists in the world, and editing it
        away would make the ledger disagree with the paper (TC-TDUP-004).
        """
        statement = await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        active = [row for row in rows if row.excluded_at is None]
        pair = self._find_pair_by_id(active, pair_id)
        if pair is None:
            raise NotFound("Duplicate pair not found.")
        t1, t2 = pair

        if action == "keep_both":
            return
        if action != "remove":
            raise LedgerError(f"Unknown action: {action!r}")
        if remove_row_id is None or remove_row_id not in (t1.id, t2.id):
            raise LedgerError("remove_row_id must name one row of the pair.")

        target = t1 if t1.id == remove_row_id else t2
        target.excluded_at = datetime.now(UTC)
        await self._session.flush()
        logger.info(
            events.DUPLICATE_REMOVED, statement_id=str(statement_id), transaction_id=str(target.id)
        )
        await self._refresh_months(statement, [target])

    async def undo_duplicate(self, statement_id: uuid.UUID, pair_id: uuid.UUID) -> None:
        """Clear ``excluded_at``. Removal is reversible, and the UI promises
        that."""
        statement = await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        pair = self._find_pair_by_id(rows, pair_id, include_excluded=True)
        if pair is None:
            raise NotFound("Duplicate pair not found.")
        t1, t2 = pair
        excluded_row = t1 if t1.excluded_at is not None else t2 if t2.excluded_at is not None else None
        if excluded_row is None:
            return

        excluded_row.excluded_at = None
        await self._session.flush()
        logger.info(
            events.DUPLICATE_RESTORED, statement_id=str(statement_id), transaction_id=str(excluded_row.id)
        )
        await self._refresh_months(statement, [excluded_row])

    def _find_pairs(self, rows: list[Transaction]) -> list[tuple[Transaction, Transaction]]:
        pairs = []
        for i, t1 in enumerate(rows):
            if t1.descriptor_key is None:
                continue
            for t2 in rows[i + 1 :]:
                if (
                    t1.descriptor_key == t2.descriptor_key
                    and t1.amount_minor == t2.amount_minor
                    and abs((t1.posted_on - t2.posted_on).days) <= 1
                ):
                    pairs.append((t1, t2))
        return pairs

    def _find_pair_by_id(
        self, rows: list[Transaction], pair_id: uuid.UUID, *, include_excluded: bool = False
    ) -> tuple[Transaction, Transaction] | None:
        candidates = rows if include_excluded else [row for row in rows if row.excluded_at is None]
        for t1, t2 in self._find_pairs(candidates):
            if self._pair_id(t1.id, t2.id) == pair_id:
                return (t1, t2)
        return None

    @staticmethod
    def _pair_id(id1: uuid.UUID, id2: uuid.UUID) -> uuid.UUID:
        """Deterministic, not stored: there is no separate "duplicate pair"
        table, so the same two row ids always name the same pair rather than
        needing one persisted anywhere."""
        ordered = sorted((str(id1), str(id2)))
        return uuid.uuid5(uuid.NAMESPACE_OID, ":".join(ordered))

    def _pair_response(self, t1: Transaction, t2: Transaction) -> DuplicatePair:
        hours_apart = abs((t1.posted_on - t2.posted_on).days) * 24.0
        return DuplicatePair(
            pair_id=self._pair_id(t1.id, t2.id),
            row_ids=[t1.id, t2.id],
            merchant=t1.description_raw,
            amount=to_decimal_string(t1.amount_minor, t1.currency),
            hours_apart=hours_apart,
        )

    async def _refresh_months(self, statement: Statement, rows: list[Transaction]) -> None:
        months = sorted({row.posted_on.replace(day=1) for row in rows})
        if months:
            await AggregateRefresher(self._session).refresh_months(months)

    # -- Step 6.3 --------------------------------------------------------------

    async def classify(
        self,
        descriptor_key: str,
        *,
        category_id: uuid.UUID | None,
        new_category_name: str | None,
        create_rule: bool,
        scope: str,
        pattern: str | None = None,
        match_type: str = MatchType.EXACT.value,
        options: RuleOptions | None = None,
    ) -> None:
        """One decision for one merchant.

        Applies to every one of the tenant's rows carrying this descriptor,
        not just the statement that prompted the question -- a user's own
        ruling sits at the top of the cascade forever, for every statement.
        With ``scope="future"`` that reach stops at rows already classified;
        ``scope="backfill"`` restates history too.

        ``pattern``/``match_type``/``options`` are the same rule-authoring
        choices the Rules screen's "New rule" form offers -- a person isn't
        limited to an exact match on this one descriptor just because
        they're creating the rule from here. Left at their defaults
        (``pattern=None`` meaning ``descriptor_key``, ``match_type="exact"``)
        this behaves exactly as it always has. Only the merchant group's own
        rows -- matched by ``descriptor_key``, regardless of how broad a
        pattern was chosen -- are reclassified immediately below; a broader
        pattern only reaches other, currently-separate merchant groups
        going forward, the same as a rule created from the Rules screen
        would for statements imported after it.
        """
        if not descriptor_key or descriptor_key.isspace():
            raise LedgerError("descriptor_key must be non-empty")

        user_id = await current_tenant_id(self._session)

        if new_category_name is not None:
            slug = slugify(new_category_name)
            # Checked first, not caught after: `uq_categories_user_id_slug`
            # would reject a repeat name at the database level regardless,
            # but as a raw IntegrityError rather than something a client can
            # act on. Two different display names that slugify the same way
            # ("Coffee" and "COFFEE!") collide here too, which is correct --
            # the slug, not the display name, is the identity rules and code
            # reference.
            existing = await self._categories.find_by_slug_for_user(slug, user_id)
            if existing is not None:
                raise CategoryAlreadyExists(
                    f"You already have a category named {existing.name!r}.",
                    details={"existing_category_id": str(existing.id)},
                )
            category = Category(slug=slug, name=new_category_name, user_id=user_id)
            self._session.add(category)
            await self._session.flush()
            category_id = category.id
        else:
            assert category_id is not None  # schema enforces exactly one of the two
            category = await self._categories.get(category_id)
            if category is None:
                raise NotFound("Category not found.")

        existing_override = (
            await self._session.execute(
                select(MerchantOverride).where(
                    MerchantOverride.user_id == user_id,
                    MerchantOverride.descriptor_key == descriptor_key,
                )
            )
        ).scalar_one_or_none()
        if existing_override is not None:
            existing_override.category_id = category_id
        else:
            self._session.add(
                MerchantOverride(user_id=user_id, descriptor_key=descriptor_key, category_id=category_id)
            )
        await self._session.flush()

        if create_rule:
            rule_pattern = pattern or descriptor_key
            rule_options = options or RuleOptions()
            conflicts = await self._rules.find_conflicts(
                pattern=rule_pattern,
                match_type=match_type,
                ignore_case=rule_options.ignore_case,
                match_negative=rule_options.match_negative,
            )
            if conflicts:
                rule, overlap = conflicts[0]
                raise RuleConflict(
                    "A rule already covers this descriptor.",
                    details={
                        "kind": "rule_collision",
                        "existing_id": str(rule.id),
                        "resolutions": ["specific_wins", "narrow_broader", "delete_specific"],
                        "affected_rows": overlap,
                        "message": f"{overlap} row(s) already match an existing rule.",
                    },
                )
            await self._rules.create(
                pattern=rule_pattern,
                match_type=match_type,
                category_id=category_id,
                scope=scope,
                ignore_case=rule_options.ignore_case,
                match_negative=rule_options.match_negative,
            )
            logger.info(events.RULE_CREATED, descriptor=descriptor_key, category_id=str(category_id))

        conditions = [Transaction.descriptor_key == descriptor_key]
        if scope != "backfill":
            conditions.append(Transaction.category_id.is_(None))
        matching = (
            (await self._session.execute(select(Transaction).where(*conditions))).scalars().all()
        )
        now = datetime.now(UTC)
        for row in matching:
            row.category_id = category_id
            row.classified_by = ClassifiedBy.OVERRIDE
            row.confidence = None
            row.prompt_version = None
            row.classified_at = now
        await self._session.flush()

        if matching:
            months = sorted({row.posted_on.replace(day=1) for row in matching})
            await AggregateRefresher(self._session).refresh_months(months)

    async def split_descriptor(self, descriptor_key: str, *, description_raw: str) -> str:
        """Eject one exact raw statement line into its own, permanent key.

        Every past and future row carrying this exact ``description_raw``
        stops being folded by ``normalise()`` and becomes its own
        unclassified merchant -- named later through the ordinary
        :meth:`classify` flow, the same as any genuinely new merchant.

        Scope is always all-history and future together: a person correcting
        an over-merge is disagreeing with the normaliser's judgment about
        this string, not asking to treat one statement specially.
        """
        if not description_raw or description_raw.isspace():
            raise LedgerError("description_raw must be non-empty")

        user_id = await current_tenant_id(self._session)
        overrides_repo = DescriptorKeyOverrideRepository(self._session)

        existing = await overrides_repo.find_one(description_raw)
        if existing is not None:
            raise DescriptorAlreadySplit(
                "This descriptor has already been split into its own merchant.",
                details={"descriptor_key": existing.descriptor_key},
            )

        matching = await self._transactions.list_by_description_raw(description_raw)
        if not any(row.descriptor_key == descriptor_key for row in matching):
            raise DescriptorNotInGroup("This descriptor is not part of that merchant group.")

        candidate = slugify(strip_reference_suffix(description_raw))
        new_key = candidate
        suffix = 2
        while await self._transactions.descriptor_key_in_use(
            new_key, excluding_description_raw=description_raw
        ):
            new_key = f"{candidate}-{suffix}"
            suffix += 1

        for row in matching:
            row.descriptor_key = new_key
            row.merchant_id = None
            row.category_id = None
            row.classified_by = None
            row.confidence = None
            row.prompt_version = None
            row.classified_at = None
            row.rule_id = None

        await overrides_repo.create(
            user_id=user_id, description_raw=description_raw, descriptor_key=new_key
        )
        await self._session.flush()

        logger.info(
            events.DESCRIPTOR_SPLIT,
            old_descriptor_key=descriptor_key,
            new_descriptor_key=new_key,
            row_count=len(matching),
        )  # never logs description_raw itself -- ADR-014

        months = sorted({row.posted_on.replace(day=1) for row in matching})
        if months:
            await AggregateRefresher(self._session).refresh_months(months)

        return new_key

    async def merge_descriptors(self, descriptor_key: str, *, target_descriptor_key: str) -> None:
        """Fold one merchant group into another, permanently -- the inverse
        of :meth:`split_descriptor`, for a normaliser under-merge (two
        genuinely different-looking descriptors that are the same real
        merchant) rather than an over-merge.

        The absorbed rows adopt the target's own classification outright
        (category, merchant, how it was classified) rather than resetting to
        unclassified: the whole point of merging is asserting "these are the
        same merchant", so they should immediately get the same treatment,
        not cost the person a second decision. ``rule_id`` is the one field
        never copied across -- it names a specific rule matching a specific
        row's own text, which merging doesn't change, so each row keeps
        whatever rule credit it already had (or none).

        Every distinct raw line in the absorbed group is pinned to the
        target key going forward too, the same permanent, all-history-and-
        future mechanism ``split_descriptor`` already uses -- a future
        statement with the same raw text lands directly in the merged group
        rather than being normalised back into its own.
        """
        if descriptor_key == target_descriptor_key:
            raise CannotMergeIntoSelf("A merchant group cannot be merged into itself.")

        source_rows = await self._transactions.list_by_descriptor_key(descriptor_key)
        if not source_rows:
            raise NotFound("This merchant group has no rows.")

        target_rows = await self._transactions.list_by_descriptor_key(target_descriptor_key)
        if not target_rows:
            raise NotFound("The target merchant group was not found.")

        canonical = target_rows[0]
        user_id = await current_tenant_id(self._session)
        overrides_repo = DescriptorKeyOverrideRepository(self._session)

        raw_texts = {row.description_raw for row in source_rows}
        for raw_text in raw_texts:
            await overrides_repo.set(
                user_id=user_id, description_raw=raw_text, descriptor_key=target_descriptor_key
            )

        for row in source_rows:
            row.descriptor_key = target_descriptor_key
            row.merchant_id = canonical.merchant_id
            row.category_id = canonical.category_id
            row.classified_by = canonical.classified_by
            row.confidence = canonical.confidence
            row.prompt_version = canonical.prompt_version
            row.classified_at = canonical.classified_at
            row.rule_id = None

        await self._session.flush()

        logger.info(
            events.DESCRIPTOR_MERGED,
            old_descriptor_key=descriptor_key,
            new_descriptor_key=target_descriptor_key,
            row_count=len(source_rows),
        )  # never logs description_raw itself -- ADR-014

        months = sorted({row.posted_on.replace(day=1) for row in source_rows})
        if months:
            await AggregateRefresher(self._session).refresh_months(months)

    async def confirm_all(
        self, statement_id: uuid.UUID, descriptor_keys: list[str] | None
    ) -> None:
        """Bulk confirm rule-matched merchants only.

        Stamps ``rule_id`` onto the matching rows, crediting the standing
        rule for the classification -- the same field screen 06c's
        provenance reads. Never touches a merchant the cascade left
        unclassified: confirming a decision nobody made is how a bulk action
        stops being trusted (TC-REV-018).
        """
        statement = await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        active = [row for row in rows if row.excluded_at is None]
        rules = await self._rules.all_for_user()

        # Per row, not per descriptor_key group: two rows sharing a
        # descriptor_key can carry different description_raw (two different
        # "SMP*..." lines both collapsing to "smp"), so a rule matching one
        # via raw text does not necessarily match the other -- unlike the
        # cascade's own resolution, which is genuinely uniform per group.
        touched: list[Transaction] = []
        for row in active:
            if row.descriptor_key is None or row.category_id is None:
                continue
            if descriptor_keys is not None and row.descriptor_key not in descriptor_keys:
                continue
            rule = next(
                (
                    r
                    for r in rules
                    if r.category_id == row.category_id
                    and matches_row(
                        r, descriptor_key=row.descriptor_key, description_raw=row.description_raw
                    )
                ),
                None,
            )
            if rule is None:
                continue
            row.rule_id = rule.id
            touched.append(row)

        await self._session.flush()
        await self._refresh_months(statement, touched)

    async def finish(self, statement_id: uuid.UUID) -> ImportSummary:
        """Close review and return the import summary.

        422 while any money is unassigned. "Skip for now" during classify()
        still assigns a category (see its docstring); this is what actually
        gates completion, not the button.
        """
        statement = await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        active = [row for row in rows if row.excluded_at is None]
        unassigned = [row for row in active if row.category_id is None]
        if unassigned:
            raise ReviewIncomplete(
                "Money is still unassigned.",
                details={
                    "unassigned_rows": len(unassigned),
                    "unassigned": to_decimal_string(
                        sum_minor([row.amount_minor for row in unassigned]), statement.currency
                    ),
                },
            )

        await AggregateRefresher(self._session).refresh_statement(statement_id)
        logger.info(events.STATEMENT_REVIEW_FINISHED, statement_id=str(statement_id))

        return self._summary_response(statement, rows)

    async def summary(self, statement_id: uuid.UUID) -> ImportSummary:
        """Rebuild the same summary later. Screen 04c is a receipt, and a
        receipt you can only see once is not much of one."""
        statement = await self._require_statement(statement_id)
        rows = await self._transactions.list_for_statement(statement_id)
        return self._summary_response(statement, rows)

    def _summary_response(self, statement: Statement, rows: list[Transaction]) -> ImportSummary:
        active = [row for row in rows if row.excluded_at is None]
        return ImportSummary(
            statement_id=statement.id,
            rows_imported=len(rows),
            duplicates_removed=len(rows) - len(active),
            counted=len(active),
            classified_by_rule=sum(1 for row in active if row.rule_id is not None),
            classified_by_user=sum(
                1 for row in active if row.classified_by == ClassifiedBy.OVERRIDE and row.rule_id is None
            ),
            unclassified=sum(1 for row in active if row.category_id is None),
        )

    # -- Shared helpers --------------------------------------------------------

    async def _require_statement(self, statement_id: uuid.UUID) -> Statement:
        statement = await self._session.get(Statement, statement_id)
        if statement is None:
            raise NotFound("Statement not found.")
        return statement
