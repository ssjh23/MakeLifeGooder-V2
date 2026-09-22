"""Add transactions.excluded_reason: why excluded_at was set.

Revision ID: 0007_row_exclusion_reason
Revises: 0006_merchant_name_key
Create Date: 2026-09-22

Screen 03b's "Check the rows" lets a user Skip or Delete a row before a
statement commits. Skip already only ever set excluded_at -- the same
undoable "not counted, stays on the record" flag duplicate-removal (screen
05) shares -- while Delete used to be a genuine hard delete with nothing
left to restore from. Making Delete undoable too means routing it through
excluded_at as well, but the two need to be told apart for the UI's
Skip/Delete-vs-Undo labelling. Deliberately a plain string, not a Postgres
enum: nothing downstream ever filters or joins on it, only the API's
_row_response reads it back for display, so a new enum type would be
ceremony this column has no use for.

Backfilling every pre-existing excluded_at row as "skip" is safe: delete
never touched excluded_at before this migration (it hard-deleted, leaving
no row to backfill), and duplicate-removal only ever applies to an
already-committed statement -- a different screen and a different service
(ReviewService, not StatementService) -- while excluded_reason is only ever
read back by StatementService._row_response, which only ever serves a
statement's pre-commit rows.

``transactions`` carries FORCE ROW LEVEL SECURITY (see 0006's own note),
which applies even to the owning migrator role, so the backfill UPDATE
needs the same NO FORCE / FORCE toggle 0006 used.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_row_exclusion_reason"
down_revision: str | None = "0006_merchant_name_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("excluded_reason", sa.Text(), nullable=True))

    op.execute("ALTER TABLE transactions NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute("UPDATE transactions SET excluded_reason = 'skip' WHERE excluded_at IS NOT NULL")
    finally:
        op.execute("ALTER TABLE transactions FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("transactions", "excluded_reason")
