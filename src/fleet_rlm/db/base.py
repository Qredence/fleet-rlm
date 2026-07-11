"""Shared SQLAlchemy database base helpers."""

from __future__ import annotations

import enum

from sqlalchemy import Enum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _pg_enum(enum_cls: type[enum.Enum], *, name: str) -> Enum:
    """Bind Python enums to existing Postgres enum values (not member names)."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
        native_enum=True,
    )
