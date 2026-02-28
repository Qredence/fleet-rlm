"""Router for Task management."""

from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.server.deps import TaskServiceDep, require_legacy_task_routes
from fleet_rlm.server.schemas.task import TaskCreate, TaskResponse, TaskUpdate

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_legacy_task_routes)],
)


@router.post("", response_model=TaskResponse, status_code=201, deprecated=True)
async def create_task(
    task: TaskCreate,
    service: TaskServiceDep,
) -> TaskResponse:
    """Create a new task."""
    created = await service.create_task(task)
    return TaskResponse.model_validate(created)


@router.get("", response_model=Sequence[TaskResponse], deprecated=True)
async def list_tasks(
    service: TaskServiceDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = 100,
) -> Sequence[TaskResponse]:
    """List all tasks."""
    tasks = await service.get_tasks(skip=skip, limit=limit)
    return [TaskResponse.model_validate(item) for item in tasks]


@router.get("/{task_id}", response_model=TaskResponse, deprecated=True)
async def get_task(
    task_id: str,
    service: TaskServiceDep,
) -> TaskResponse:
    """Get a specific task by ID."""
    task = await service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}", response_model=TaskResponse, deprecated=True)
async def update_task(
    task_id: str,
    update_data: TaskUpdate,
    service: TaskServiceDep,
) -> TaskResponse:
    """Update a specific task."""
    task = await service.update_task(task_id, update_data)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=204, deprecated=True)
async def delete_task(
    task_id: str,
    service: TaskServiceDep,
) -> None:
    """Delete a specific task."""
    success = await service.delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return None
