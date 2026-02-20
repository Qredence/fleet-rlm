"""Router for Task management."""

from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm.server.dependencies import get_db
from fleet_rlm.server.schemas.task import TaskCreate, TaskResponse, TaskUpdate
from fleet_rlm.server.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(db=Depends(get_db)) -> TaskService:
    return TaskService(db)


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    task: TaskCreate, service: TaskService = Depends(get_task_service)
):
    """Create a new task."""
    return await service.create_task(task)


@router.get("", response_model=Sequence[TaskResponse])
async def list_tasks(
    skip: int = 0, limit: int = 100, service: TaskService = Depends(get_task_service)
):
    """List all tasks."""
    return await service.get_tasks(skip=skip, limit=limit)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, service: TaskService = Depends(get_task_service)):
    """Get a specific task by ID."""
    task = await service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    update_data: TaskUpdate,
    service: TaskService = Depends(get_task_service),
):
    """Update a specific task."""
    task = await service.update_task(task_id, update_data)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str, service: TaskService = Depends(get_task_service)):
    """Delete a specific task."""
    success = await service.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
