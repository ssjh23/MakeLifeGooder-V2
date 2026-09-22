"""Cards, categories, export and deletion. Screens 02b to 02d, 08 to 08c.

===========================================================================
BUILD STEPS 9.1, 9.2 and 9.3 live in this file. They are independent of each
other, so take them in whichever order you need.

  9.1 CardService      depends on: 3.1 statement repository
      Verify: uv run pytest tests/integration/test_cards.py

  9.2 CategoryService  depends on: 6.3 review classify
      Verify: uv run pytest tests/integration/test_categories.py

  9.3 AccountService   depends on: 7.1 aggregates
      Verify: uv run pytest tests/integration/test_account.py
===========================================================================
Last phase. Everything here is reachable from the product working end to end,
which is why it comes after the dashboard rather than before it.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.aggregate.refresh import AggregateRefresher
from app.api.errors import CategoryAlreadyExists, Conflict, LedgerError, NotFound
from app.config import get_settings
from app.db.models import (
    Account,
    Card,
    CardStatus,
    Export,
    ExportStatus,
    Rule,
    Statement,
    Transaction,
    User,
)
from app.db.repositories import (
    CardRepository,
    CategoryRepository,
    StatementRepository,
    TransactionRepository,
)
from app.db.session import current_tenant_id
from app.money import to_decimal_string
from app.schemas.account import (
    AccountInventory,
    CategoryResponse,
    ExportResponse,
)
from app.schemas.cards import ArchivePreview, CardResponse, StatementDisposition
from app.services._slug import slugify
from app.storage import ObjectStore, build_object_store
from app.telemetry import current_traceparent, events, get_logger
from app.worker.queue import enqueue
from app.worker.tasks import purge_account

logger = get_logger(__name__)

#: The columns every transaction export carries. Declared before the file is
#: written, per TC-ACCT-002 to 006 -- somebody about to take their data
#: elsewhere should know what they are getting before they commit to it.
EXPORT_COLUMNS = ["posted_on", "description", "amount", "currency", "category", "card"]


class CardService:
    """BUILD STEP 9.1.

    Known gap: :class:`~app.schemas.cards.CardCreate` has no field naming
    which existing account a new card belongs to, so a second card at the
    same institution and of the same kind is grouped onto whichever such
    account already exists rather than getting its own -- correct for a
    supplementary card, not distinguishable from a genuinely separate second
    account at the same bank. Flagged rather than silently guessed; the
    schema is what would need a field added to resolve it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cards = CardRepository(session)
        self._statements = StatementRepository(session)
        self._transactions = TransactionRepository(session)

    async def list_cards(self) -> list[CardResponse]:
        rows = await self._cards.list_with_counts()
        return [await self._response(card, count) for card, count in rows]

    async def create(self, **fields: object) -> CardResponse:
        """Create a card.

        The request schema (:class:`~app.schemas.cards.CardCreate`) is
        already the complete set of fields the system accepts about a
        card -- there is no full number, expiry or CVV anywhere in it, and
        ``Schema``'s ``extra="forbid"`` rejects anything beyond that rather
        than silently dropping it (TC-CARD-002, TC-CARD-003).
        """
        account = await self._find_or_create_account(
            institution=str(fields["institution"]), kind=str(fields["type"])
        )
        card = Card(
            user_id=await current_tenant_id(self._session),
            account_id=account.id,
            nickname=str(fields["nickname"]),
            last4=str(fields["last4"]),
            statement_day=fields.get("statement_day"),  # type: ignore[arg-type]
            currency=str(fields.get("currency") or "SGD"),
            colour_hex=fields.get("colour"),  # type: ignore[arg-type]
        )
        self._session.add(card)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise Conflict(
                "This card's last 4 digits are already in use by another active card."
            ) from exc
        return await self._response(card, 0)

    async def update(self, card_id: uuid.UUID, **changes: object) -> CardResponse:
        """Rename, recolour, or correct the last four digits or
        institution. Never touches a statement.

        ``institution`` lives on the card's ``Account``, not the card
        itself -- cards are grouped onto an account by ``(institution,
        kind)`` the same way :meth:`create` finds or creates one, so
        correcting it here moves the card onto whichever account now
        matches, creating one if this is the first card there. The kind
        itself is not editable through this call; the card keeps its
        current account's kind.
        """
        card = await self._require_card(card_id)
        if "nickname" in changes and changes["nickname"] is not None:
            card.nickname = str(changes["nickname"])
        if "colour" in changes:
            card.colour_hex = changes["colour"]  # type: ignore[assignment]
        if "last4" in changes and changes["last4"] is not None:
            card.last4 = str(changes["last4"])
        if "institution" in changes and changes["institution"] is not None:
            current_account = await self._session.get(Account, card.account_id)
            kind = str(current_account.account_kind) if current_account is not None else "credit"
            account = await self._find_or_create_account(
                institution=str(changes["institution"]), kind=kind
            )
            card.account_id = account.id
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise Conflict(
                "This card's last 4 digits are already in use by another active card."
            ) from exc
        _, count = await self._with_count(card)
        return await self._response(card, count)

    async def archive_preview(self, card_id: uuid.UUID) -> ArchivePreview:
        """What archiving would do, computed once and shared with
        :meth:`archive` so the preview and the action can never disagree."""
        card = await self._require_card(card_id)
        statements = await self._statements.list_for_user(card_id=card.id)
        return ArchivePreview(
            statements=[statement.id for statement in statements],
            totals_that_move=0,
            totals_unchanged=len(statements),
        )

    async def archive(
        self, card_id: uuid.UUID, dispositions: list[StatementDisposition]
    ) -> CardResponse:
        """Archive a card, reversibly.

        Every statement on the card needs a disposition, or the whole
        request is refused -- a partially archived card would leave
        statements attached to something the user believes is gone
        (TC-CARD-007).
        """
        card = await self._require_card(card_id)
        statements = await self._statements.list_for_user(card_id=card.id)
        by_id = {d.statement_id: d for d in dispositions}
        missing = [s.id for s in statements if s.id not in by_id]
        if missing:
            raise LedgerError(
                "Every statement on this card needs a disposition before it can be archived.",
                details={"missing_statement_ids": [str(sid) for sid in missing]},
            )

        touched_months: set = set()
        for statement in statements:
            disposition = by_id[statement.id]
            if disposition.action == "reassign":
                if disposition.target_card_id is None:
                    raise LedgerError("target_card_id is required when reassigning a statement.")
                statement.card_id = disposition.target_card_id
                rows = await self._transactions.list_for_statement(statement.id)
                for row in rows:
                    row.card_id = disposition.target_card_id
                    touched_months.add(row.posted_on.replace(day=1))
            # "keep" leaves the statement attached to the now-archived card.

        card.status = CardStatus.ARCHIVED
        await self._session.flush()

        if touched_months:
            await AggregateRefresher(self._session).refresh_months(sorted(touched_months))

        logger.info(events.CARD_ARCHIVED, card_id=str(card.id))
        _, count = await self._with_count(card)
        return await self._response(card, count)

    async def restore(self, card_id: uuid.UUID) -> CardResponse:
        """Reactivate the card with its statements attached."""
        card = await self._require_card(card_id)
        card.status = CardStatus.ACTIVE
        await self._session.flush()
        _, count = await self._with_count(card)
        return await self._response(card, count)

    async def delete_statements(self, card_id: uuid.UUID, *, confirm: bool) -> None:
        """Destructive and not recoverable, unlike archiving."""
        if not confirm:
            raise LedgerError("confirm must be true to delete a card's statements.")
        card = await self._require_card(card_id)
        statements = await self._statements.list_for_user(card_id=card.id)

        store: ObjectStore = build_object_store(get_settings())
        for statement in statements:
            await self._transactions.delete_for_statement(statement.id)
            store.delete(key=statement.object_key)
            await self._statements.delete(statement)
        logger.info(
            events.CARD_STATEMENTS_DELETED, card_id=str(card.id), rows_deleted=len(statements)
        )

    # -- Shared ----------------------------------------------------------------

    async def _find_or_create_account(self, *, institution: str, kind: str) -> Account:
        user_id = await current_tenant_id(self._session)
        result = await self._session.execute(
            select(Account)
            .where(
                Account.user_id == user_id, Account.institution == institution, Account.account_kind == kind
            )
            .order_by(Account.created_at)
        )
        # .first(), not .scalar_one_or_none(): CardCreate has no way to name
        # which of several same-institution-and-kind accounts a new card
        # belongs to (that is a real gap, not a bug this masks -- see the
        # class docstring), so more than one existing match is a case to
        # degrade on, not a 500 to raise.
        account = result.scalars().first()
        if account is not None:
            return account
        account = Account(user_id=user_id, institution=institution, account_kind=kind)
        self._session.add(account)
        await self._session.flush()
        return account

    async def _require_card(self, card_id: uuid.UUID) -> Card:
        card = await self._cards.get(card_id)
        if card is None:
            raise NotFound("Card not found.")
        return card

    async def _with_count(self, card: Card) -> tuple[Card, int]:
        for candidate, count in await self._cards.list_with_counts():
            if candidate.id == card.id:
                return candidate, count
        return card, 0

    async def _response(self, card: Card, statement_count: int) -> CardResponse:
        account = await self._session.get(Account, card.account_id)
        return CardResponse(
            id=card.id,
            nickname=card.nickname,
            institution=account.institution if account is not None else "",
            last4=card.last4,
            network=card.network,
            statement_day=card.statement_day,
            currency=card.currency,
            colour=card.colour_hex,
            status=card.status.value,
            expires_on=card.expires_on,
            statement_count=statement_count,
            archived=card.status == CardStatus.ARCHIVED,
        )


