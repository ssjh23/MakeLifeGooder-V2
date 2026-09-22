"""Add descriptor_key_overrides: per-tenant raw-descriptor split records.

Revision ID: 0005_descriptor_key_overrides
Revises: 0004_seed_categories
Create Date: 2026-09-21

Lets a person eject one exact raw statement line from a wrongly-merged
descriptor group into its own key, retroactively and permanently, without
touching normalise() or any other row sharing the group. See
app/db/models.py's DescriptorKeyOverride and app/classify/pipeline.py's
classify() for how it's consulted.

RLS policy text is copied from 0001_baseline's PREDICATE rather than
imported: migrations are frozen snapshots, and importing a "current" constant
from application code would make this file's meaning drift if that constant
ever changed for an unrelated reason.

No explicit GRANT is needed: 0001_baseline already runs `ALTER DEFAULT
PRIVILEGES IN SCHEMA public GRANT ... ON TABLES TO ledger_app`, which covers
every table created afterward.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_descriptor_key_overrides"
down_revision: str | None = "0004_seed_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "descriptor_key_overrides"
PREDICATE = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description_raw", sa.Text(), nullable=False),
        sa.Column("descriptor_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "description_raw",
            name="uq_descriptor_key_overrides_user_id_description_raw",
        ),
    )
    op.create_index(
        "ix_descriptor_key_overrides_user_id_descriptor_key",
        TABLE,
        ["user_id", "descriptor_key"],
    )

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.drop_index("ix_descriptor_key_overrides_user_id_descriptor_key", table_name=TABLE)
    op.drop_table(TABLE)
