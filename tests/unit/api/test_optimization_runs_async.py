from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fleet_rlm.api.routers.optimization import orchestration
from fleet_rlm.api.routers.optimization.background import run_optimization_background
from fleet_rlm.api.schemas.optimization import GEPAOptimizationRequest


class _BackgroundTaskCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def add_task(self, fn: object, *args: object, **kwargs: object) -> None:
        self.calls.append((fn, args, kwargs))


@pytest.mark.asyncio
async def test_create_async_run_and_enqueue_registers_background_task(
    monkeypatch,
    tmp_path,
) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text('{"example": true}\n', encoding="utf-8")
    request = GEPAOptimizationRequest.model_validate(
        {
            "dataset_path": str(dataset_path),
            "skill_name": "optimization",
        }
    )
    resolved_dataset_id = uuid.uuid4()
    prepared = orchestration.PreparedOptimizationRequest(
        program_spec="skill:optimization",
        dataset_path=dataset_path,
        dataset_ref=str(dataset_path),
        dataset_id=resolved_dataset_id,
        output_path=None,
        skill_path=None,
        trace_bundle_paths=[],
        reflection_lm_config=None,
        task_lm_config=None,
        search_config={},
        max_full_evals=None,
        run_spec=None,
        run_fingerprint=None,
        timeout_seconds=900,
    )

    async def fake_prepare(**kwargs):
        _ = kwargs
        return prepared

    monkeypatch.setattr(orchestration, "prepare_optimization_request", fake_prepare)

    run_uuid = uuid.uuid4()
    persistence = SimpleNamespace(
        create_optimization_run=AsyncMock(return_value=SimpleNamespace(id=run_uuid)),
    )
    persisted_identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    background_tasks = _BackgroundTaskCapture()

    response = await orchestration.create_async_run_and_enqueue(
        request=request,
        background_tasks=background_tasks,
        persistence=persistence,
        persistence_deps=SimpleNamespace(),
        persisted_identity=persisted_identity,
    )

    assert response.run_id == str(run_uuid)
    assert response.status == "running"
    assert len(background_tasks.calls) == 1
    create_request = persistence.create_optimization_run.await_args.args[0]
    assert create_request.dataset_id == resolved_dataset_id
    task_fn, task_args, task_kwargs = background_tasks.calls[0]
    assert task_fn is run_optimization_background
    assert task_kwargs["run_id"] == str(run_uuid)
    assert task_kwargs["program_spec"] == "skill:optimization"
    assert task_kwargs["dataset_path"] == dataset_path
