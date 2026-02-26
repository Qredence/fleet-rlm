"""Session service for executing business logic and database CRUD."""

from typing import Optional, Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from fleet_rlm.server.legacy_models import Session
from fleet_rlm.server.schemas.session import SessionCreate, SessionUpdate


class SessionService:
    """Service layer for Session operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_session(self, session_create: SessionCreate) -> Session:
        """Create a new Session."""
        db_session = Session(**session_create.model_dump())
        self._session.add(db_session)
        await self._session.commit()
        await self._session.refresh(db_session)
        return db_session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Fetch a specific Session by ID."""
        return await self._session.get(Session, session_id)

    async def get_sessions(self, skip: int = 0, limit: int = 100) -> Sequence[Session]:
        """List sessions with pagination."""
        statement = select(Session).offset(skip).limit(limit)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def update_session(
        self, session_id: str, session_update: SessionUpdate
    ) -> Optional[Session]:
        """Update an existing Session."""
        db_session = await self.get_session(session_id)
        if not db_session:
            return None

        update_data = session_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_session, key, value)

        self._session.add(db_session)
        await self._session.commit()
        await self._session.refresh(db_session)
        return db_session

    async def delete_session(self, session_id: str) -> bool:
        """Delete an existing Session."""
        db_session = await self.get_session(session_id)
        if not db_session:
            return False

        await self._session.delete(db_session)
        await self._session.commit()
        return True
