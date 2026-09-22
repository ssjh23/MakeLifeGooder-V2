"""Add merchants.name_key: a normalised identity key, and consolidate any
existing near-duplicates it reveals.

Revision ID: 0006_merchant_name_key
Revises: 0005_descriptor_key_overrides
Create Date: 2026-09-21

The LLM classification rung (app/classify/cascade.py's CascadeResolver._llm)
checks for an existing merchant by canonical_name before minting a new row,
but that check was exact case-insensitive equality only -- "Old Tea Hut" and
"OLD-TEA-HUT" passed as different merchants, silently splitting one
merchant's history across two ids. name_key is the fix: a slugified,
punctuation/casing-insensitive identity key, unique the same way
merchant_aliases.descriptor_key already is.

Backfilling a NOT NULL unique column onto a table that may already hold rows
means handling exactly the collision this migration exists to prevent: two
current merchants whose canonical names slugify to the same key. Rather than
fail the constraint, this migration consolidates them first -- keeping the
oldest row and repointing every foreign key that points at merchant_id
(transactions, merchant_aliases, merchant_overrides -- confirmed the only
three) from the loser to the survivor, then dropping the loser.

The slug regex is copied inline rather than imported from
app.services._slug: migrations are frozen snapshots, the same reasoning
0005 already used for not importing PREDICATE from application code.

``transactions`` and ``merchant_overrides`` carry FORCE ROW LEVEL SECURITY,
which -- per Postgres, and per the precedent already set in
0004_seed_categories.py -- applies even to the owning migrator role, not
only ``ledger_app``. A cross-tenant repoint has no single ``app.user_id`` to
set, so this migration drops FORCE for its own duration on exactly those two
tables and restores it immediately after, the same pattern 0004 uses for
``categories``. ``merchants`` and ``merchant_aliases`` carry no RLS at all
(cross-tenant by design, ADR-006), so they need no such toggle.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0006_merchant_name_key"
down_revision: str | None = "0005_descriptor_key_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "merchant"


def upgrade() -> None:
    connection = op.get_bind()

    op.add_column("merchants", sa.Column("name_key", sa.Text(), nullable=True))

    rows = connection.execute(
        sa.text("SELECT id, canonical_name, created_at FROM merchants")
    ).all()

    groups: dict[str, list[sa.Row[Any]]] = defaultdict(list)
    for row in rows:
        groups[_slugify(row.canonical_name)].append(row)

    op.execute("ALTER TABLE transactions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE merchant_overrides NO FORCE ROW LEVEL SECURITY")
    try:
        for slug, group in groups.items():
            group.sort(key=lambda row: (row.created_at, str(row.id)))
            survivor = group[0]
            for loser in group[1:]:
                for table in ("transactions", "merchant_aliases", "merchant_overrides"):
                    connection.execute(
                        sa.text(
                            f"UPDATE {table} SET merchant_id = :survivor_id "
                            "WHERE merchant_id = :loser_id"
                        ),
                        {"survivor_id": survivor.id, "loser_id": loser.id},
                    )
                connection.execute(
                    sa.text("DELETE FROM merchants WHERE id = :loser_id"),
                    {"loser_id": loser.id},
                )
            connection.execute(
                sa.text("UPDATE merchants SET name_key = :slug WHERE id = :id"),
                {"slug": slug, "id": survivor.id},
            )
    finally:
        op.execute("ALTER TABLE transactions FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE merchant_overrides FORCE ROW LEVEL SECURITY")

    op.alter_column("merchants", "name_key", nullable=False)
    op.create_unique_constraint("uq_merchants_name_key", "merchants", ["name_key"])


def downgrade() -> None:
    op.drop_constraint("uq_merchants_name_key", "merchants", type_="unique")
    op.drop_column("merchants", "name_key")
