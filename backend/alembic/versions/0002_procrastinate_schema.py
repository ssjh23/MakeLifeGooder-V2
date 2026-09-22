"""procrastinate's own schema.

Revision ID: 0002_procrastinate_schema
Revises: 0001_baseline
Create Date: 2026-09-14

The queue lives in the same Postgres as the data (ADR-004), which only holds
if procrastinate's tables actually exist there. Nothing installed them until
now: the worker's ``App`` (``app/worker/app.py``) only *uses* the schema, and
``procrastinate schema --apply`` is a separate, manual step nobody had wired
into ``uv run alembic upgrade head``. Folding it into this migration means the
one documented setup command is still the only one required.

``SchemaManager.get_schema()`` is procrastinate's own DDL, read from the
installed package rather than copied by hand, so this migration stays in step
with whatever version of the library the worker was built against instead of
silently drifting from it.

Table privileges need no extra grant: ``0001_baseline``'s ``ALTER DEFAULT
PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO
ledger_app`` already covers a table the moment it exists, this migration's
tables included. Sequences are a second, easy-to-miss case: that same
``ALTER DEFAULT PRIVILEGES`` clause was scoped to tables only, and
``procrastinate_jobs.id`` is ``bigserial``, so inserting a row means calling
``nextval()`` on ``procrastinate_jobs_id_seq`` under ``ledger_app``'s own
privileges (the function has no ``SECURITY DEFINER``). Without an explicit
grant that insert fails with ``permission denied for sequence``, which is
exactly the shape of error that looks like a missing table grant and is not.
"""

from __future__ import annotations

from collections.abc import Sequence

from procrastinate.schema import SchemaManager

from alembic import op

revision: str = "0002_procrastinate_schema"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every table procrastinate 3.9's schema.sql creates, CASCADE so each drop
#: also takes its triggers and indexes with it.
_TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)
_TYPES = (
    "procrastinate_job_to_defer_v1",
    "procrastinate_job_event_type",
    "procrastinate_job_status",
)


APP_ROLE = "ledger_app"


def upgrade() -> None:
    op.execute(SchemaManager.get_schema())
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Functions by hand-copied signature is exactly the kind of thing that
    # drifts from the library silently: ``DROP FUNCTION IF EXISTS`` matches on
    # the full signature, so a copied argument list that is wrong by one
    # parameter does not error, it just leaves the real function behind for
    # the next ``upgrade`` to collide with. Looking the signatures up from the
    # catalog by name prefix instead means there is nothing here to fall out
    # of step with whatever procrastinate actually installed.
    op.execute(
        "DO $$ DECLARE r record; BEGIN "
        "FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate\\_%' "
        "LOOP EXECUTE 'DROP FUNCTION IF EXISTS ' || r.sig || ' CASCADE'; "
        "END LOOP; END $$"
    )

    for type_ in _TYPES:
        op.execute(f"DROP TYPE IF EXISTS {type_} CASCADE")
