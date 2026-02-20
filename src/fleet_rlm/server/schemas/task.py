"""Task schemas for the FastAPI layer."""

from datetime import datetime
from typing import Optional

from .base import CamelCaseModel


class TaskCreate(CamelCaseModel):
    """Schema for creating a new Task."""

    objective: str
    session_id: Optional[str] = None


class TaskUpdate(CamelCaseModel):
    """Schema for updating an existing Task."""

    status: Optional[str] = None
    result: Optional[str] = None


class TaskResponse(CamelCaseModel):
    """Schema for returning a Task to the frontend."""

    id: str
    session_id: Optional[str]
    objective: str
    status: str
    result: Optional[str]
    created_at: datetime
    updated_at: datetime
