"""Seed the system category taxonomy.

Revision ID: 0004_seed_categories
Revises: 0003_card_period_committed
Create Date: 2026-09-17

BUILD STEP 5.5 needs somewhere for an LLM-suggested ``category_slug`` to
resolve to: :class:`~app.classify.llm.base.MerchantSuggestion` names a slug
from a fixed taxonomy (the adapter protocol requires it -- "return categories
from the known taxonomy rather than inventing names"), and until now no row
existed for it to resolve against. These eight match
``app.classify.llm.fake.FAKE_CATEGORIES``, the taxonomy every cascade test
runs against, so the fake adapter and the real one classify into the same
rows.

``is_system = true``, ``user_id = NULL``: visible to every tenant, the same
shared-taxonomy reasoning as the merchant alias cache. A user can still create
their own categories (BUILD STEP 9.2); these are only the floor every account
starts with.

``categories``' own policy (``0001_baseline``) reads system rows through
``CATEGORY_PREDICATE`` but writes them through the plain tenant ``PREDICATE``,
which a NULL ``user_id`` never satisfies -- deliberately, so a tenant session
cannot create a system-visible row. ``FORCE ROW LEVEL SECURITY`` means that
bars the owning migrator role too, not only ``ledger_app``, so this seed drops
FORCE for the duration of its own insert and restores it immediately after,
rather than weakening the policy that keeps every other session out.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_seed_categories"
down_revision: str | None = "0003_card_period_committed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (slug, display name) -- mirrors app.classify.llm.fake.FAKE_CATEGORIES.
SYSTEM_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("food-drink", "Food & Drink"),
    ("transport", "Transport"),
    ("groceries", "Groceries"),
    ("shopping", "Shopping"),
    ("utilities", "Utilities"),
    ("entertainment", "Entertainment"),
    ("health", "Health"),
    ("other", "Other"),
)


def upgrade() -> None:
    op.execute("ALTER TABLE categories NO FORCE ROW LEVEL SECURITY")
    try:
        for slug, name in SYSTEM_CATEGORIES:
            op.execute(
                "INSERT INTO categories (slug, name, kind, is_system, user_id) "
                f"VALUES ('{slug}', '{name}', 'expense', true, NULL) "
                "ON CONFLICT DO NOTHING"
            )
    finally:
        op.execute("ALTER TABLE categories FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    slugs = ", ".join(f"'{slug}'" for slug, _ in SYSTEM_CATEGORIES)
    op.execute(f"DELETE FROM categories WHERE user_id IS NULL AND slug IN ({slugs})")