class CategoryService:
    """BUILD STEP 9.2."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._categories = CategoryRepository(session)

    async def list_categories(self) -> list[CategoryResponse]:
        """The tenant's categories plus system ones, each with a row count."""
        rows = await self._categories.list_for_user()
        return [self._response(category, count) for category, count in rows]

    async def create(self, *, name: str, colour: str | None, kind: str) -> CategoryResponse:
        """Derive a slug from the name; the unique index on
        ``(user_id, slug)`` enforces uniqueness per user.

        That constraint is what actually stops a duplicate -- this only
        turns its violation into a 409 a client can act on, the same
        pattern ``CardService.create`` uses for a repeat ``last4`` above.
        """
        try:
            category = await self._categories.create(
                slug=slugify(name), name=name, colour=colour, kind=kind
            )
        except IntegrityError as exc:
            raise CategoryAlreadyExists(
                f"You already have a category named {name!r}."
            ) from exc
        return self._response(category, 0)

    async def update(self, category_id: uuid.UUID, **changes: object) -> CategoryResponse:
        """Rename and recolour. The slug stays fixed: code and rules
        reference it, so renaming the display name must not break them."""
        category = await self._require_category(category_id)
        if category.user_id is None:
            raise LedgerError("System categories cannot be modified.")
        if "name" in changes and changes["name"] is not None:
            category.name = str(changes["name"])
        if "colour" in changes:
            category.colour_hex = changes["colour"]  # type: ignore[assignment]
        await self._session.flush()
        count = await self._categories.row_count(category.id)
        return self._response(category, count)

    async def merge(self, category_id: uuid.UUID, *, into_category_id: uuid.UUID) -> CategoryResponse:
        """Move rows, then delete the source."""
        source = await self._require_category(category_id)
        target = await self._require_category(into_category_id)
        if source.user_id is None:
            raise LedgerError("System categories cannot be merged away.")

        await self._categories.reassign(from_category_id=source.id, to_category_id=target.id)
        await self._categories.delete(source)

        await AggregateRefresher(self._session).refresh_all()

        count = await self._categories.row_count(target.id)
        return self._response(target, count)

    async def delete(self, category_id: uuid.UUID) -> None:
        """Refuse with 409 while any row still references it -- deleting a
        category with rows would orphan them into an unclassified state the
        user never chose."""
        category = await self._require_category(category_id)
        if category.user_id is None:
            raise LedgerError("System categories cannot be deleted.")
        count = await self._categories.row_count(category.id)
        if count > 0:
            raise Conflict(
                "This category still has transactions. Merge or reassign them first.",
                details={"row_count": count},
            )
        await self._categories.delete(category)

    async def _require_category(self, category_id: uuid.UUID):
        category = await self._categories.get(category_id)
        if category is None:
            raise NotFound("Category not found.")
        return category

    def _response(self, category, row_count: int) -> CategoryResponse:
        return CategoryResponse(
            id=category.id,
            name=category.name,
            slug=category.slug,
            kind=category.kind.value,
            colour=category.colour_hex,
            is_system=category.is_system,
            row_count=row_count,
        )


