"""Scope uq_statements_card_id_period to committed statements.

Revision ID: 0003_card_period_committed
Revises: 0002_procrastinate_schema
Create Date: 2026-09-14

The original partial index (``0001_baseline``, generated from the model)
fired as soon as extraction wrote ``period_start``/``period_end`` on a
statement, whether or not it had ever been committed. Screen 03d exists so a
person can choose ``replace``, ``keep_both`` or ``cancel`` when a second
upload collides with a card and period -- which requires the second,
still-uncommitted draft to be able to reach ``needs_review`` at all. Under the
original index it never could: extraction's own write of its period fields
raised the unique violation directly, so the 409 the commit gate is supposed
to raise (BUILD STEP 4.4) was unreachable.

Scoped to ``status = 'ready'`` rather than ``committed_at IS NOT NULL``: both
transition together in ``commit()``, but ``status`` is what
``tests/integration/test_statement_repository.py``'s own
``TC_DUP_007``/``TestFindByCardAndPeriod`` fixtures already seed directly to
exercise this constraint, so matching it is what keeps those tests meaningful
rather than merely coincidentally green.

See ``app/db/models.py``'s ``Statement.__table_args__`` for the same
reasoning next to the corrected definition.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_card_period_committed"
down_revision: str | None = "0002_procrastinate_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_WHERE = "card_id IS NOT NULL AND period_start IS NOT NULL"
_NEW_WHERE = "card_id IS NOT NULL AND period_start IS NOT NULL AND status = 'ready'"


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_statements_card_id_period")
    op.execute(
        "CREATE UNIQUE INDEX uq_statements_card_id_period ON statements "
        f"(card_id, period_start, period_end) WHERE ({_NEW_WHERE})"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_statements_card_id_period")
    op.execute(
        "CREATE UNIQUE INDEX uq_statements_card_id_period ON statements "
        f"(card_id, period_start, period_end) WHERE ({_OLD_WHERE})"
    )
