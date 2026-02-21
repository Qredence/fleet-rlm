import logging
from datetime import datetime, timezone
from typing import Optional, List
from sqlmodel import Field, SQLModel
from sqlalchemy import DateTime
from pgvector.sqlalchemy import Vector

logger = logging.getLogger(__name__)


class TaxonomyNode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str
    parent_id: Optional[int] = Field(default=None, foreign_key="taxonomynode.id")
    embedding: Optional[List[float]] = Field(sa_type=Vector(1536))  # type: ignore
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class AgentMemory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    memory_type: str  # "observation", "plan", "execution", "reflection"
    content: str
    taxonomy_node_id: Optional[int] = Field(default=None, foreign_key="taxonomynode.id")
    embedding: Optional[List[float]] = Field(sa_type=Vector(1536))  # type: ignore
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


# End of models
