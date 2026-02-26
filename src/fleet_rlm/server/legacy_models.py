"""Database models using SQLModel."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def generate_uuid() -> str:
    """Generate a UUID4 string."""
    return str(uuid.uuid4())


def get_utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


class Session(SQLModel, table=True):
    """Database model for a user session."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    title: str = Field(default="New Session")
    status: str = Field(default="active")
    created_at: datetime = Field(default_factory=get_utc_now)


class Task(SQLModel, table=True):
    """Database model for a task orchestration."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    session_id: Optional[str] = Field(default=None, foreign_key="session.id")
    objective: str
    status: str = Field(default="pending")
    result: Optional[str] = None
    created_at: datetime = Field(default_factory=get_utc_now)
    updated_at: datetime = Field(default_factory=get_utc_now)
