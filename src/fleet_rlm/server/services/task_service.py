"""Task service for executing business logic and database CRUD."""

from typing import Optional, Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from fleet_rlm.server.models import Task
from fleet_rlm.server.schemas.task import TaskCreate, TaskUpdate


class TaskService:
    """Service layer for Task operations."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_task(self, task_create: TaskCreate) -> Task:
        """Create a new Task."""
        db_task = Task(**task_create.model_dump())
        self._session.add(db_task)
        await self._session.commit()
        await self._session.refresh(db_task)
        return db_task

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Fetch a specific Task by ID."""
        return await self._session.get(Task, task_id)

    async def get_tasks(self, skip: int = 0, limit: int = 100) -> Sequence[Task]:
        """List tasks with pagination."""
        statement = select(Task).offset(skip).limit(limit)
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def update_task(
        self, task_id: str, task_update: TaskUpdate
    ) -> Optional[Task]:
        """Update an existing Task."""
        db_task = await self.get_task(task_id)
        if not db_task:
            return None

        update_data = task_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_task, key, value)

        self._session.add(db_task)
        await self._session.commit()
        await self._session.refresh(db_task)
        return db_task

    async def delete_task(self, task_id: str) -> bool:
        """Delete an existing Task."""
        db_task = await self.get_task(task_id)
        if not db_task:
            return False

        await self._session.delete(db_task)
        await self._session.commit()
        return True
