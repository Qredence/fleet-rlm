"""Session schemas for the FastAPI layer."""

from datetime import datetime
from typing import Optional

from .base import CamelCaseModel


class SessionCreate(CamelCaseModel):
    """Schema for creating a new Session."""

    title: Optional[str] = "New Session"


class SessionUpdate(CamelCaseModel):
    """Schema for updating an existing Session."""

    title: Optional[str] = None
    status: Optional[str] = None


class SessionResponse(CamelCaseModel):
    """Schema for returning a Session to the frontend."""

    id: str
    title: str
    status: str
    created_at: datetime