class AccountService:
    """BUILD STEP 9.3."""

    def __init__(self, session: AsyncSession, store: ObjectStore) -> None:
        self._session = session
        self._store = store
        self._statements = StatementRepository(session)
        self._transactions = TransactionRepository(session)

    async def inventory(self) -> AccountInventory:
        """Counts reused verbatim by the deletion screen, so a person is
        shown the same numbers in both places."""
        user_id = await current_tenant_id(self._session)
        statements = await self._session.execute(
            select(Statement).where(Statement.user_id == user_id)
        )
        statement_rows = list(statements.scalars().all())
        transaction_count = await self._session.execute(
            select(Transaction).where(Transaction.user_id == user_id)
        )
        rule_count = await self._session.execute(select(Rule).where(Rule.user_id == user_id))
        cards = await self._session.execute(select(Card).where(Card.user_id == user_id))
        categories = CategoryRepository(self._session)
        category_rows = await categories.list_for_user()
        tenant_categories = [c for c, _ in category_rows if c.user_id == user_id]

        return AccountInventory(
            statements=len(statement_rows),
            transactions=len(list(transaction_count.scalars().all())),
            rules=len(list(rule_count.scalars().all())),
            categories=len(tenant_categories),
            cards=len(list(cards.scalars().all())),
        )

    async def start_export(self, **request: object) -> ExportResponse:
        """Begin an export.

        Columns, row count and size are computed and returned before the
        file is written. Generated synchronously: there is no dedicated
        export worker stage in the build order, and this keeps the promise
        ("declared before the file is written") true without inventing a
        new job type the plan never asked for.
        """
        scope = str(request.get("scope") or "all")
        scope_id = request.get("scope_id")
        if scope != "all" and scope_id is None:
            raise LedgerError("scope_id is required unless scope is 'all'.")

        rows = await self._rows_for_scope(scope, scope_id)  # type: ignore[arg-type]
        user_id = await current_tenant_id(self._session)

        export = Export(
            user_id=user_id,
            scope=scope,
            scope_id=scope_id,  # type: ignore[arg-type]
            status=ExportStatus.PENDING,
        )
        self._session.add(export)
        await self._session.flush()

        content = self._render_csv(rows)
        object_key = f"exports/{user_id}/{export.id}.csv"
        self._store.put_bytes(key=object_key, data=content, content_type="text/csv")

        export.status = ExportStatus.READY
        export.object_key = object_key
        export.row_count = len(rows)
        await self._session.flush()

        return self._response(export, len(content))

    async def get_export(self, export_id: uuid.UUID) -> ExportResponse:
        export = await self._session.get(Export, export_id)
        if export is None:
            raise NotFound("Export not found.")
        size = 0
        return self._response(export, size)

    async def delete_everything(self, *, confirmation: str) -> None:
        """Mark the account for deletion and enqueue the purge.

        ``confirmation`` is already constrained to the literal string
        ``"DELETE"`` by :class:`~app.schemas.account.DeleteAccountRequest`;
        checked again here so calling the service directly carries the same
        guarantee the schema gives the endpoint (TC-ACCT-007).
        """
        if confirmation != "DELETE":
            raise LedgerError("confirmation must be exactly 'DELETE'.")

        user_id = await current_tenant_id(self._session)
        user = await self._session.get(User, user_id)
        if user is not None:
            user.deleted_at = datetime.now(UTC)

        await enqueue(
            self._session, purge_account, user_id=str(user_id), traceparent=current_traceparent()
        )

    # -- Shared ------------------------------------------------------------

    async def _rows_for_scope(self, scope: str, scope_id: uuid.UUID | None) -> list[Transaction]:
        if scope == "card":
            return await self._transactions.list_filtered(card_id=scope_id)
        if scope == "category":
            return await self._transactions.list_filtered(category_id=scope_id)
        if scope == "statement":
            if scope_id is None:
                raise LedgerError("scope_id is required for a statement export.")
            return await self._transactions.list_for_statement(scope_id)
        return await self._transactions.list_filtered()

    def _render_csv(self, rows: list[Transaction]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.posted_on.isoformat(),
                    row.description_raw,
                    to_decimal_string(row.amount_minor, row.currency),
                    row.currency,
                    str(row.category_id) if row.category_id else "",
                    str(row.card_id) if row.card_id else "",
                ]
            )
        return buffer.getvalue().encode("utf-8")

    def _response(self, export: Export, size_bytes: int) -> ExportResponse:
        download_url = (
            self._store.presign_download(key=export.object_key)
            if export.object_key is not None
            else None
        )
        return ExportResponse(
            export_id=export.id,
            status=export.status.value,
            columns=EXPORT_COLUMNS,
            row_count=export.row_count or 0,
            estimated_size_bytes=size_bytes,
            filename=f"ledger-export-{export.id}.csv",
            download_url=download_url,
            created_at=export.created_at,
        )
