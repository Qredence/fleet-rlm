"""Background-task tests for the evaluation endpoints.

These tests verify the background-task conversion of POST /api/v1/evaluations
(VAL-SEC-009, VAL-SEC-010, VAL-SEC-011):

- VAL-SEC-009: POST returns immediately with run_id and status="pending",
  and does not block until the evaluation completes.
- VAL-SEC-010: GET /api/v1/evaluations/{run_id} polls for results and never
  blocks; the status transitions pending -> running -> completed.
- VAL-SEC-011: while an evaluation is running, other requests are still
  served (the event loop is not blocked).

The non-blocking assertions use direct ``asyncio`` service-level calls (per
the validation contract: "a unit test using asyncio that spawns the eval task
and asserts a concurrent await call completes without blocking is acceptable
evidence"). The POST-returns-pending assertion uses the FastAPI TestClient.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Iterator
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import (
    require_http_identity,
    resolve_persisted_identity,
)
from fleet_rlm.api.runtime_services import evaluations as evaluation_service
from fleet_rlm.api.runtime_services.evaluations import (
    _RunState,
    _store_insert,
    get_evaluation_report,
    start_evaluation_run,
)
from fleet_rlm.api.schemas.evaluations import EvaluationRequest
from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.quality.eval.report import EvaluationReport

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _stub_identity() -> object:
    from fleet_rlm.api.auth import NormalizedIdentity

    return NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )


def _stub_persisted_identity() -> IdentityUpsertResult:
    import uuid as _uuid

    tenant_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "tenant-a")
    user_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "user-a")
    return IdentityUpsertResult(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=tenant_id,
    )


@pytest.fixture
def evaluations_client(no_db_app) -> Iterator[TestClient]:
    """Client with auth dependencies stubbed so requests reach the router."""
    app = no_db_app
    app.dependency_overrides[require_http_identity] = _stub_identity  # type: ignore[assignment]
    app.dependency_overrides[resolve_persisted_identity] = _stub_persisted_identity  # type: ignore[assignment]
    evaluation_service._EVALUATION_STORE.clear()
    with TestClient(app) as client:
        # config_deps is populated by the lifespan startup handler, so it is
        # only accessible once the TestClient context has been entered.
        client.app.state.config_deps.config.auth_required = False
        yield client
    evaluation_service._EVALUATION_STORE.clear()
    for task in list(evaluation_service._INFLIGHT_TASKS):
        task.cancel()


@pytest.fixture(autouse=True)
def _clear_store_between() -> Iterator[None]:
    """Ensure each test starts with an empty store and no inflight tasks."""
    evaluation_service._EVALUATION_STORE.clear()
    evaluation_service._INFLIGHT_TASKS.clear()
    yield
    for task in list(evaluation_service._INFLIGHT_TASKS):
        task.cancel()
    evaluation_service._EVALUATION_STORE.clear()
    evaluation_service._INFLIGHT_TASKS.clear()


def _make_report(run_id: str) -> EvaluationReport:
    return EvaluationReport(
        run_id=run_id,
        created_at="2026-01-01T00:00:00+00:00",
        filters={"trace_ids": None, "limit": None, "from_last_days": 1},
        per_trace=[],
        aggregates={"mean": {}, "median": {}},
    )


def _gated_run_evaluation(gate: threading.Event, run_id: str):
    """Return a run_evaluation replacement that blocks on ``gate`` until set.

    The gated wait runs inside a worker thread via ``asyncio.to_thread`` in
    the service, so it does not block the event loop. Tests release the gate
    to let the background task complete cleanly.
    """

    def _impl(*, trace_ids=None, limit=None, from_last_days=1) -> EvaluationReport:
        gate.wait(timeout=10.0)
        return _make_report(run_id)

    return _impl


# ---------------------------------------------------------------------------
# VAL-SEC-009: POST returns immediately with run_id and status=pending
# ---------------------------------------------------------------------------


def test_post_evaluations_returns_immediately_with_pending_status(
    evaluations_client: TestClient,
) -> None:
    """VAL-SEC-009: POST returns within ~1s with run_id (UUID) and status=pending.

    The evaluation is gated so it cannot complete until released. If POST
    blocked until completion, the response would hang. We assert it returns
    promptly with status="pending" and a UUID run_id.
    """
    gate = threading.Event()
    with patch.object(
        evaluation_service,
        "run_evaluation",
        side_effect=_gated_run_evaluation(gate, "11111111-2222-3333-4444-555555555555"),
    ):
        start = time.monotonic()
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={"from_last_days": 1},
        )
        elapsed = time.monotonic() - start

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "pending", body
        assert _UUID_RE.match(body["run_id"]) is not None, body["run_id"]
        # Must return well before the (gated) evaluation completes.
        assert elapsed < 1.0, f"POST took {elapsed:.2f}s, expected < 1.0s"
        UUID(body["run_id"])

        # Release the gate so the background task completes before exit.
        gate.set()
        # Drain the task on the TestClient loop by polling to completion.
        for _ in range(200):
            time.sleep(0.01)
            r = evaluations_client.get(f"/api/v1/evaluations/{body['run_id']}")
            if r.status_code == 200 and r.json().get("status") == "completed":
                break
        else:  # pragma: no cover - defensive
            pytest.fail("gated eval did not complete before TestClient exit")


def test_start_evaluation_run_returns_pending_and_schedules_task() -> None:
    """VAL-SEC-009: start_evaluation_run returns status=pending and schedules a task.

    Direct service-level test: the function must return immediately with
    status="pending" and a UUID run_id, and must call asyncio.create_task
    (not await run_evaluation).
    """
    gate = threading.Event()
    captured: list[object] = []

    async def _main() -> dict:
        loop = asyncio.get_running_loop()
        real_create_task = asyncio.create_task

        def _spy(coro, *args, **kwargs):
            captured.append(coro)
            return real_create_task(coro, *args, **kwargs)

        with (
            patch.object(asyncio, "create_task", side_effect=_spy),
            patch.object(
                evaluation_service,
                "run_evaluation",
                side_effect=_gated_run_evaluation(gate, "22222222-3333-4444-5555-666666666666"),
            ),
        ):
            request = EvaluationRequest(from_last_days=1)
            start = loop.time()
            response = await start_evaluation_run(request)
            elapsed = loop.time() - start

            assert response.status == "pending"
            assert _UUID_RE.match(response.run_id) is not None
            # Returned before the gated evaluation completed.
            assert elapsed < 0.5, f"start_evaluation_run took {elapsed:.2f}s"
            assert len(captured) >= 1, "asyncio.create_task was not called"

            # The run should be pending or running in the store.
            state = evaluation_service._EVALUATION_STORE[response.run_id]
            assert state.status in {"pending", "running"}

            # Release the gate and let the task finish to clean up.
            gate.set()
            # Yield control repeatedly so the background task can complete.
            for _ in range(500):
                await asyncio.sleep(0.001)
                state = evaluation_service._EVALUATION_STORE.get(response.run_id)
                if state is not None and state.status == "completed":
                    break
            assert state is not None and state.status == "completed"

        return {"run_id": response.run_id}

    result = asyncio.run(_main())
    assert _UUID_RE.match(result["run_id"]) is not None


# ---------------------------------------------------------------------------
# VAL-SEC-010: GET polls for results (pending/running -> completed)
# ---------------------------------------------------------------------------


def test_get_returns_pending_or_running_while_eval_in_progress() -> None:
    """VAL-SEC-010: GET while the eval is gated returns pending/running (not blocking).

    Direct service-level test: with the evaluation gated, get_evaluation_report
    must return promptly with status pending/running and must NOT block.
    """
    gate = threading.Event()

    async def _main() -> str:
        with patch.object(
            evaluation_service,
            "run_evaluation",
            side_effect=_gated_run_evaluation(gate, "33333333-4444-5555-6666-777777777777"),
        ):
            request = EvaluationRequest(from_last_days=1)
            response = await start_evaluation_run(request)

            # Immediately poll: should be pending or running, and not block.
            loop = asyncio.get_running_loop()
            start = loop.time()
            report = await get_evaluation_report(response.run_id)
            elapsed = loop.time() - start

            assert report.status in {"pending", "running"}, report.status
            # Must not block waiting for the gated evaluation.
            assert elapsed < 0.5, f"GET took {elapsed:.2f}s while eval running"

            # Release the gate and let the task complete.
            gate.set()
            for _ in range(500):
                await asyncio.sleep(0.001)
                report = await get_evaluation_report(response.run_id)
                if report.status == "completed":
                    break
            assert report.status == "completed"
            return response.run_id

    run_id = asyncio.run(_main())
    assert _UUID_RE.match(run_id) is not None


def test_get_returns_completed_with_report_after_eval_finishes() -> None:
    """VAL-SEC-010: GET after the background task completes returns the full report."""
    gate = threading.Event()

    async def _main() -> EvaluationReport:
        with patch.object(
            evaluation_service,
            "run_evaluation",
            side_effect=_gated_run_evaluation(gate, "44444444-5555-6666-7777-888888888888"),
        ):
            request = EvaluationRequest(from_last_days=1)
            response = await start_evaluation_run(request)

            # While gated, the run is pending/running.
            mid = await get_evaluation_report(response.run_id)
            assert mid.status in {"pending", "running"}

            # Release the gate and poll until completed.
            gate.set()
            for _ in range(500):
                await asyncio.sleep(0.001)
                report = await get_evaluation_report(response.run_id)
                if report.status == "completed":
                    return report
            raise AssertionError("eval did not complete")

    report = asyncio.run(_main())
    assert report.status == "completed"
    assert report.run_id  # populated
    # Full report fields present (VAL-SEC-010).
    assert hasattr(report, "per_trace")
    assert hasattr(report, "aggregates")
    assert hasattr(report, "filters")


def test_get_returns_503_for_failed_run() -> None:
    """VAL-SEC-010 (failure path): a failed run surfaces 503 on GET."""
    gate = threading.Event()

    def _failing_run_evaluation(*, trace_ids=None, limit=None, from_last_days=1):
        gate.wait(timeout=10.0)
        raise RuntimeError("MLflow tracking server unreachable")

    async def _main() -> None:
        with patch.object(
            evaluation_service,
            "run_evaluation",
            side_effect=_failing_run_evaluation,
        ):
            request = EvaluationRequest(from_last_days=1)
            response = await start_evaluation_run(request)

            gate.set()
            # Poll until the run reports failed.
            from fastapi import HTTPException

            last_exc: HTTPException | None = None
            for _ in range(500):
                await asyncio.sleep(0.001)
                try:
                    await get_evaluation_report(response.run_id)
                except HTTPException as exc:
                    last_exc = exc
                    if exc.status_code == 503:
                        return
            raise AssertionError(f"failed run did not surface 503; last_exc={last_exc!r}")

    asyncio.run(_main())


# ---------------------------------------------------------------------------
# VAL-SEC-011: event loop not blocked during eval
# ---------------------------------------------------------------------------


def test_other_coroutines_run_while_eval_in_progress() -> None:
    """VAL-SEC-011: a concurrent coroutine completes while the eval is gated.

    Direct asyncio test: the background eval task is gated (running in a
    worker thread). While it is in progress, an unrelated ``await`` must
    complete promptly, proving the event loop is not blocked.
    """
    gate = threading.Event()

    async def _quick_task() -> str:
        # A tiny coroutine that should complete while the eval is gated.
        await asyncio.sleep(0.01)
        return "ok"

    async def _main() -> None:
        with patch.object(
            evaluation_service,
            "run_evaluation",
            side_effect=_gated_run_evaluation(gate, "55555555-6666-7777-8888-999999999999"),
        ):
            request = EvaluationRequest(from_last_days=1)
            response = await start_evaluation_run(request)

            loop = asyncio.get_running_loop()
            start = loop.time()
            # Run the quick task concurrently with the gated eval background task.
            quick_result = await _quick_task()
            elapsed = loop.time() - start

            assert quick_result == "ok"
            # The quick task must complete in well under the gated eval's runtime.
            assert elapsed < 0.3, f"concurrent await took {elapsed:.2f}s"

            # And the eval run is still in progress (not yet completed).
            state = evaluation_service._EVALUATION_STORE[response.run_id]
            assert state.status in {"pending", "running"}

            # Release the gate and let the task complete to clean up.
            gate.set()
            for _ in range(500):
                await asyncio.sleep(0.001)
                state = evaluation_service._EVALUATION_STORE.get(response.run_id)
                if state is not None and state.status == "completed":
                    break
            assert state is not None and state.status == "completed"

    asyncio.run(_main())


def test_get_does_not_block_for_in_progress_run() -> None:
    """VAL-SEC-010/011: GET on a perpetually-pending run returns immediately.

    A run with no background task scheduled stays pending; GET must return the
    pending status without blocking.
    """
    run_id = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    _store_insert(run_id, _RunState(run_id=run_id, status="pending"))

    async def _main() -> EvaluationReport:
        loop = asyncio.get_running_loop()
        start = loop.time()
        report = await get_evaluation_report(run_id)
        elapsed = loop.time() - start
        assert report.status == "pending"
        assert elapsed < 0.3, f"GET took {elapsed:.2f}s for pending run"
        return report

    asyncio.run(_main())
