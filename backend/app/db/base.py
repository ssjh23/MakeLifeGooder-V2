"""Declarative base and shared column conventions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import MetaData, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Explicit naming so Alembic autogenerate produces stable, readable names and
#: a constraint can be dropped by name in a later migration.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def created_at() -> Mapped[datetime]:
    return mapped_column(server_default=text("now()"), nullable=False)
