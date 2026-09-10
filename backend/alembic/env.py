"""Alembic environment.

Migrations run as the owning role over a synchronous driver. The application
role must never appear here: it owns nothing, which is exactly what makes row
level security apply to it, and that same property means it cannot create
tables.

Which database is decided by an environment variable rather than alembic.ini,
so the test database is migrated by pointing at a different URL rather than by
editing a committed file.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base
from app.db import models  # noqa: F401  - imported so metadata is populated

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("DATABASE_MIGRATOR_URL")
    if not url:
        raise RuntimeError(
            "Set DATABASE_MIGRATOR_URL (or ALEMBIC_DATABASE_URL to override). "
            "It must name the owning role, not ledger_app."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
