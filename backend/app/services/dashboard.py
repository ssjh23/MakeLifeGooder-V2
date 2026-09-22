"""Dashboard and per-row override. Screens 06, 06b, 06c.

===========================================================================
BUILD STEP 7.2   depends on: 7.1 aggregate refresher
Verify: uv run pytest tests/integration/test_dashboard.py
===========================================================================
The milestone after this one is the whole product working end to end: import,
reconcile, review, classify, see the breakdown.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.aggregate.refresh import AggregateRefresher
from app.api.errors import NotFound
from app.config import get_settings
from app.db.models import ClassifiedBy, Merchant, Rule, Statement, Transaction
from app.db.repositories import (
    CardRepository,
    CategoryRepository,
    DashboardRepository,
    MerchantRepository,
    RuleRepository,
    TransactionRepository,
)
from app.money import DEFAULT_CURRENCY, sum_minor, to_decimal_string
from app.schemas.dashboard import (
    CardBand,
    CategoryBand,
    CategoryDetail,
    CategoryTotal,
    CompanyRow,
    DashboardResponse,
    MonthBand,
    OverrideResponse,
    Provenance,
    TransactionResponse,
    UnclassifiedBanner,
)
from app.storage import ObjectStore, build_object_store
from app.telemetry import events, get_logger

logger = get_logger(__name__)

#: How many trailing months (including the anchor) each range covers.
_RANGE_MONTHS = {"month": 1, "6m": 6, "year": 12}


def _preceding_months(anchor: date, count: int) -> list[date]:
    """``count`` calendar months, first-of-month, ending at (and including)
    ``anchor``, oldest first."""
    months = []
    year, month = anchor.year, anchor.month
    for _ in range(count):
        months.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return sorted(months)


def _next_month(month: date) -> date:
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


def _previous_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


class DashboardService:
    def __init__(self, session: AsyncSession, store: ObjectStore | None = None) -> None:
        self._session = session
        self._store = store
        self._dashboard = DashboardRepository(session)
        self._categories = CategoryRepository(session)
        self._cards = CardRepository(session)
        self._merchants = MerchantRepository(session)
        self._transactions = TransactionRepository(session)
        self._rules = RuleRepository(session)

    # -- The five bands ------------------------------------------------------

    async def bands(self, *, range_: str, month: date | None) -> DashboardResponse:
        """Months stacked, categories ranked, by card, and the unclassified
        banner -- all read from precomputed totals, never summed here.

        Handles the empty case by construction: a tenant with no totals yet
        gets zeroed bands, not a divide by zero in a share calculation.
        """
        anchor = (month or date.today()).replace(day=1)
        months = _preceding_months(anchor, _RANGE_MONTHS[range_])
        start, end_exclusive = months[0], _next_month(months[-1])

        # One extra month before the range, purely to compute "vs last month"
        # for the anchor's own category totals -- needed even when range is
        # "month" (a single point has no earlier point of its own to compare
        # against otherwise).
        prior_anchor_month = _previous_month(anchor)
        fetch_months = sorted({*months, prior_anchor_month})
        totals = await self._dashboard.totals_for_months(fetch_months)
        unclassified_count, unclassified_minor = await self._dashboard.unclassified_total(
            start=start, end_exclusive=end_exclusive
        )

        category_ids = {t.category_id for t in totals}
        card_ids = {t.card_id for t in totals}
        categories = {cid: await self._categories.get(cid) for cid in category_ids}
        cards = {cid: await self._cards.get(cid) for cid in card_ids}

        month_totals: dict[date, int] = dict.fromkeys(months, 0)
        month_by_category: dict[date, dict[str, int]] = {m: {} for m in months}
        category_totals: dict[uuid.UUID, int] = {}
        category_rows: dict[uuid.UUID, int] = {}
        category_totals_by_month: dict[date, dict[uuid.UUID, int]] = {}
        card_totals: dict[uuid.UUID, int] = {}
        card_rows: dict[uuid.UUID, int] = {}
        card_category_totals: dict[uuid.UUID, dict[uuid.UUID, int]] = {}

        for total in totals:
            category_totals_by_month.setdefault(total.month, {})
            category_totals_by_month[total.month][total.category_id] = (
                category_totals_by_month[total.month].get(total.category_id, 0)
                + total.total_minor
            )

            if total.month not in months:
                continue  # the extra prior month only feeds `change` above

            month_totals[total.month] += total.total_minor
            category = categories.get(total.category_id)
            name = category.name if category is not None else "Unknown"
            month_by_category[total.month][name] = (
                month_by_category[total.month].get(name, 0) + total.total_minor
            )
            category_totals[total.category_id] = (
                category_totals.get(total.category_id, 0) + total.total_minor
            )
            category_rows[total.category_id] = (
                category_rows.get(total.category_id, 0) + total.txn_count
            )
            card_totals[total.card_id] = card_totals.get(total.card_id, 0) + total.total_minor
            card_rows[total.card_id] = card_rows.get(total.card_id, 0) + total.txn_count
            card_category_totals.setdefault(total.card_id, {})
            card_category_totals[total.card_id][total.category_id] = (
                card_category_totals[total.card_id].get(total.category_id, 0)
                + total.total_minor
            )

        classified_minor = sum(category_totals.values())
        grand_total = classified_minor or 1  # avoid a divide by zero when nothing is classified yet

        prior_category_totals = category_totals_by_month.get(prior_anchor_month, {})

        months_band = [
            MonthBand(
                month=m,
                total=to_decimal_string(month_totals[m], DEFAULT_CURRENCY),
                by_category={
                    name: to_decimal_string(amount, DEFAULT_CURRENCY)
                    for name, amount in month_by_category[m].items()
                },
            )
            for m in months
        ]

        categories_band = []
        for cid, amount in sorted(category_totals.items(), key=lambda kv: -kv[1]):
            change = None
            previous = prior_category_totals.get(cid)
            if previous:  # zero or absent both mean "no prior month to compare"
                anchor_amount = category_totals_by_month.get(anchor, {}).get(cid, 0)
                change = (anchor_amount - previous) / abs(previous)

            # The whole displayed range, not just the anchor month: matches
            # the scope `amount`/`share` above already use, so a "6 months"
            # or "year" view doesn't show a real category total next to an
            # empty merchant list just because the anchor month itself is
            # thin (or, viewed today, not yet underway).
            top, other_count, other_total = await self._top_merchants_in_category(
                cid, start=start, end_exclusive=end_exclusive
            )

            category_row = categories.get(cid)
            categories_band.append(
                CategoryBand(
                    category_id=cid,
                    name=category_row.name if category_row is not None else "Unknown",
                    total=to_decimal_string(amount, DEFAULT_CURRENCY),
                    share=amount / grand_total,
                    change=change,
                    rows=category_rows.get(cid, 0),
                    top_merchants=top,
                    other_merchants_count=other_count,
                    other_merchants_total=(
                        to_decimal_string(other_total, DEFAULT_CURRENCY) if other_count else None
                    ),
                )
            )

        cards_band = []
        for cid, amount in card_totals.items():
            by_category = card_category_totals.get(cid, {})
            largest_category = None
            if by_category:
                largest_cid, largest_amount = max(by_category.items(), key=lambda kv: kv[1])
                largest_category_row = categories.get(largest_cid)
                largest_name = (
                    largest_category_row.name if largest_category_row is not None else "Unknown"
                )
                largest_category = CategoryTotal(
                    category_id=largest_cid,
                    name=largest_name,
                    total=to_decimal_string(largest_amount, DEFAULT_CURRENCY),
                )

            card_row = cards.get(cid)
            by_category_named: dict[str, str] = {}
            for c, a in by_category.items():
                row = categories.get(c)
                by_category_named[row.name if row is not None else "Unknown"] = to_decimal_string(
                    a, DEFAULT_CURRENCY
                )

            cards_band.append(
                CardBand(
                    card_id=cid,
                    nickname=card_row.nickname if card_row is not None else "Unknown",
                    colour=card_row.colour_hex if card_row is not None else None,
                    total=to_decimal_string(amount, DEFAULT_CURRENCY),
                    rows=card_rows.get(cid, 0),
                    largest_category=largest_category,
                    by_category=by_category_named,
                )
            )

        return DashboardResponse(
            banner=UnclassifiedBanner(
                classified=to_decimal_string(classified_minor, DEFAULT_CURRENCY),
                unclassified=to_decimal_string(unclassified_minor, DEFAULT_CURRENCY),
                locked=unclassified_count > 0,
            ),
            months=months_band,
            categories=categories_band,
            cards=cards_band,
            range=range_,
        )

    async def _top_merchants_in_category(
        self, category_id: uuid.UUID, *, start: date, end_exclusive: date
    ) -> tuple[list[CompanyRow], int, int]:
        """Top two merchants in a category for one month, the rest folded
        into "others" -- screen 06's category card (band 02), not to be
        confused with 06b's full ranked list for every month in range."""
        rows = await self._dashboard.merchant_totals_in_category(
            category_id, start=start, end_exclusive=end_exclusive
        )
        # A row classified by name (``new_category_name``) or by an
        # LLM/rule match that never resolved a merchant carries no
        # `merchant_id` at all -- real money, no company to attribute it to,
        # so it is folded into "others" rather than dropped.
        named = [row for row in rows if row[0] is not None]
        named.sort(key=lambda row: -row[1])
        category_total = sum(row[1] for row in rows) or 1

        top_rows = named[:2]
        merchant_ids = {row[0] for row in top_rows if row[0] is not None}
        merchants = await self._merchants.get_many(merchant_ids)
        top = [
            CompanyRow(
                merchant_id=merchant_id,
                name=(
                    merchants[merchant_id].canonical_name if merchant_id in merchants else "Unknown"
                ),
                total=to_decimal_string(total_minor, DEFAULT_CURRENCY),
                share=total_minor / category_total,
                transaction_count=txn_count,
            )
            for merchant_id, total_minor, txn_count in top_rows
            if merchant_id is not None
        ]

        top_ids = {row[0] for row in top_rows}
        remaining = [row for row in rows if row[0] not in top_ids]
        other_total = sum(row[1] for row in remaining)
        other_count = len(remaining)
        return top, other_count, other_total

    async def category_detail(self, category_id: uuid.UUID) -> CategoryDetail:
        """Six months of totals, companies ranked within the category, and
        which cards were used. Screen 06b answers *who*."""
        category = await self._categories.get(category_id)
        if category is None:
            raise NotFound("Category not found.")

        anchor = date.today().replace(day=1)
        months = _preceding_months(anchor, 6)
        start, end_exclusive = months[0], _next_month(months[-1])

        totals = await self._dashboard.totals_for_months(months)
        month_totals: dict[date, int] = dict.fromkeys(months, 0)
        for total in totals:
            if total.category_id == category_id:
                month_totals[total.month] += total.total_minor
        six_month_totals = [
            MonthBand(month=m, total=to_decimal_string(month_totals[m], DEFAULT_CURRENCY), by_category={})
            for m in months
        ]

        merchant_rows = await self._dashboard.merchant_totals_in_category(
            category_id, start=start, end_exclusive=end_exclusive
        )
        company_total = sum(row[1] for row in merchant_rows) or 1
        companies = []
        for merchant_id, total_minor, txn_count in sorted(merchant_rows, key=lambda r: -r[1]):
            if merchant_id is None:
                continue
            merchant = await self._merchants.get(merchant_id)
            companies.append(
                CompanyRow(
                    merchant_id=merchant_id,
                    name=merchant.canonical_name if merchant is not None else "Unknown",
                    total=to_decimal_string(total_minor, DEFAULT_CURRENCY),
                    share=total_minor / company_total,
                    transaction_count=txn_count,
                )
            )

        card_rows = await self._dashboard.card_totals_in_category(
            category_id, start=start, end_exclusive=end_exclusive
        )
        cards_used = []
        for card_id, total_minor in card_rows:
            card = await self._cards.get(card_id)
            if card is not None:
                cards_used.append(
                    CardBand(
                        card_id=card.id,
                        nickname=card.nickname,
                        colour=card.colour_hex,
                        total=to_decimal_string(total_minor, DEFAULT_CURRENCY),
                    )
                )

        return CategoryDetail(
            category_id=category.id,
            name=category.name,
            six_month_totals=six_month_totals,
            companies=companies,
            cards_used=cards_used,
        )

    # -- Transaction list --------------------------------------------------------

    async def list_transactions(
        self,
        *,
        category_id: uuid.UUID | None,
        card_id: uuid.UUID | None,
        merchant_id: uuid.UUID | None,
        from_: date | None,
        to: date | None,
        search: str | None = None,
        sort: str = "date_desc",
    ) -> list[TransactionResponse]:
        rows = await self._transactions.list_filtered(
            category_id=category_id,
            card_id=card_id,
            merchant_id=merchant_id,
            from_=from_,
            to=to,
            search=search,
            sort=sort,
        )
        # One bulk lookup rather than one per row -- screen 06's
        # individual-transactions band (05) can list dozens of rows at once.
        merchants = await self._merchants.get_many(
            {row.merchant_id for row in rows if row.merchant_id is not None}
        )
        # A row a rule classified without the cascade ever resolving a
        # merchant has no merchant_name to show -- the rule's own pattern is
        # the only thing that names it, so the list needs this too, not just
        # the single-transaction detail view.
        rules = await self._rules.get_many(
            {row.rule_id for row in rows if row.rule_id is not None}
        )
        return [
            self._transaction_response(row, merchants=merchants, rules=rules) for row in rows
        ]

    # -- One row ---------------------------------------------------------------

    async def transaction(self, transaction_id: uuid.UUID) -> TransactionResponse:
        """The only place that answers *why is this in this category*."""
        row = await self._transactions.get(transaction_id)
        if row is None:
            raise NotFound("Transaction not found.")

        merchant = await self._merchants.get(row.merchant_id) if row.merchant_id else None
        merchants = {merchant.id: merchant} if merchant is not None else {}
        rules = {}
        if row.rule_id is not None:
            rule = await self._rules.get(row.rule_id)
            if rule is not None:
                rules = {rule.id: rule}
        flags = await self._duplicate_flags(row)
        return self._transaction_response(row, merchants=merchants, rules=rules, flags=flags)

    async def _duplicate_flags(self, row: Transaction) -> list[str]:
        """Whether this row is one half of a pair the person already chose
        to keep both of (TC-TDUP), so screen 06c can say so -- a flag the
        review screen already knew about and the dashboard otherwise loses."""
        pairs = await self._transactions.find_duplicate_pairs(row.statement_id)
        for first, second in pairs:
            if row.id in (first.id, second.id):
                return ["paired_with_duplicate_kept"]
        return []

    async def override(self, transaction_id: uuid.UUID, *, category_id: uuid.UUID) -> OverrideResponse:
        """Recategorise one row.

        Sets ``classified_by='override'``, which protects the row from a
        future rule reapply, and clears ``rule_id``: this decision is the
        person's own, not a rule's, from this point on.
        """
        row = await self._transactions.get(transaction_id)
        if row is None:
            raise NotFound("Transaction not found.")
        category = await self._categories.get(category_id)
        if category is None:
            raise NotFound("Category not found.")

        row.category_id = category_id
        row.classified_by = ClassifiedBy.OVERRIDE
        row.rule_id = None
        row.confidence = None
        row.prompt_version = None
        row.classified_at = datetime.now(UTC)
        await self._session.flush()

        month = row.posted_on.replace(day=1)
        await AggregateRefresher(self._session).refresh_months([month])
        logger.info(
            events.TRANSACTION_OVERRIDDEN, transaction_id=str(transaction_id), category_id=str(category_id)
        )

        totals = await self._dashboard.totals_for_months([month])
        category_minor = sum(t.total_minor for t in totals if t.category_id == category_id)

        statement = await self._session.get(Statement, row.statement_id)
        statement_rows = await self._transactions.list_for_statement(row.statement_id)
        statement_total_minor = sum_minor(
            [r.amount_minor for r in statement_rows if r.excluded_at is None]
        )
        currency = statement.currency if statement is not None else DEFAULT_CURRENCY

        return OverrideResponse(
            transaction_id=transaction_id,
            category_totals={str(category_id): to_decimal_string(category_minor, currency)},
            statement_total=to_decimal_string(statement_total_minor, currency),
        )

    def _transaction_response(
        self,
        row: Transaction,
        *,
        merchants: dict[uuid.UUID, Merchant] | None = None,
        rules: dict[uuid.UUID, Rule] | None = None,
        flags: list[str] | None = None,
    ) -> TransactionResponse:
        merchant = merchants.get(row.merchant_id) if merchants and row.merchant_id else None
        rule = rules.get(row.rule_id) if rules and row.rule_id else None
        return TransactionResponse(
            id=row.id,
            posted_on=row.posted_on,
            description=row.description_raw,
            amount=to_decimal_string(row.amount_minor, row.currency),
            currency=row.currency,
            category_id=row.category_id,
            merchant_id=row.merchant_id,
            merchant_name=merchant.canonical_name if merchant is not None else None,
            descriptor_key=row.descriptor_key,
            card_id=row.card_id,
            provenance=Provenance(
                statement_id=row.statement_id,
                page=row.page,
                line=row.line,
                classified_by=row.classified_by.value if row.classified_by is not None else None,
                rule_id=row.rule_id,
                rule_pattern=rule.pattern if rule is not None else None,
                prompt_version=row.prompt_version,
            ),
            flags=flags or [],
        )

    async def source_page_url(self, statement_id: uuid.UUID) -> str:
        """A short-lived signed URL for the source PDF.

        Never logged: it is a bearer credential, and a logged one is a
        leaked file.
        """
        statement = await self._session.get(Statement, statement_id)
        if statement is None:
            raise NotFound("Statement not found.")
        store = self._store or build_object_store(get_settings())
        return store.presign_download(key=statement.object_key)
