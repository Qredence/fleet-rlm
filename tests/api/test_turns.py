"""Offline domain regression coverage."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import importlib
import json
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import httpx
import pytest
from dspy import AdapterParseError
from fastapi import FastAPI
from fastapi.sse import EventSourceResponse
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from pydantic import ValidationError

import fleet_rlm.api.app as api_app
import fleet_rlm.api.deps as api_deps
import fleet_rlm.api.routes.turns as turns_route
import fleet_rlm.engine.turn as turn_module
import fleet_rlm.observability as observability
from fleet_rlm.api.app import create_app
from fleet_rlm.api.deps import get_registry
from fleet_rlm.api.events import SSERuntimeCallback
from fleet_rlm.api.routes.turns import create_turn_executor
from fleet_rlm.api.schemas import TurnRequest, WorkspaceWriteRequest
from fleet_rlm.api.stream_contracts import FleetEnvelope, frame_envelope
from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import Route
from fleet_rlm.experiments import (
    _RECOVERY_INTERVAL_SECONDS,
    ExperimentBusyError,
    ExperimentManager,
    _open_worker_log,
)
from fleet_rlm.optimization_runs import ExperimentOptions
from fleet_rlm.replay import decode_snapshot
from fleet_rlm.sandbox.manager import CLEANUP_NOT_REQUIRED
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.session import SessionRuntime, SessionRuntimeRegistry
from fleet_rlm.store import IdempotencyConflictError
from fleet_rlm.tui.commands import SessionState
from fleet_rlm.tui.render import TurnTracker
from fleet_rlm.tui.stream import _turn_events
from fleet_rlm.workspace import WorkspaceDataset, validate_session_id

# Local test doubles


class FakeManager(SimpleNamespace):
    def read_workspace_files(self, paths):
        return {path: self.read_workspace_file(path) for path in paths}

    def __init__(self, **overrides: Any) -> None:
        defaults: dict[str, Any] = {
            "session_id": "session-a",
            "event_callback": None,
            "_cancelled": threading.Event(),
            "_sandbox": None,
            "sub_lm": None,
            "max_llm_calls": 150,
            "max_depth": 3,
            "max_children": 8,
            "max_active_children": 4,
            "turn_timeout_seconds": 900,
            "turn_active": False,
            "_turn_generation": 0,
            "snapshot_provenance": lambda: {},
            "deadline_provenance": lambda: {"deadline_source": "default", "effective_seconds": 900},
            "list_workspace_files": lambda: [],
            "read_workspace_file": lambda _path: b"",
            "write_learned_note": lambda *_args, **_kwargs: None,
            "reset_turn_budget": lambda: None,
            "end_turn": lambda: None,
            "cancel_turn": lambda: None,
        }
        super().__init__(**{**defaults, **overrides})
        if "request_cancellation" not in overrides:
            # Mirror the real manager's single-owner guard: the terminal branch, a disconnect, and
            # a deadline all may race, and only one of them may invoke cancel_turn().
            self._cancellation_claimed = False
            self._teardown_done = threading.Event()

            def request_cancellation() -> None:
                if self._cancellation_claimed:
                    self._teardown_done.wait()
                    return
                self._cancellation_claimed = True
                try:
                    self.cancel_turn()
                finally:
                    self._teardown_done.set()

            self.request_cancellation = request_cancellation
        if "reset_turn_budget" not in overrides:

            def reset_turn_budget(
                *, deadline_seconds: int | None = None, deadline_source: str = "configuration", **_kwargs: Any
            ) -> None:
                self._cancellation_claimed = False
                self._teardown_done.clear()
                self._turn_generation += 1
                self.turn_active = True
                self.deadline_provenance = lambda: {
                    "deadline_source": deadline_source,
                    "effective_seconds": deadline_seconds,
                }

            self.reset_turn_budget = reset_turn_budget
        if "end_turn" not in overrides:

            def end_turn() -> None:
                self.turn_active = False

            self.end_turn = end_turn
        if "stat_workspace_file" not in overrides:
            self.stat_workspace_file = self._stat_file
        if "execution_evidence" not in overrides:
            self.execution_evidence = self._execution_evidence
        if "turn_inputs" not in overrides:
            # Derived from the other fakes so tests that only fake listing or contents stay consistent.
            self.turn_inputs = lambda: {
                "digests": {
                    path: hashlib.sha256(self.read_workspace_file(path)).hexdigest()
                    for path in self.list_workspace_files()
                },
                "environment": "",
            }

    def _stat_file(self, path: str) -> dict[str, Any]:
        content = self.read_workspace_file(path)
        return {"path": path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def _execution_evidence(self) -> dict[str, Any]:
        files = {}
        for path in self.list_workspace_files():
            if path.startswith("artifacts/"):
                content = self.read_workspace_file(path)
                files[path] = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "base64": base64.b64encode(content).decode(),
                }
        return {
            "schema_version": 1,
            "artifacts": {"complete": True, "files": files},
            "children": [],
            "children_complete": True,
        }


def store_with(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "history": lambda _session: [],
        "turn_status": lambda _session, _turn: None,
        "save_runtime_settings": lambda *_args: None,
        "finish_turn": lambda *_args, **_kwargs: None,
        "commit_turn": lambda *_args, **_kwargs: None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRuntime(SessionRuntime):
    """Allow individual runtime policy methods to be replaced in boundary tests."""


def engine_with(root: str = "openai/system.ai.root", sub: str = "openai/system.ai.sub") -> SimpleNamespace:
    return SimpleNamespace(
        execution_mode="hybrid",
        root_lm=SimpleNamespace(model=root),
        sub_lm=SimpleNamespace(model=sub),
        compiled_path=None,
    )


def runtime_with(**parts: Any) -> SessionRuntime:
    engine = parts.pop("engine", engine_with())
    execution_mode = parts.pop("execution_mode", getattr(engine, "execution_mode", "hybrid"))
    engine.execution_mode = execution_mode
    real_policy = parts.pop("real_policy", False)
    policy = {
        name: parts.pop(name)
        for name in (
            "claim_turn",
            "memories",
            "load_context",
            "load_rlm_history",
            "commit_turn",
            "turn_status",
            "save_memory",
            "delete_memory",
        )
        if name in parts
    }
    runtime = FakeRuntime(
        sandbox_mgr=parts.pop("sandbox_mgr", FakeManager()),
        store=parts.pop("store", store_with()),
        skill_mgr=parts.pop(
            "skill_mgr",
            SimpleNamespace(sync_local_skills_to_volume=lambda: 0, build_catalog_xml=lambda: "<available_skills />"),
        ),
        engine=engine,
        **parts,
    )
    if not real_policy:
        defaults = {
            "claim_turn": lambda *_args: None,
            "memories": lambda *_args, **_kwargs: [],
            "load_context": lambda _session: ("", ""),
            "load_rlm_history": lambda _session: None,
            "turn_status": lambda _turn: None,
        }
        for name, method in (defaults | policy).items():
            setattr(runtime, name, method)
    return runtime


def completion_for(
    turn_record: dict[str, Any],
    artifacts: list[str] | None = None,
    *,
    replay_snapshot: tuple[str, bytes] | None = None,
) -> dict[str, Any]:
    """Use the real canonical writer for tests replacing the commit boundary."""
    runtime = runtime_with(sandbox_mgr=FakeManager(list_workspace_files=lambda: artifacts or []))
    captured_paths = turn_record.get("artifact_capture", {}).get("paths")
    return runtime.commit_turn("session-a", turn_record, replay_snapshot=replay_snapshot, artifact_paths=captured_paths)


class FakeRegistry:
    def __init__(self, runtime: SessionRuntime, engine_for_models: Any = None) -> None:
        self.runtime = runtime
        self._engine_for_models = engine_for_models

    def get(self, session_id: str) -> SessionRuntime:
        validate_session_id(session_id)
        return self.runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> Any:
        return self._engine_for_models(runtime, models)

    def evict(self, session_id: str) -> bool:
        validate_session_id(session_id)
        return self._evicted.pop(session_id, False) if hasattr(self, "_evicted") else True

    def close(self) -> None:
        return None


def api_settings(**values: Any) -> FleetSettings:
    return FleetSettings(
        _env_file=None,
        databricks_token="test-token",
        **values,
    )


@contextmanager
def opened_client(*args: Any, **kwargs: Any):
    """Create and enter a fake-runtime client with its FastAPI lifespan active."""
    client, app = client_for(*args, **kwargs)
    with client:
        yield client, app


def client_for(
    runtime: SessionRuntime | None = None,
    *,
    settings: FleetSettings | None = None,
    store: Any = None,  # A SimpleNamespace fake, not a real Store instance.
    **registry: Any,
) -> tuple[TestClient, Any]:
    app = create_app(
        settings=settings or api_settings(),
        initialize_db=False,
        configure_observability=False,
        store=store,
    )
    if runtime is not None:
        fake = FakeRegistry(runtime, **registry)
        app.dependency_overrides[get_registry] = lambda: fake
    client = TestClient(app, raise_server_exceptions=False)
    # TestClient requests without a context manager do not run FastAPI lifespan. Give each
    # test app an explicit executor owner instead of relying on a hidden process-global fallback.
    executor = create_turn_executor()
    app.state.turn_executor = executor
    weakref.finalize(client, executor.shutdown, wait=False, cancel_futures=True)
    return client, app


# Consolidated from api/test_turns.py


# Turn lifecycle and durable outcomes


def events_of(response) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize(
    "names", [None, "ledger", [1], [True], [""], ["A"], ["é"], ["../ledger"], ["a", "a"], ["a", "b", "c", "d"], ["a\n"]]
)
def test_required_skill_request_shape_is_strict(names):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TurnRequest(task="task", requested_skills=names)
    assert TurnRequest(task="task").requested_skills == []
    assert TurnRequest(task="task", requested_skills=["a", "b", "c"]).requested_skills == ["a", "b", "c"]


@pytest.fixture
def turn_results():
    return json.loads((Path(__file__).parents[1] / "fixtures.json").read_text())["turn_results"]


@pytest.mark.usefixtures("traced")
def test_baseline_live_success_and_failure_fields_are_preserved(turn_results):
    runtime, _state = durable_runtime()
    success = []
    _run_turn_direct(runtime, emitted=success, turn_id="fixture-turn", timeout_seconds=0)
    assert success[0]["duration_seconds"] >= 0
    assert {**success[0], "duration_seconds": 0.0} == turn_results["live_success"]

    stored = []

    def fail(**_kwargs):
        raise RuntimeError("private response")

    runtime = _failed_turn_runtime(FakeManager(), stored, fail)
    failure = []
    _run_turn_direct(runtime, emitted=failure, turn_id="fixture-turn", timeout_seconds=900)
    assert {**failure[0], "duration_seconds": 0.0} == turn_results["live_failure"]
    assert stored[0][2] == failure[0]
    assert failure[0]["effective_seconds"] == stored[0][2]["effective_seconds"] == 900


@pytest.mark.usefixtures("traced")
def test_historical_recovery_preserves_unknown_fields_and_only_normalizes_at_read_boundary(turn_results):
    for row in turn_results["legacy_rows"]:
        prior = deepcopy(row)
        runtime = runtime_with(claim_turn=lambda *_args, prior=prior: prior)
        store = store_with(
            turn_status=lambda *_args, prior=prior: {**deepcopy(prior), "updated_at": "2026-10-01T00:00:00Z"}
        )
        expected = {**row["result"], "turn_id": row["turn_id"]}
        with opened_client(runtime, store=store) as (client, _app):
            replay = events_of(client.post("/sessions/session-a/turn", json={"task": "old"}))
            status = client.get(f"/sessions/session-a/turns/{row['turn_id']}").json()
        assert {"event": replay[1]["event"], **replay[1]["payload"]} == expected
        state = status["result"].pop("provenance_state")
        assert state == ("unresolved" if "deadline_source" in row["result"] else "pre-provenance")
        assert status["result"] == expected
        replayed = turn_module._replayed(prior)
        replayed["historical_extension"] = "changed by consumer"
        assert prior == row


def durable_runtime(*, lose_ack=False, unavailable_readback=False):
    """Exercise real Session policies over a JSON-owning Store fake."""
    rows, commits, executions, cancellations = {}, [], [], []

    def claim(_session, turn_id, key, digest):
        if key in rows:
            return deepcopy(rows[key])
        rows[key] = {"turn_id": turn_id, "task_sha256": digest, "status": "running", "result": {}}
        return None

    def persist(_session, **kwargs):
        commits.append(deepcopy(kwargs))
        turn_id, result = kwargs["completion"]
        row = next(row for row in rows.values() if row["turn_id"] == turn_id)
        row.update(status="completed", result=json.loads(json.dumps(result)))
        if lose_ack:
            raise ConnectionError("private provider response")

    def status(_session, turn_id):
        if unavailable_readback:
            raise ConnectionError("private database response")
        row = next((row for row in rows.values() if row["turn_id"] == turn_id), None)
        return {**deepcopy(row), "updated_at": "2026-10-01T00:00:00Z"} if row else None

    def finish(*_args):
        pytest.fail("a committed or uncertain Turn must not be written as failed")

    def engine(**_kwargs):
        executions.append(1)
        return SimpleNamespace(
            answer="verified",
            route="flex",
            learned_skill_note="A reusable hint.",
            lm_calls=4,
            sandbox_errors=1,
            submit_rejections=2,
            budget_breakdown={"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
        )

    manager = FakeManager(
        session_id="session-a",
        list_workspace_files=lambda: ["artifacts/report.txt"],
        snapshot_provenance=lambda: {
            "snapshot_name": "fleet-test",
            "snapshot_id": "snap-1",
            "snapshot_cpu": 4,
            "snapshot_mem": 8,
            "snapshot_disk": 8,
            "snapshot_definition_digest": "a" * 64,
            "snapshot_build_digest": "b" * 64,
        },
        live_sandbox_id="warm-root",
        provider_bounds=lambda: {
            "ttl_minutes": 1440,
            "auto_stop_interval": 60,
            "maximum_lifespan_minutes": None,
            "provider_owned": True,
        },
        cancel_turn=lambda: cancellations.append(1),
    )
    store = store_with(
        claim_turn=claim,
        turn_by_key=lambda _session, key: deepcopy(rows.get(key)),
        commit_turn=persist,
        turn_status=status,
        finish_turn=finish,
        load_session=lambda _session: ("", []),
        memories=lambda *_args, **_kwargs: [],
    )
    return runtime_with(real_policy=True, sandbox_mgr=manager, store=store, engine=engine), SimpleNamespace(
        rows=rows,
        commits=commits,
        executions=executions,
        cancellations=cancellations,
    )


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "phase_outputs",
        "phase_exit",
        "span_outputs",
        "span_attributes",
        "final_attribute",
        "span_exit",
        "phase_and_span_exit",
        "trace",
        "note",
        "end_turn",
    ],
)
def test_canonical_completion_survives_postcommit_observation_faults(traced, monkeypatch, caplog, fault):
    runtime, state = durable_runtime()
    original = turn_module.mlflow.start_span

    def broken(*_args, **_kwargs):
        raise RuntimeError("private provider response")

    @contextmanager
    def span(**kwargs):
        with original(**kwargs) as active:
            root = kwargs["name"] == "fleet_turn"
            commit = kwargs["name"] == "turn.commit"
            if (commit and fault in {"phase_outputs", "phase_and_span_exit"}) or (root and fault == "span_outputs"):
                active.set_outputs = broken
            if root and fault == "span_attributes":
                original_attributes = active.set_attributes
                active.set_attributes = lambda values: broken() if state.commits else original_attributes(values)
            if root and fault == "final_attribute":
                original_attribute = active.set_attribute
                active.set_attribute = lambda key, value: (
                    broken() if key == "cleanup" else original_attribute(key, value)
                )
            yield active
        if (commit and fault == "phase_exit") or (root and fault in {"span_exit", "phase_and_span_exit"}):
            broken()

    monkeypatch.setattr(turn_module.mlflow, "start_span", span)
    if fault == "trace":
        update = turn_module.mlflow.update_current_trace
        monkeypatch.setattr(
            turn_module.mlflow,
            "update_current_trace",
            lambda **kw: broken() if "response_preview" in kw else update(**kw),
        )
    if fault == "note":
        monkeypatch.setattr(turn_module, "_write_learned_note", broken)
    ended = []
    if fault == "end_turn":

        def end_turn():
            ended.append(1)
            broken()

        runtime.sandbox_mgr.end_turn = end_turn
    traced["span_trace_id"] = "canonical-trace"
    body = {"task": "verify", "idempotency_key": "canonical", "timeout_seconds": 0}
    with opened_client(runtime, store=runtime.store) as (client, app):
        app.state.mlflow_experiment_id = "test-experiment"
        live = events_of(client.post("/sessions/session-a/turn", json=body))
        replay = events_of(client.post("/sessions/session-a/turn", json=body))
        status = client.get(f"/sessions/session-a/turns/{state.rows['canonical']['turn_id']}").json()
    stored = state.rows["canonical"]["result"]
    assert [event["event"] for event in live] == ["turn_status", "turn_complete", "done"]
    assert [event["event"] for event in replay] == ["turn_status", "turn_complete", "done"]
    assert {"event": live[1]["event"], **live[1]["payload"]} == stored
    assert {"event": replay[1]["event"], **replay[1]["payload"]} == stored
    provenance_state = status["result"].pop("provenance_state")
    assert provenance_state == "recorded"
    assert status["result"] == stored
    assert stored["effective_seconds"] is None and stored["maximum_lifespan_minutes"] is None
    assert stored["root_sandbox_id"] == "warm-root" and stored["trace_id"] == "canonical-trace"
    assert len(state.executions) == len(state.commits) == 1 and state.cancellations == []
    if fault == "end_turn":
        assert ended == [1, 1], "one finalization attempt per live/replayed request"
    assert "private provider response" not in caplog.text


@pytest.mark.parametrize("unavailable", [False, True])
@pytest.mark.usefixtures("traced")
def test_commit_acknowledgment_loss_uses_claimed_identity_without_reexecution(unavailable):
    runtime, state = durable_runtime(lose_ack=True, unavailable_readback=unavailable)
    emitted = []
    _run_turn_direct(runtime, emitted=emitted, idempotency_key="lost-ack")
    assert emitted[0]["event"] == ("error" if unavailable else "turn_complete")
    assert emitted[-1] == {"event": "done"}
    assert state.rows["lost-ack"]["status"] == "completed"
    replay = []
    _run_turn_direct(runtime, emitted=replay, idempotency_key="lost-ack")
    assert replay[0] == state.rows["lost-ack"]["result"]
    assert len(state.executions) == len(state.commits) == 1
    assert state.cancellations == ([1] if unavailable else [])


@pytest.mark.usefixtures("traced")
def test_terminal_delivery_failure_does_not_cancel_committed_turn(caplog):
    runtime, state = durable_runtime()
    emitted = []

    def emit(event):
        emitted.append(event)
        if event["event"] == "turn_complete":
            raise ConnectionError("private client response")

    turn_module.run_turn(
        runtime,
        api_settings(),
        session_id="session-a",
        turn_id="delivery-turn",
        task="verify termination",
        idempotency_key="delivery",
        mlflow_experiment_id=None,
        callback=object(),
        emit=emit,
    )
    assert [event["event"] for event in emitted] == ["turn_complete", "done"]
    replay = []
    _run_turn_direct(runtime, emitted=replay, idempotency_key="delivery")
    assert replay[0] == state.rows["delivery"]["result"]
    assert state.cancellations == [] and len(state.executions) == 1
    assert "private client response" not in caplog.text


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("committed", [False, True])
def test_invalid_terminal_frame_keeps_existing_commit_and_cancellation_authority(monkeypatch, committed):
    runtime, state = durable_runtime()
    cancelled = threading.Event()
    request_cancellation = runtime.sandbox_mgr.request_cancellation

    def cancel_and_signal():
        request_cancellation()
        cancelled.set()

    runtime.sandbox_mgr.request_cancellation = cancel_and_signal
    frame = turns_route.frame_envelope
    bad_event = "turn_complete" if committed else "error"

    def reject_terminal(wire):
        if wire["event"] == bad_event:
            raise RuntimeError("Turn event contract validation failed")
        return frame(wire)

    monkeypatch.setattr(turns_route, "frame_envelope", reject_terminal)
    if not committed:

        def emit_uncommitted_outcome(*_args, emit, **_kwargs):
            runtime.sandbox_mgr.reset_turn_budget()
            emit({"event": "error", "request_id": "invalid-terminal"})
            assert cancelled.wait(5), "the uncommitted worker should be fenced and drained"

        monkeypatch.setattr(turns_route, "run_turn", emit_uncommitted_outcome)

    with opened_client(runtime, store=runtime.store) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "validate"})

    assert [event["event"] for event in events_of(response)] == ["turn_status"]

    if committed:
        assert [row["status"] for row in state.rows.values()] == ["completed"]
        assert state.cancellations == []
    else:
        assert state.cancellations == [1]


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("fault", ["execution", "lease", "cancelled", "admission_lease"])
def test_failure_before_commit_keeps_terminal_cleanup_and_never_completes(fault):
    runtime, state = durable_runtime()
    finished = []
    execute = runtime.engine
    if fault == "admission_lease":
        claim = runtime.store.claim_turn

        def lose_lease(*args):
            prior = claim(*args)
            runtime.lease_lost.set()
            return prior

        runtime.store.claim_turn = lose_lease

    def engine(**kwargs):
        if fault == "admission_lease":
            pytest.fail("A lease lost during admission must fence execution")
        if fault == "execution":
            raise RuntimeError("private execution response")
        prediction = execute(**kwargs)
        if fault == "lease":
            runtime.lease_lost.set()
        else:
            runtime.sandbox_mgr._cancelled.set()
        return prediction

    runtime.engine = engine
    runtime.engine.execution_mode = "hybrid"
    runtime.store.finish_turn = lambda *args: finished.append(args)
    emitted = []
    _run_turn_direct(runtime, emitted=emitted)
    outcome = "cancelled" if fault == "cancelled" else "failed"
    assert [event["event"] for event in emitted] == ["turn_cancelled" if fault == "cancelled" else "error", "done"]
    assert finished[0][2] == emitted[0]["outcome"] == outcome
    assert state.commits == [] and state.cancellations == [1]


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("readback", ["running", "missing", "unavailable"])
def test_unproven_commit_never_reexecutes_and_preserves_unavailable_intent(readback):
    runtime, state = durable_runtime()
    reads, writes = [], []

    def commit(*_args, **_kwargs):
        raise ConnectionError("private commit response")

    def status(_session, turn_id):
        reads.append(turn_id)
        if readback == "unavailable":
            raise ConnectionError("private readback response")
        return deepcopy(state.rows["uncertain"]) if readback == "running" else None

    def finish(_session, turn_id, outcome, result):
        writes.append(turn_id)
        state.rows["uncertain"].update(status=outcome, result=deepcopy(result))

    runtime.store.commit_turn = commit
    runtime.store.turn_status = status
    runtime.store.finish_turn = finish
    emitted = []
    _run_turn_direct(runtime, emitted=emitted, idempotency_key="uncertain")
    assert [event["event"] for event in emitted] == ["error", "done"]
    assert reads == ["turn-teardown"]
    assert state.rows["uncertain"]["status"] == ("failed" if readback == "running" else "running")
    assert writes == (["turn-teardown"] if readback == "running" else [])
    replay = []
    _run_turn_direct(runtime, emitted=replay, idempotency_key="uncertain")
    assert replay[0]["event"] == "error" and "not replayed" in replay[0]["error"]
    assert len(state.executions) == 1 and state.cancellations == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", ["poll", "close"])
@pytest.mark.parametrize("boundary", ["commit", "note"])
@pytest.mark.usefixtures("traced")
async def test_disconnect_during_postcommit_observation_preserves_success(monkeypatch, disconnect, boundary):
    runtime, state = durable_runtime()
    observing, release = threading.Event(), threading.Event()
    ended = threading.Event()
    runtime.sandbox_mgr.end_turn = ended.set

    def observe(*_args):
        if boundary == "note":
            assert ended.is_set(), "the execution deadline must end before postcommit observation"
        observing.set()
        assert release.wait(timeout=5)

    async def is_disconnected():
        if observing.is_set():
            asyncio.get_running_loop().call_later(0.05, release.set)
            return True
        return False

    if boundary == "note":
        monkeypatch.setattr(turn_module, "_write_learned_note", observe)
    else:
        persist = runtime.store.commit_turn

        def commit(*args, **kwargs):
            persist(*args, **kwargs)
            observe()

        runtime.store.commit_turn = commit
    monkeypatch.setattr(turns_route, "_DISCONNECT_POLL_SECONDS", 0.01)
    with turns_route.create_turn_executor() as executor:
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(mlflow_experiment_id=None)),
            is_disconnected=is_disconnected,
        )
        stream = turns_route.stream_turn(
            "session-a",
            turns_route.TurnRequest(task="verify", idempotency_key="disconnected"),
            request,
            runtime,
            api_settings(),
            executor,
            AsyncExitStack(),
        )
        try:
            assert (await anext(stream)).data["event"] == "turn_status"
            assert await asyncio.to_thread(observing.wait, 2)
            if disconnect == "close":
                asyncio.get_running_loop().call_later(0.05, release.set)
                await stream.aclose()
            else:
                assert [event async for event in stream] == []
        finally:
            release.set()
            await stream.aclose()
    assert state.rows["disconnected"]["status"] == "completed"
    assert state.cancellations == []
    monkeypatch.setattr(turn_module, "_write_learned_note", lambda *_args: None)
    with opened_client(runtime) as (client, _app):
        events = events_of(
            client.post(
                "/sessions/session-a/turn",
                json={"task": "verify", "idempotency_key": "disconnected"},
            )
        )
    assert {"event": events[1]["event"], **events[1]["payload"]} == state.rows["disconnected"]["result"]
    assert len(state.executions) == len(state.commits) == 1


def test_artifact_changes_distinguish_retained_outputs():
    before = {"artifacts/prior.txt": "same", "artifacts/report.txt": "old", "artifacts/gone.txt": "gone"}
    capture = {
        "complete": True,
        "paths": ["artifacts/created.txt", "artifacts/prior.txt", "artifacts/report.txt"],
        "files": [
            {"path": "artifacts/prior.txt", "sha256": "same", "size_bytes": 1, "source": "workspace"},
            {"path": "artifacts/report.txt", "sha256": "new", "size_bytes": 1, "source": "workspace"},
            {"path": "artifacts/created.txt", "sha256": "created", "size_bytes": 1, "source": "workspace"},
        ],
    }
    changes = turn_module._artifact_changes(before, capture)
    assert changes["complete"] is True
    assert {path: item["state"] for path, item in changes["files"].items()} == {
        "artifacts/prior.txt": "retained",
        "artifacts/report.txt": "modified",
        "artifacts/created.txt": "created",
        "artifacts/gone.txt": "deleted",
    }
    assert changes["files"]["artifacts/report.txt"] == {
        "state": "modified",
        "input_sha256": "old",
        "sha256": "new",
    }


def test_artifact_capture_validates_hashes_and_retains_sorted_logical_metadata() -> None:
    payload = b"captured"
    evidence = {
        "artifacts": {
            "complete": True,
            "files": {
                "artifacts/z.txt": {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "base64": base64.b64encode(payload).decode(),
                }
            },
        },
        "children": [],
        "children_complete": True,
    }

    capture = turn_module._capture_artifact_view(evidence)

    assert capture == {
        "complete": True,
        "paths": ["artifacts/z.txt"],
        "files": [
            {
                "path": "artifacts/z.txt",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "source": "workspace",
            }
        ],
    }
    evidence["artifacts"]["files"]["artifacts/z.txt"]["base64"] = base64.b64encode(b"mutated").decode()
    assert capture["files"][0]["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("removed", [False, True])
def test_root_can_supersede_promoted_child_output(removed):
    path = "artifacts/child/out.txt"
    current = b"root post-processing"
    evidence = {
        "artifacts": {
            "complete": True,
            "files": {}
            if removed
            else {path: {"sha256": hashlib.sha256(current).hexdigest(), "base64": base64.b64encode(current).decode()}},
        },
        "children_complete": True,
        "children": [
            {
                "status": "succeeded",
                "artifacts": [
                    {"root_path": path, "agent_id": "child", "sha256": hashlib.sha256(b"original").hexdigest()}
                ],
            }
        ],
    }
    capture = turn_module._capture_artifact_view(evidence)
    assert capture["complete"] is True
    assert capture["superseded_child_receipts"] == [path]
    assert capture["paths"] == ([] if removed else [path])
    if not removed:
        assert capture["files"][0]["source"] == "workspace"
        assert "agent_id" not in capture["files"][0]
    # Keep the original promotion-time receipt intact for downstream scoring.
    assert evidence["children"][0]["artifacts"][0]["sha256"] == hashlib.sha256(b"original").hexdigest()


def test_incomplete_or_invalid_artifact_capture_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="incomplete"):
        turn_module._capture_artifact_view(
            {"artifacts": {"complete": False, "files": {}}, "children": [], "children_complete": True}
        )


@pytest.mark.parametrize("bound", ["count", "bytes"])
@pytest.mark.usefixtures("traced")
def test_large_session_inventory_keeps_success_and_replay_without_claiming_complete_evidence(bound):
    from fleet_rlm.execution_evidence import MAX_ARTIFACTS, collect_artifacts

    runtime, state = durable_runtime()
    manager = runtime.sandbox_mgr
    paths = [f"artifacts/{index:03}.txt" for index in range(MAX_ARTIFACTS + 1)]
    if bound == "bytes":
        paths = paths[:2]
    manager.list_workspace_files = lambda: paths
    manager.read_workspace_file = lambda _path, **_kwargs: b"x" * (13_000_000 if bound == "bytes" else 1)
    manager.execution_evidence = lambda: {
        "artifacts": collect_artifacts(manager),
        "children": [],
        "children_complete": True,
    }
    with opened_client(runtime) as (client, _app):
        first = events_of(
            client.post("/sessions/session-a/turn", json={"task": "continue", "idempotency_key": "large-session"})
        )
        replay = events_of(
            client.post("/sessions/session-a/turn", json={"task": "continue", "idempotency_key": "large-session"})
        )
    result = state.rows["large-session"]["result"]
    assert result["event"] == "turn_complete" and result["artifacts"] == paths
    assert any(event["event"] == "turn_complete" for event in first)
    assert next(event["payload"] for event in replay if event["event"] == "turn_complete")["artifacts"] == paths
    assert len(state.executions) == 1
    record = json.loads(state.commits[0]["turn_record_json"])
    assert record["artifact_capture"]["complete"] is False
    assert record["artifact_changes"]["complete"] is False
    assert {item["state"] for item in record["artifact_changes"]["files"].values()} == {"unknown"}


def test_artifact_capture_accepts_the_full_workspace_artifact_set() -> None:
    """33 artifacts and a 300 KB file are ordinary Turns, not invalid captures."""
    from fleet_rlm.execution_evidence import MAX_ARTIFACTS

    files = {}
    for index in range(MAX_ARTIFACTS - 1):
        raw = f"row {index}".encode()
        files[f"artifacts/{index:03}.txt"] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "base64": base64.b64encode(raw).decode(),
        }
    large = b"z" * 300_000
    files["artifacts/large.csv"] = {
        "sha256": hashlib.sha256(large).hexdigest(),
        "base64": base64.b64encode(large).decode(),
    }

    capture = turn_module._capture_artifact_view(
        {"artifacts": {"complete": True, "files": files}, "children": [], "children_complete": True}
    )

    assert capture["complete"] is True
    assert len(capture["paths"]) == MAX_ARTIFACTS
    assert next(entry for entry in capture["files"] if entry["path"] == "artifacts/large.csv")["size_bytes"] == 300_000


def test_artifact_heavy_span_keeps_route_usage_and_cleanup_after_sanitization(traced) -> None:
    """Scalar Turn facts must survive an evidence tree that fills the span node budget.

    MLflow sanitizes a span with a fixed node budget, so anything recorded after a large
    execution_evidence tree is at risk. The evidence itself must stay whole: scoring compares it
    against reviewed gold.
    """
    from fleet_rlm.execution_evidence import MAX_ARTIFACTS

    payload = b"a"
    files = {
        f"artifacts/{index:03}.txt": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "base64": base64.b64encode(payload).decode(),
        }
        for index in range(MAX_ARTIFACTS)
    }
    evidence = {"artifacts": {"complete": True, "files": files}, "children": [], "children_complete": True}
    paths = sorted(files)
    runtime = runtime_with(
        sandbox_mgr=FakeManager(list_workspace_files=lambda: paths, read_workspace_file=lambda _path: payload),
        commit_turn=lambda **kwargs: completion_for(
            kwargs["turn_record"], paths, replay_snapshot=kwargs["replay_snapshot"]
        ),
        engine=lambda **_kwargs: SimpleNamespace(
            answer="Done",
            route="rlm",
            execution_evidence=evidence,
            lm_calls=4,
            sandbox_errors=0,
            submit_rejections=0,
            budget_breakdown={"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
        ),
    )

    with opened_client(runtime) as (client, _app):
        assert client.post("/sessions/session-a/turn", json={"task": "Produce many files"}).status_code == 200

    outputs = traced["outputs"]
    order = list(outputs)
    # The evidence tree is emitted last so sanitization truncates it, never the scalar facts.
    assert order.index(_turn_cleanup_key()) > order.index("lm_calls")
    assert order[-1] == "artifact_changes"
    assert outputs["route"] == "rlm"
    assert outputs["lm_calls"] == 4
    assert outputs[_turn_cleanup_key()] == "not_required"
    # The evidence is never clipped: gold scoring compares against these exact bytes.
    assert len(outputs["execution_evidence"]["artifacts"]["files"]) == MAX_ARTIFACTS


def _turn_cleanup_key() -> str:
    from fleet_rlm.engine.turn import _CLEANUP_ATTRIBUTE

    return _CLEANUP_ATTRIBUTE


@pytest.fixture
def traced(monkeypatch):
    """Replace MLflow and dspy.context in the Turn module; record what the Turn reports."""
    seen: dict[str, Any] = {
        "updates": [],
        "callbacks": [],
        "destinations": [],
        "span_active": False,
        "spans": [],
        "stack": [],
    }

    @contextmanager
    def span(**kwargs):
        root = kwargs["name"] == "fleet_turn"
        row = {"name": kwargs["name"], "parent": seen["stack"][-1] if seen["stack"] else None}
        seen["spans"].append(row)
        seen["stack"].append(kwargs["name"])
        if root:
            seen["destinations"].append(kwargs.get("trace_destination"))
        seen["span_active"] = True
        try:
            if not root:
                # Phase spans record bounded counts, so the fake must accept every setter a
                # real span does; a missing one shows up as a production AttributeError.
                yield SimpleNamespace(
                    set_inputs=lambda value: row.__setitem__("inputs", value),
                    set_outputs=lambda value: row.__setitem__("outputs", value),
                    set_attributes=lambda value: row.setdefault("attributes", {}).update(value),
                    set_attribute=lambda name, value: row.__setitem__(name, value),
                )
                return
            yield SimpleNamespace(
                trace_id=seen.get("span_trace_id"),
                set_status=lambda value: seen.__setitem__("status", value),
                set_inputs=lambda value: seen.__setitem__("inputs", value),
                set_outputs=lambda value: seen.__setitem__("outputs", value),
                set_attributes=lambda value: seen.setdefault("attributes", {}).update(value),
                set_attribute=lambda name, value: seen.__setitem__(name, value),
            )
        finally:
            seen["stack"].pop()
            seen["span_active"] = bool(seen["stack"])

    @contextmanager
    def context(*, callbacks, disable_history, **_extra):
        assert disable_history is True
        seen["callbacks"].extend(callbacks)
        yield

    seen["global_callback"] = object()
    fake_dspy = SimpleNamespace(settings=SimpleNamespace(callbacks=[seen["global_callback"]]), context=context)
    monkeypatch.setattr(turn_module, "dspy", fake_dspy)
    # Callback composition lives in observability, so the same ambient list must be visible there.
    monkeypatch.setattr(observability, "dspy", fake_dspy)
    # The phase-span helper lives in observability, so the same recorder must be visible there.
    monkeypatch.setattr(observability.mlflow, "start_span", span)
    monkeypatch.setattr(turn_module.mlflow, "start_span", span)
    monkeypatch.setattr(turn_module.mlflow, "update_current_trace", lambda **kwargs: seen["updates"].append(kwargs))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a Turn must not change process-wide MLflow state")

    monkeypatch.setattr(turn_module.mlflow, "set_experiment", forbidden)
    monkeypatch.setattr(turn_module.mlflow, "get_last_active_trace_id", forbidden)

    prepare_inputs = turn_module._prepare_inputs

    def record_preparation(*args, **kwargs):
        seen["preparation_inside_span"] = seen["span_active"]
        return prepare_inputs(*args, **kwargs)

    monkeypatch.setattr(turn_module, "_prepare_inputs", record_preparation)
    return seen


@pytest.mark.parametrize("write_fails", [False, True])
def test_a_turn_persists_then_writes_its_note_and_streams_one_completion(traced, write_fails):
    files = {"files/input.txt": b"initial input", "files/rates.csv": b"rate\n3\n"}
    records: list[dict] = []
    order: list[str] = []
    written: list[tuple] = []
    traced["span_trace_id"] = "trace-test-id"

    def run_engine(**_kwargs):
        files["files/input.txt"] = b"modified during execution"
        return SimpleNamespace(
            answer="processed locally",
            route="flex",
            learned_skill_note="Reuse the digit window check.",
            lm_calls=4,
            sandbox_errors=1,
            submit_rejections=2,
            budget_breakdown={"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
        )

    def persist(**kwargs):
        records.append(kwargs["turn_record"])
        order.append("sync")
        return completion_for(
            kwargs["turn_record"], ["artifacts/result.csv"], replay_snapshot=kwargs["replay_snapshot"]
        )

    def write_note(turn_id, note, trace_id=None):
        order.append("write")
        if write_fails:
            raise RuntimeError("shared mount is gone")
        written.append((turn_id, note, trace_id))

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            list_workspace_files=lambda: list(reversed(files)),
            read_workspace_file=files.__getitem__,
            execution_evidence=lambda: {
                "artifacts": {
                    "complete": True,
                    "files": {
                        "artifacts/result.csv": {
                            "sha256": hashlib.sha256(b"generated result").hexdigest(),
                            "base64": base64.b64encode(b"generated result").decode(),
                        }
                    },
                },
                "children": [],
                "children_complete": True,
            },
            write_learned_note=write_note,
        ),
        load_context=lambda _session: ("summary", "prior history"),
        commit_turn=persist,
        engine=run_engine,
        context=WorkspaceDataset("files/input.txt"),
    )
    with opened_client(runtime) as (client, app):
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "summarize this file"})

    events = events_of(response)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert [event["event"] for event in events] == ["turn_status", "turn_complete", "done"]
    assert events[0]["payload"] == {"phase": "preparation"}
    payload = events[1]["payload"]
    turn_id = payload.pop("turn_id")
    outcome = payload.pop("outcome")
    duration_seconds = payload.pop("duration_seconds")
    assert turn_id == records[0]["turn_id"]
    assert outcome == "completed"
    assert duration_seconds >= 0
    assert payload == {
        "replay_snapshot_selected": True,
        "answer": "processed locally",
        "route": "flex",
        "learned_skill_note": "Reuse the digit window check.",
        "artifacts": ["artifacts/result.csv"],
        "trace_id": "trace-test-id",
        "lm_calls": 4,
        "sandbox_errors": 1,
        "submit_rejections": 2,
        "budget_breakdown": {"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
        "deadline_source": "configuration",
        "effective_seconds": None,
        # A completed Turn keeps its warm root sandbox for the next Turn.
        "cleanup": "not_required",
    }
    described, completed = traced["updates"]
    assert described["metadata"] == {"mlflow.trace.session": "session-a"}
    assert described["request_preview"] == "summarize this file"
    assert described["client_request_id"] == records[0]["turn_id"]
    assert described["tags"]["fleet.execution_mode"] == "hybrid"
    assert described["tags"]["fleet.idempotency_key"] == records[0]["turn_id"]
    assert completed == {"response_preview": "processed locally", "tags": {"fleet.route": "flex"}}
    # The experiment travels with the span rather than through process-wide `set_experiment`.
    assert [getattr(d, "experiment_id", None) for d in traced["destinations"]] == ["experiment-test-id"]
    assert traced["global_callback"] in traced["callbacks"]
    assert traced["preparation_inside_span"] is True
    # The root span is a self-contained optimization example and says which program served it.
    assert traced["inputs"]["session_id"] == "session-a"
    assert traced["inputs"]["workspace_manifest"] == "/workspace/files/input.txt, /workspace/files/rates.csv"
    assert traced["inputs"]["context_files"] == {
        "files/input.txt": hashlib.sha256(b"initial input").hexdigest(),
        "files/rates.csv": hashlib.sha256(b"rate\n3\n").hexdigest(),
    }
    assert traced["outputs"]["lm_calls"] == 4 and traced["outputs"]["sandbox_errors"] == 1
    assert traced["outputs"]["submit_rejections"] == records[0]["submit_rejections"] == 2
    assert (
        traced["outputs"]["artifact_changes"]
        == records[0]["artifact_changes"]
        == {
            "complete": True,
            "files": {
                "artifacts/result.csv": {
                    "state": "created",
                    "input_sha256": None,
                    "sha256": hashlib.sha256(b"generated result").hexdigest(),
                }
            },
        }
    )
    assert traced["compiled_program_digest"] == ""
    assert any(isinstance(callback, SSERuntimeCallback) for callback in traced["callbacks"])
    assert records[0]["context_files"] == {
        "files/input.txt": hashlib.sha256(b"initial input").hexdigest(),
        "files/rates.csv": hashlib.sha256(b"rate\n3\n").hexdigest(),
    }
    assert records[0]["workspace_manifest"] == "/workspace/files/input.txt, /workspace/files/rates.csv"
    assert order == ["sync", "write"]
    # The learned note carries the Turn's own trace id, read from the root span.
    expected_note = (records[0]["turn_id"], "Reuse the digit window check.", "trace-test-id")
    assert written == ([] if write_fails else [expected_note])
    assert not runtime.turn_lock.locked()


def test_turn_persists_retained_artifact_provenance_on_root_trace(traced):
    raw = b"digit=5\n"
    digest = hashlib.sha256(raw).hexdigest()
    path = "artifacts/prior.txt"
    records = []
    evidence = {
        "artifacts": {"complete": True, "files": {path: {"sha256": digest, "base64": base64.b64encode(raw).decode()}}},
        "children": [],
        "children_complete": True,
    }
    runtime = runtime_with(
        sandbox_mgr=FakeManager(list_workspace_files=lambda: [path], read_workspace_file=lambda _path: raw),
        commit_turn=lambda **kwargs: (
            records.append(kwargs["turn_record"])
            or completion_for(kwargs["turn_record"], [path], replay_snapshot=kwargs["replay_snapshot"])
        ),
        engine=lambda **_kwargs: SimpleNamespace(answer="5", route="flex", execution_evidence=evidence),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "repeat prior computation"})
    assert response.status_code == 200
    assert (
        records[0]["artifact_changes"]
        == traced["outputs"]["artifact_changes"]
        == {
            "complete": True,
            "files": {path: {"state": "retained", "input_sha256": digest, "sha256": digest}},
        }
    )
    assert traced["outputs"]["artifacts"] == [path]


def test_artifact_heavy_turn_preserves_execution_evidence_after_trace_sanitization(traced):
    from fleet_rlm.optimize.objectives import artifact_correctness, delegation_correctness
    from fleet_rlm.optimize.scorers import efficiency_metric

    raw = b"report"
    paths = [*(f"artifacts/report-{i}.txt" for i in range(23)), "artifacts/zz-final.txt"]
    final_path = paths[-1]
    digest = hashlib.sha256(raw).hexdigest()
    evidence = {
        "schema_version": 1,
        "artifacts": {
            "complete": True,
            "files": {path: {"sha256": digest, "base64": base64.b64encode(raw).decode("ascii")} for path in paths},
        },
        "children": [
            {
                "status": "succeeded",
                "inputs": {"files/input.csv": "input-digest"},
                "artifacts": [{"path": final_path, "root_path": final_path, "sha256": digest, "agent_id": "child-a"}],
            }
        ],
        "children_complete": True,
        "python": {},
    }
    artifact_gold = {"artifacts": [{"path": final_path, "kind": "text", "expected": "report"}]}
    delegation_gold = {"children": [{"input_paths": ["files/input.csv"], "artifacts": ["zz-final.txt"]}]}
    usage = {
        "lm_calls": 4,
        "sandbox_errors": 1,
        "submit_rejections": 2,
        "budget_breakdown": {"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
    }
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            list_workspace_files=lambda: paths,
            read_workspace_file=lambda _path: raw,
        ),
        commit_turn=lambda **kwargs: completion_for(
            kwargs["turn_record"], paths, replay_snapshot=kwargs["replay_snapshot"]
        ),
        engine=lambda **_kwargs: SimpleNamespace(answer="Done", route="flex", execution_evidence=evidence, **usage),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "Produce reports"})
    assert response.status_code == 200
    sanitized = {}
    observability.sanitize_trace_span(
        SimpleNamespace(name="fleet_turn", outputs=traced["outputs"], set_outputs=sanitized.update)
    )
    assert {name: sanitized[name] for name in usage} == usage
    assert efficiency_metric(dspy.Prediction(**sanitized)) == efficiency_metric(dspy.Prediction(**usage))
    assert efficiency_metric(dspy.Prediction(**sanitized)) is not None
    assert artifact_correctness(artifact_gold, evidence)[0] == 1
    assert artifact_correctness(artifact_gold, sanitized["execution_evidence"])[0] == 1
    assert delegation_correctness(delegation_gold, evidence)[0] == 1
    assert delegation_correctness(delegation_gold, sanitized["execution_evidence"])[0] == 1


@pytest.mark.parametrize("record_replay", [False, True])
@pytest.mark.parametrize("bad_commit", [False, True])
def test_real_runtime_commits_managed_inventory_and_reports_commit_failure(
    traced, monkeypatch, tmp_path, record_replay, bad_commit
):
    import io
    from contextlib import redirect_stdout

    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files" / "input.txt").write_bytes(b"source")
    (workspace / "checkout").mkdir()
    scratch = workspace / "checkout" / "db.py"
    scratch.write_bytes(b"sandbox edits")
    (workspace / "bundle.txt").write_bytes(b"scratch bundle")
    order = []
    records = []
    claims = {}
    engine_calls = []

    def remote_exec(_self, code):
        output = io.StringIO()
        code = code.replace("'/workspace'", repr(str(workspace)))
        with redirect_stdout(output):
            exec(code, {})
        return output.getvalue()

    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", remote_exec)
    remote = DaytonaSandboxManager("key", "org", "volume", "session-a")

    def claim(_session, turn_id, key, digest):
        if key in claims:
            return claims[key]
        claims[key] = {"turn_id": turn_id, "task_sha256": digest, "status": "running", "result": {}}
        return None

    def persist(_session, **kwargs):
        order.append("persist")
        records.append(kwargs)
        for prior in claims.values():
            if prior["turn_id"] == kwargs["completion"][0]:
                prior.update(status="completed", result=kwargs["completion"][1])

    def fail(_session, turn_id, outcome, result):
        order.append("failed")
        assert "stop" in order
        for prior in claims.values():
            if prior["turn_id"] == turn_id:
                prior.update(status=outcome, result=result)

    store = store_with(
        load_session=lambda _session: ("", []),
        memories=lambda *_args, **_kwargs: [],
        claim_turn=claim,
        commit_turn=persist,
        finish_turn=fail,
        turn_status=lambda _session, turn_id: next((row for row in claims.values() if row["turn_id"] == turn_id), None),
    )
    manager = FakeManager(
        session_id="session-a",
        turn_inputs=remote.turn_inputs,
        list_workspace_files=remote.list_workspace_files,
        read_workspace_file=lambda path: (workspace / path).read_bytes(),
        cancel_turn=lambda: order.append("stop"),
    )

    def engine(**_kwargs):
        engine_calls.append(1)
        (workspace / "artifacts").mkdir(exist_ok=True)
        (workspace / "artifacts" / "result.txt").write_bytes(b"verified output")
        if bad_commit:
            (workspace / "artifacts" / "bad\nname").write_bytes(b"invalid name")
        return SimpleNamespace(answer="complete", route="flex", lm_calls=1, sandbox_errors=0)

    runtime = runtime_with(
        sandbox_mgr=manager,
        store=store,
        real_policy=True,
        engine=engine,
    )
    body = {"task": "produce output", "record_replay": record_replay, "idempotency_key": "inventory-first"}
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json=body)
        events = events_of(response)
        assert events[-1]["event"] == "done"
        terminal = [event for event in events if event["event"] in {"turn_complete", "error", "turn_cancelled"}]
        assert len(terminal) == 1
        if bad_commit:
            assert terminal[0]["event"] == "error"
            assert order == ["stop", "failed"] and records == []
            assert traced["status"] == "ERROR"
            assert traced["outputs"]["outcome"] == "failed"
            assert "bad\nname" not in traced["outputs"]["error"]
            assert "answer" not in traced["outputs"]
        else:
            assert terminal[0]["event"] == "turn_complete"
            assert terminal[0]["payload"]["artifacts"] == ["artifacts/result.txt"]
            assert order == ["persist"]
            first = records[0]
            saved = json.loads(first["turn_record_json"])
            assert saved["context_files"] == {"files/input.txt": hashlib.sha256(b"source").hexdigest()}
            if record_replay:
                snapshot = decode_snapshot(*first["replay_snapshot"])
                assert snapshot["context_files"] == saved["context_files"]
                assert snapshot["replay_files"] == {"files/input.txt": base64.b64encode(b"source").decode()}
            else:
                assert first["replay_snapshot"] is None
            replayed = events_of(client.post("/sessions/session-a/turn", json=body))
            recovered = next(event for event in replayed if event["event"] == "turn_complete")
            assert {key: value for key, value in recovered.items() if key != "timestamp"} == {
                key: value for key, value in terminal[0].items() if key != "timestamp"
            }
            assert len(engine_calls) == 1 and len(records) == 1
            second = {**body, "idempotency_key": "inventory-second"}
            assert events_of(client.post("/sessions/session-a/turn", json=second))[-2]["event"] == "turn_complete"
            assert len(engine_calls) == 2 and len(records) == 2
    assert scratch.read_bytes() == b"sandbox edits"
    assert (workspace / "bundle.txt").read_bytes() == b"scratch bundle"
    assert (workspace / "artifacts" / "result.txt").read_bytes() == b"verified output"


@pytest.mark.parametrize("environment", ["", "Sandbox environment: Python 3.12; pandas available."])
def test_selected_replay_persists_the_exact_pre_turn_inputs(traced, environment) -> None:
    content = b"id,value\n1,17\n"
    files = {"files/input.csv": content}
    persisted: list[dict[str, Any]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            list_workspace_files=lambda: list(files),
            read_workspace_file=lambda path: files[path],
            turn_inputs=lambda: {
                "digests": {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
                "environment": environment,
            },
        ),
        commit_turn=lambda **kwargs: (
            persisted.append(kwargs) or completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])
        ),
        engine=lambda **_kwargs: SimpleNamespace(
            answer="17", route="flex", learned_skill_note="", lm_calls=1, sandbox_errors=0
        ),
        context=WorkspaceDataset(
            "files/input.csv", "selected source", len(content), hashlib.sha256(content).hexdigest()
        ),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post(
            "/sessions/session-a/turn",
            json={"task": "sum the values", "record_replay": True},
        )

    assert response.status_code == 200
    assert len(persisted) == 1
    turn_record = persisted[0]["turn_record"]
    digest, compressed = persisted[0]["replay_snapshot"]
    snapshot = decode_snapshot(digest, compressed)
    assert turn_record["replay_snapshot_selected"] is True
    assert turn_record["replay_snapshot_sha256"] == digest
    assert snapshot["session_id"] == "session-a"
    assert snapshot["task"] == "sum the values"
    assert snapshot["workspace_manifest"] == "/workspace/files/input.csv"
    if environment:
        assert environment in snapshot["memory_summary"]
    assert snapshot["dataset_context"] == {
        "workspace_path": "files/input.csv",
        "description": "selected source",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    assert snapshot["replay_files"] == {"files/input.csv": base64.b64encode(content).decode("ascii")}
    assert snapshot["context_files"] == {"files/input.csv": hashlib.sha256(content).hexdigest()}
    assert traced["inputs"]["turn_id"] == turn_record["turn_id"]
    assert traced["inputs"]["replay_snapshot_sha256"] == digest


def test_oversized_replay_workspace_completes_without_a_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(turn_module, "MAX_REPLAY_FILE_BYTES", 4)
    files = {"files/input.csv": b"too large for replay"}
    persisted: list[dict[str, Any]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(list_workspace_files=lambda: list(files), read_workspace_file=lambda path: files[path]),
        commit_turn=lambda **kwargs: (
            persisted.append(kwargs) or completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])
        ),
        engine=lambda **_kwargs: SimpleNamespace(answer="ok", route="flex", lm_calls=1, sandbox_errors=0),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute"})

    events = events_of(response)
    [skipped] = [event for event in events if event["event"] == "replay_skipped"]
    assert (skipped["payload"]["bytes"], skipped["payload"]["limit"]) == (len(files["files/input.csv"]), 4)
    assert not [event for event in events if event["event"] == "error"]
    assert persisted[0]["replay_snapshot"] is None
    assert persisted[0]["turn_record"]["replay_snapshot_selected"] is False


def test_turn_provenance_matches_across_span_record_replay_and_terminal(traced) -> None:
    provenance = {
        "snapshot_name": "fleet-rlm-python313-v10",
        "snapshot_id": "snap-provider-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": "a" * 64,
        "snapshot_build_digest": "c" * 64,
    }
    persisted: list[dict[str, Any]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(snapshot_provenance=lambda: dict(provenance)),
        commit_turn=lambda **kwargs: (
            persisted.append(kwargs) or completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])
        ),
        engine=lambda **_kwargs: SimpleNamespace(answer="5", route="flex", lm_calls=1, sandbox_errors=0),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute", "record_replay": True})

    assert response.status_code == 200
    assert len(persisted) == 1
    turn_record = persisted[0]["turn_record"]
    digest, compressed = persisted[0]["replay_snapshot"]
    terminal = next(event for event in events_of(response) if event["event"] == "turn_complete")
    joined = (
        "route",
        "outcome",
        "deadline_source",
        "effective_seconds",
        "duration_seconds",
        "cleanup",
        *provenance,
    )
    assert {key: terminal["payload"][key] for key in joined} == {key: turn_record[key] for key in joined}
    span_joined = {key: traced["attributes"][key] for key in joined if key != "cleanup"}
    assert span_joined == {key: turn_record[key] for key in joined if key != "cleanup"}
    assert traced["cleanup"] == turn_record["cleanup"]
    assert traced["outputs"]["turn_id"] == terminal["turn_id"] == turn_record["turn_id"]
    assert traced["outputs"]["answer"] == terminal["payload"]["answer"] == turn_record["answer"]
    assert {key: decode_snapshot(digest, compressed)[key] for key in provenance} == provenance
    # Stored record and span duration are one measurement; MLflow's own span duration differs
    # only at the span boundary, within the documented tolerance.
    assert abs(traced["attributes"]["duration_seconds"] - turn_record["duration_seconds"]) <= 2.0


def test_snapshot_provenance_survives_a_preparation_failure_after_preflight(traced, monkeypatch) -> None:
    """A failure after the provider resolved the identity keeps the span and the Turn in agreement."""
    provenance = {
        "snapshot_name": "fleet-rlm-python313-v10",
        "snapshot_id": "snap-provider-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": "b" * 64,
    }
    finished: list[tuple] = []
    manager = FakeManager(snapshot_provenance=lambda: dict(provenance))

    def resolve() -> dict[str, Any]:
        manager.snapshot_resolved_callback(SimpleNamespace(provenance=lambda: dict(provenance)))
        return {"digests": {}, "environment": ""}

    def oversized_replay(_snapshot):
        raise ValueError("Replay snapshot exceeds the 8 MB limit")

    manager.turn_inputs = resolve
    monkeypatch.setattr(turn_module, "encode_snapshot", oversized_replay)
    runtime = runtime_with(
        sandbox_mgr=manager,
        store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute", "record_replay": True})

    failure = next(event for event in events_of(response) if event["event"] == "error")
    assert {key: traced["attributes"][key] for key in provenance} == provenance
    assert {key: failure["payload"][key] for key in provenance} == provenance
    assert {key: finished[0][2][key] for key in provenance} == provenance
    assert manager.snapshot_resolved_callback is None, "the Turn restores the manager's previous callback"


def test_a_reused_snapshot_identity_reaches_the_span_before_preparation(traced) -> None:
    """An identity resolved on an earlier Turn is applied before this Turn can fail."""
    provenance = {
        "snapshot_name": "fleet-rlm-python313-v10",
        "snapshot_id": "snap-provider-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": "c" * 64,
    }
    identity = SimpleNamespace(provenance=lambda: dict(provenance))
    finished: list[tuple] = []

    def explode():
        raise ValueError("workspace changed while the Turn was prepared")

    manager = FakeManager(
        snapshot_identity=identity,
        snapshot_provenance=lambda: dict(provenance),
        turn_inputs=explode,
    )
    runtime = runtime_with(
        sandbox_mgr=manager,
        store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute"})

    assert {key: traced["attributes"][key] for key in provenance} == provenance
    failure = next(event for event in events_of(response) if event["event"] == "error")
    assert {key: failure["payload"][key] for key in provenance} == provenance


def test_no_snapshot_provenance_is_invented_when_preflight_never_resolved(traced) -> None:
    finished: list[tuple] = []

    def explode():
        raise RuntimeError("provider unavailable")

    runtime = runtime_with(
        sandbox_mgr=FakeManager(turn_inputs=explode),
        store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute"})

    attributes = traced.get("attributes", {})
    assert [key for key in attributes if key.startswith("snapshot_")] == []
    assert "root_sandbox_id" not in attributes
    failure = next(event for event in events_of(response) if event["event"] == "error")
    assert [key for key in failure["payload"] if key.startswith("snapshot_")] == []
    assert "root_sandbox_id" not in failure["payload"]
    assert [key for key in finished[0][2] if key.startswith("snapshot_")] == []
    assert "root_sandbox_id" not in finished[0][2]


@pytest.mark.parametrize("fail_preparation", [False, True])
def test_a_failed_turn_reports_an_error_with_its_own_trace_id(traced, fail_preparation):
    traced["span_trace_id"] = "trace-failed-id"
    finished: list[tuple] = []

    def hydrate(_session):
        if fail_preparation:
            raise RuntimeError("preparation failed")
        return "summary", "history"

    def failing_engine(**_kwargs):
        raise AdapterParseError(adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[")

    runtime = runtime_with(
        load_context=hydrate,
        store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
        engine=failing_engine,
    )
    with opened_client(runtime) as (client, app):
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "anything"})
    failure = next(event for event in events_of(response) if event["event"] == "error")
    if fail_preparation:
        assert failure["payload"]["trace_id"] == "trace-failed-id"
        assert traced["inputs"]["session_id"] == "session-a"
        assert traced["inputs"]["task"] == "anything"
        assert traced["inputs"]["turn_id"] == failure["payload"]["request_id"]
    else:
        assert failure["payload"]["trace_id"] == "trace-failed-id"
    expected = "The Turn failed" if fail_preparation else "invalid structured response"
    assert expected in failure["payload"]["error"]
    assert finished[0][1] == "failed"
    assert not runtime.turn_lock.locked()


def _run_turn_direct(
    runtime: Any,
    *,
    emitted: list[dict[str, Any]],
    order: list[str] | None = None,
    turn_id: str = "turn-teardown",
    timeout_seconds: int | None = None,
    idempotency_key: str | None = None,
    requested_skills: tuple[str, ...] = (),
) -> None:
    """Run one Turn with an ordered emit log, so `done` is observable outside the SSE route."""

    def emit(event: dict[str, Any]) -> None:
        if order is not None:
            order.append(event["event"])
        emitted.append(event)

    turn_module.run_turn(
        runtime,
        api_settings(),
        session_id="session-a",
        turn_id=turn_id,
        task="verify termination",
        idempotency_key=idempotency_key or turn_id,
        mlflow_experiment_id=None,
        callback=object(),
        emit=emit,
        timeout_seconds=timeout_seconds,
        requested_skills=requested_skills,
    )


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("change", ["edited", "deleted"])
def test_completed_required_turn_replays_recorded_intent_without_stopping_root(monkeypatch, tmp_path, change):
    from fleet_rlm.skills import SkillManager

    folder = tmp_path / "required"
    folder.mkdir()
    path = folder / "SKILL.md"
    raw = b"---\nname: required\ndescription: Required source\n---\nOriginal instructions"
    path.write_bytes(raw)
    runtime, state = durable_runtime()
    files = {}
    runtime.skill_mgr = SkillManager(runtime.sandbox_mgr, tmp_path, None)
    runtime.sandbox_mgr.exec_remote_python = lambda _code: "{}"
    runtime.sandbox_mgr.write_workspace_files = files.update
    runtime.sandbox_mgr.list_workspace_files = lambda: list(files)
    runtime.sandbox_mgr.read_workspace_file = files.__getitem__
    emitted = []
    _run_turn_direct(runtime, emitted=emitted, idempotency_key="required-key", requested_skills=("required",))
    completed = next(event for event in emitted if event["event"] == "turn_complete")
    original = completed["required_skills"]
    assert original[0]["name"] == "required" and original[0]["source"] == "workspace"
    if change == "edited":
        path.write_bytes(raw + b" changed")
    else:
        path.unlink()
    resolve = SkillManager.resolve_required
    monkeypatch.setattr(
        SkillManager, "resolve_required", lambda *_args: pytest.fail("Completed receipts must not recapture sources")
    )
    replay = []
    _run_turn_direct(
        runtime, emitted=replay, turn_id="retry", idempotency_key="required-key", requested_skills=("required",)
    )
    replayed = next(event for event in replay if event["event"] == "turn_complete")
    assert replayed["required_skills"] == original
    assert replayed["turn_id"] == completed["turn_id"]
    assert len(state.executions) == 1 and state.cancellations == []
    if change == "edited":
        monkeypatch.setattr(SkillManager, "resolve_required", resolve)
        state.rows["required-key"]["status"] = "failed"
        conflict = []
        _run_turn_direct(
            runtime,
            emitted=conflict,
            turn_id="retry-failed",
            idempotency_key="required-key",
            requested_skills=("required",),
        )
        assert next(event for event in conflict if event["event"] == "error")["error_type"] == "idempotency_conflict"
        assert state.cancellations == []


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_required_receipt_and_source_conflict_do_not_own_a_deadline(monkeypatch, status):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\nname: required\ndescription: Required\n---\nOriginal instructions"
    skill = captured_required_skill("required", "workspace", (("SKILL.md", raw),))
    changed = captured_required_skill("required", "workspace", (("SKILL.md", raw + b" changed"),))
    runtime, state = durable_runtime()
    runtime.claim_turn(
        "original", "key", "verify termination", requested_skills=("required",), required_skills=(skill,)
    )
    state.rows["key"].update(
        status=status, result={"event": "turn_complete", "turn_id": "original", "answer": "original"}
    )
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    root = object()
    manager._sandbox = root
    cancellations = []

    def cancel(_manager):
        cancellations.append(1)
        _manager._sandbox = None

    from fleet_rlm.sandbox import manager as manager_module

    clock = [time.monotonic()]
    monkeypatch.setattr(manager_module, "time", SimpleNamespace(**(vars(time) | {"monotonic": lambda: clock[0]})))

    def unexpected_watchdog(*_args, **_kwargs):
        pytest.fail("Receipt replay and admission conflicts must not arm a Turn watchdog")

    monkeypatch.setattr(
        manager_module, "threading", SimpleNamespace(**(vars(threading) | {"Timer": unexpected_watchdog}))
    )

    def resolve(_names):
        clock[0] += 10.2  # Cross the public deadline without waiting in real time.
        return (changed,)

    monkeypatch.setattr(DaytonaSandboxManager, "cancel_turn", cancel)
    runtime.sandbox_mgr = manager
    runtime.skill_mgr = SimpleNamespace(resolve_required=resolve)
    emitted = []
    _run_turn_direct(
        runtime, emitted=emitted, idempotency_key="key", requested_skills=("required",), timeout_seconds=10
    )
    assert cancellations == [] and manager._sandbox is root
    assert state.executions == []
    if status == "completed":
        assert emitted[0]["event"] == "turn_complete" and emitted[0]["turn_id"] == "original"
    else:
        assert emitted[0]["error_type"] == "idempotency_conflict"


@pytest.mark.asyncio
@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("status", ["completed", "failed", "new"])
@pytest.mark.parametrize("disconnect", ["poll", "close"])
async def test_disconnect_during_required_admission_preserves_receipts_and_conflicts(monkeypatch, status, disconnect):
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\nname: required\ndescription: Required\n---\nOriginal instructions"
    skill = captured_required_skill("required", "workspace", (("SKILL.md", raw),))
    changed = captured_required_skill("required", "workspace", (("SKILL.md", raw + b" changed"),))
    runtime, state = durable_runtime()
    if status != "new":
        runtime.claim_turn("original", "key", "verify", requested_skills=("required",), required_skills=(skill,))
        state.rows["key"].update(
            status=status, result={"event": "turn_complete", "turn_id": "original", "answer": "original"}
        )
    entered, release = threading.Event(), threading.Event()
    cancelling = asyncio.Event()
    lookup = runtime.store.turn_by_key

    def wait():
        entered.set()
        assert release.wait(5), "admission must be released after disconnect starts"

    def find(*args):
        if status == "completed":
            wait()
        return lookup(*args)

    def resolve(_names):
        wait()
        return (changed,)

    runtime.store.turn_by_key = find
    runtime.skill_mgr = SimpleNamespace(
        resolve_required=resolve,
        sync_local_skills_to_volume=lambda **_kwargs: 1,
        build_catalog_xml=lambda: "<available_skills />",
    )
    files = {"skills/required/SKILL.md": raw + b" changed"}
    runtime.sandbox_mgr.list_workspace_files = lambda: list(files)
    runtime.sandbox_mgr.read_workspace_file = files.__getitem__
    root = runtime.sandbox_mgr._sandbox = object()
    cancel = turns_route._cancel

    def stop():
        state.cancellations.append(1)
        runtime.sandbox_mgr._cancelled.set()
        runtime.sandbox_mgr._sandbox = None

    runtime.sandbox_mgr.cancel_turn = stop

    async def cancelling_request(*args):
        cancelling.set()
        await cancel(*args)

    def finish(_session, turn_id, outcome, result):
        row = next(row for row in state.rows.values() if row["turn_id"] == turn_id)
        row.update(status=outcome, result=result)

    runtime.store.finish_turn = finish
    monkeypatch.setattr(turns_route, "_cancel", cancelling_request)
    monkeypatch.setattr(turns_route, "_DISCONNECT_POLL_SECONDS", 0.01)

    async def is_disconnected():
        return entered.is_set()

    async def consume(stream):
        return [event async for event in stream]

    with turns_route.create_turn_executor() as executor:
        stream = turns_route.stream_turn(
            "session-a",
            TurnRequest(task="verify", idempotency_key="key", requested_skills=["required"]),
            SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(mlflow_experiment_id=None)), is_disconnected=is_disconnected
            ),
            runtime,
            api_settings(),
            executor,
            AsyncExitStack(),
        )
        closing = None
        try:
            assert (await anext(stream)).data["event"] == "turn_status"
            assert await asyncio.to_thread(entered.wait, 2)
            closing = asyncio.create_task(stream.aclose() if disconnect == "close" else consume(stream))
            await asyncio.wait_for(cancelling.wait(), 2)
        finally:
            release.set()
            if closing is not None:
                await asyncio.wait_for(closing, 5)
            await stream.aclose()
    assert state.executions == []
    if status == "new":
        assert state.cancellations == [1]
        assert state.rows["key"]["status"] == "cancelled"
    else:
        assert state.cancellations == [] and runtime.sandbox_mgr._sandbox is root
        assert state.rows["key"]["status"] == status


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("mode", ["hybrid", "direct"])
def test_required_admission_refusal_is_durable_and_caller_safe(tmp_path, mode):
    from fleet_rlm.skills import SkillManager

    runtime, state = durable_runtime()
    runtime.engine.execution_mode = mode
    runtime.skill_mgr = SkillManager(runtime.sandbox_mgr, tmp_path, None)

    def finish(_session, turn_id, status, result):
        row = next(row for row in state.rows.values() if row["turn_id"] == turn_id)
        row.update(status=status, result=result)

    runtime.store.finish_turn = finish
    emitted = []
    _run_turn_direct(runtime, emitted=emitted, idempotency_key="refused", requested_skills=("missing",))
    failure = next(event for event in emitted if event["event"] == "error")
    assert str(tmp_path) not in failure["error"]
    assert state.rows["refused"]["status"] == "failed" and state.executions == []
    assert state.rows["refused"]["result"]["requested_skills"] == ["missing"]
    assert runtime.turn_status(failure["turn_id"])["status"] == "failed"


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("failure", [None, "preparation", "generation"])
def test_turn_associates_actual_engine_before_root_closes(monkeypatch, failure):
    observed = []

    def load(_session):
        if failure == "preparation":
            raise RuntimeError("prepare failed")
        return "", ""

    def engine(**_kwargs):
        if failure == "generation":
            raise RuntimeError("generate failed")
        return SimpleNamespace(answer="verified", route="flex", learned_skill_note="")

    engine.program = object()
    runtime = runtime_with(load_context=load, engine=engine)

    def associate(actual, config_digest, experiment, span):
        observed.append((actual, config_digest, experiment, span))

    monkeypatch.setattr(turn_module, "associate_turn_agent", associate)
    emitted = []
    _run_turn_direct(runtime, emitted=emitted)
    assert len(observed) == 1
    assert observed[0][0] is runtime.engine.program and len(observed[0][1]) == 64
    assert emitted[-1] == {"event": "done"}
    assert emitted[-2]["event"] == ("error" if failure else "turn_complete")


def _failed_turn_runtime(manager: FakeManager, stored: list[tuple[str, str, dict[str, Any]]], engine: Any):
    return runtime_with(sandbox_mgr=manager, store=_recording_store(stored), engine=engine)


def _recording_store(stored: list[tuple[str, str, dict[str, Any]]]) -> SimpleNamespace:
    """A Store fake that records the terminal status and result of a failing or cancelled Turn."""

    def finish_turn(_session: str, turn_id: str, status: str, result: dict[str, Any]) -> None:
        stored.append((turn_id, status, result))

    return store_with(finish_turn=finish_turn)


def test_a_completed_turn_records_not_required_and_never_stops_the_root(traced) -> None:
    order: list[str] = []
    records: list[dict[str, Any]] = []

    def cancel_turn() -> None:
        order.append("cancel_turn")

    def engine(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(answer="kept warm", route="direct")

    def persist(**kwargs: Any) -> dict[str, Any]:
        records.append(kwargs["turn_record"])
        return completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])

    manager = FakeManager(cancel_turn=cancel_turn, end_turn=lambda: order.append("end_turn"))
    runtime = runtime_with(
        sandbox_mgr=manager,
        commit_turn=persist,
        engine=engine,
    )
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted, order=order)

    assert "cancel_turn" not in order, "a completed Turn must not stop its root sandbox"
    assert order == ["end_turn", "turn_complete", "done"]
    assert [event["event"] for event in emitted] == ["turn_complete", "done"]
    assert emitted[0]["cleanup"] == CLEANUP_NOT_REQUIRED
    assert records[0]["cleanup"] == CLEANUP_NOT_REQUIRED
    assert traced["cleanup"] == CLEANUP_NOT_REQUIRED


def test_provider_bounds_are_reported_on_event_record_and_span_without_a_deadline(traced) -> None:
    records: list[dict[str, Any]] = []
    bounds = {
        "ttl_minutes": 1440,
        "auto_stop_interval": 60,
        "maximum_lifespan_minutes": 2880,
        "provider_owned": True,
    }

    def persist(**kwargs: Any) -> dict[str, Any]:
        records.append(kwargs["turn_record"])
        return completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])

    runtime = runtime_with(
        sandbox_mgr=FakeManager(provider_bounds=lambda: bounds),
        commit_turn=persist,
        engine=lambda **_kwargs: SimpleNamespace(answer="kept warm", route="direct"),
    )
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted, timeout_seconds=0)

    completion = emitted[0]
    for surface in (completion, records[0]):
        assert {key: surface[key] for key in bounds} == bounds
        assert surface["effective_seconds"] is None, "provider bounds never become a Fleet deadline"
    assert json.loads(traced["provider_bounds"]) == bounds


def test_two_successful_turns_record_the_same_live_root_sandbox_id(traced) -> None:
    records: list[dict[str, Any]] = []

    def persist(**kwargs: Any) -> dict[str, Any]:
        records.append(kwargs["turn_record"])
        return completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])

    runtime = runtime_with(
        sandbox_mgr=FakeManager(live_sandbox_id="sandbox-warm-root"),
        commit_turn=persist,
        engine=lambda **_kwargs: SimpleNamespace(answer="kept warm", route="direct"),
    )
    for index in range(2):
        emitted: list[dict[str, Any]] = []
        _run_turn_direct(runtime, emitted=emitted, turn_id=f"turn-warm-{index}")
        assert emitted[0]["cleanup"] == CLEANUP_NOT_REQUIRED
        assert emitted[0]["root_sandbox_id"] == "sandbox-warm-root"

    assert [record["root_sandbox_id"] for record in records] == ["sandbox-warm-root"] * 2
    assert traced["attributes"]["root_sandbox_id"] == "sandbox-warm-root"


@pytest.mark.parametrize("error_kind", ["ordinary", "parse", "bridged_parse"])
def test_a_failed_turn_stops_the_root_before_its_terminal_event_and_done(traced, error_kind) -> None:
    order: list[str] = []
    stored: list[tuple[str, str, dict[str, Any]]] = []
    manager = FakeManager(live_sandbox_id="sandbox-failed-root")

    def cancel_turn() -> None:
        order.append("cancel_turn")
        # The real manager discards the stopped handle once teardown has run.
        manager.live_sandbox_id = None

    def engine(**_kwargs: Any) -> SimpleNamespace:
        if error_kind == "parse":
            raise AdapterParseError(
                adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="secret-reply"
            )
        if error_kind == "bridged_parse":
            from fleet_rlm.sandbox.interpreter import StructuredResponseError

            raise StructuredResponseError("secret-reply")
        raise RuntimeError("engine failed")

    manager.cancel_turn = cancel_turn
    manager.end_turn = lambda: order.append("end_turn")
    runtime = _failed_turn_runtime(manager, stored, engine)
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted, order=order)

    # Teardown precedes acknowledgment: cancel_turn, end_turn, the outcome, then stream end.
    assert order == ["cancel_turn", "end_turn", "error", "done"]
    assert emitted[0]["event"] == "error" and emitted[0]["cleanup"] == "stopped"
    assert stored[0][1] == "failed"
    assert stored[0][2]["cleanup"] == "stopped"
    assert emitted[0]["root_sandbox_id"] == "sandbox-failed-root"
    assert stored[0][2]["root_sandbox_id"] == "sandbox-failed-root"
    assert traced["root_sandbox_id"] == "sandbox-failed-root"
    assert traced["cleanup"] == "stopped"
    assert manager.live_sandbox_id is None

    assert ("invalid structured response" in emitted[0]["error"]) == (error_kind != "ordinary")
    assert "secret-reply" not in json.dumps(emitted)


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("failure", ["explicit_cancellation", "deadline_expiry"])
def test_cancelled_and_deadline_turns_are_stored_as_cancelled(failure) -> None:
    calls: list[str] = []
    stored: list[tuple[str, str, dict[str, Any]]] = []
    manager = FakeManager(cancel_turn=lambda: calls.append("cancel_turn"))

    def check_turn() -> None:
        manager._cancelled.set()
        raise TimeoutError("Turn deadline exceeded")

    manager.check_turn = check_turn

    def engine(**_kwargs: Any) -> SimpleNamespace:
        if failure == "explicit_cancellation":
            manager._cancelled.set()
            raise RuntimeError("Turn cancelled")
        manager.check_turn()
        return SimpleNamespace(answer="unreachable", route="direct")

    runtime = _failed_turn_runtime(manager, stored, engine)
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted)

    assert [event["event"] for event in emitted] == ["turn_cancelled", "done"]
    assert emitted[0]["cleanup"] == "stopped"
    assert stored[0][1] == "cancelled"
    assert calls == ["cancel_turn"]


def test_a_raising_provider_stop_records_unconfirmed_and_still_ends_the_stream(traced, caplog) -> None:
    order: list[str] = []
    stored: list[tuple[str, str, dict[str, Any]]] = []

    def cancel_turn() -> None:
        order.append("cancel_turn")
        raise RuntimeError("provider refused the stop; credential=secret")

    def engine(**_kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("engine failed")

    manager = FakeManager(cancel_turn=cancel_turn, end_turn=lambda: order.append("end_turn"))
    runtime = _failed_turn_runtime(manager, stored, engine)
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted, order=order)

    assert order == ["cancel_turn", "end_turn", "error", "done"]
    assert emitted[0]["cleanup"] == "unconfirmed"
    assert stored[0][1] == "failed"
    assert stored[0][2]["cleanup"] == "unconfirmed"
    assert traced["cleanup"] == "unconfirmed"
    # The bounded warning never carries the provider text to a client or a log sink.
    assert "secret" not in json.dumps(emitted)
    assert any("did not confirm" in record.message for record in caplog.records)


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize("transport_stopped", [False, True])
def test_an_unknown_execution_outcome_stays_terminal_and_tears_down_once(transport_stopped) -> None:
    calls: list[str] = []
    stored: list[tuple[str, str, dict[str, Any]]] = []

    def engine(**_kwargs: Any) -> SimpleNamespace:
        if transport_stopped:
            manager.request_cancellation()
        raise ExecutionOutcomeUnknownError("transport lost after execution started")

    def cancel_turn():
        calls.append("cancel_turn")
        manager._cancelled.set()

    manager = FakeManager(cancel_turn=cancel_turn)
    runtime = _failed_turn_runtime(manager, stored, engine)
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted)

    assert [event["event"] for event in emitted] == ["error", "done"]
    assert emitted[0]["cleanup"] == "stopped"
    assert stored[0][1] == "failed"
    assert calls == ["cancel_turn"], "an unknown outcome tears down exactly once"
    assert turn_module._replayed({"status": "failed", "turn_id": "turn-teardown", "result": {}}) == {
        "event": "error",
        "request_id": "turn-teardown",
        "error": "Prior turn has an uncertain or failed outcome; it was not replayed.",
    }


@pytest.mark.usefixtures("traced")
def test_a_replayed_unknown_outcome_creates_no_new_execution() -> None:
    prior = {"turn_id": "turn-original", "status": "failed", "result": {"event": "error"}}
    ran: list[str] = []

    def never(**_kwargs: Any) -> SimpleNamespace:
        ran.append("engine")
        return SimpleNamespace(answer="unreachable", route="direct")

    runtime = runtime_with(
        claim_turn=lambda *_args: prior,
        engine=never,
    )
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted)

    assert ran == []
    assert [event["event"] for event in emitted] == ["error", "done"]
    assert emitted[0]["error"] == "Prior turn has an uncertain or failed outcome; it was not replayed."


def _snapshot_refusal(state: str = "active", cpu: int = 4, mem: int = 8):
    """Produce the exact refusal the manager raises during Turn preparation."""
    from fleet_rlm.sandbox import snapshots as snapshot_module

    dto = None if state == "missing" else SimpleNamespace(id="snap-1", state=state, cpu=cpu, mem=mem, disk=8)
    service = SimpleNamespace(get=lambda _name: dto)
    with pytest.raises(snapshot_module.SnapshotUnavailableError) as error:
        snapshot_module.preflight(
            service,
            name=snapshot_module.SNAPSHOT_NAME,
            api_key="key",
            org_id="org",
            refresh=True,
        )
    return error.value


@pytest.mark.parametrize(
    ("state", "cpu", "mem", "observed"),
    [
        ("missing", 4, 8, "state=missing"),
        ("inactive", 4, 8, "state=inactive"),
        ("active", 3, 8, "cpu=3"),
        ("active", 4, 6, "mem=6"),
    ],
)
def test_snapshot_preflight_refusal_reaches_the_client_and_the_stored_failure(state, cpu, mem, observed) -> None:
    from fleet_rlm.sandbox import snapshots as snapshot_module

    refusal = _snapshot_refusal(state, cpu, mem)
    finished: list[tuple] = []

    def refuse():
        raise refusal

    runtime = runtime_with(
        sandbox_mgr=FakeManager(turn_inputs=refuse),
        store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute pi"})

    failure = next(event for event in events_of(response) if event["event"] == "error")
    message = failure["payload"]["error"]
    assert "The Turn failed" not in message
    assert snapshot_module.SNAPSHOT_NAME in message
    assert observed in message
    assert f"cpu>={snapshot_module.CPU_FLOOR}" in message
    assert f"mem>={snapshot_module.MEMORY_FLOOR}" in message
    if state == "inactive":
        assert "fleet snapshot activate" in message
    else:
        assert snapshot_module.BUILD_COMMAND in message
    # The stored failure carries the same bounded text, and no sandbox was provisioned.
    assert finished[0][1] == "failed"
    assert finished[0][2]["error"] == message
    assert runtime.sandbox_mgr._sandbox is None


def test_other_preparation_failures_stay_sanitized_on_both_surfaces() -> None:
    finished: list[tuple] = []
    leaked = "/Users/operator/private/secret.py credential=super-secret provider-body"

    def explode():
        raise RuntimeError(leaked)

    runtime = runtime_with(
        sandbox_mgr=FakeManager(turn_inputs=explode),
        store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
    )
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "compute pi"})

    failure = next(event for event in events_of(response) if event["event"] == "error")
    request_id = failure["payload"]["request_id"]
    assert failure["payload"]["error"] == f"The Turn failed (request {request_id})."
    assert finished[0][2]["error"] == failure["payload"]["error"]
    assert leaked not in response.text
    assert "/Users/operator" not in response.text and "super-secret" not in response.text


@pytest.mark.parametrize("status", ["completed", "running", "failed"])
def test_a_replayed_turn_returns_the_original_outcome_without_running(status, traced):
    prior = {
        "turn_id": "original-turn",
        "status": status,
        "result": {"event": "turn_complete", "answer": "Verified result", "route": "rlm"},
    }

    def never(**_kwargs):
        raise AssertionError("a replayed turn must not run the engine")

    runtime = runtime_with(
        claim_turn=lambda *_args: prior,
        engine=never,
    )
    store = store_with(
        turn_status=lambda _session, turn_id: (
            {**prior, "updated_at": "2026-10-16T00:00:00"} if turn_id == "original-turn" else None
        )
    )
    with opened_client(runtime, store=store) as (client, _):
        response = client.post("/sessions/s1/turn", json={"task": "same task", "idempotency_key": "same-key"})
        outcomes = [event for event in events_of(response) if event["event"] in {"turn_complete", "error"}]
        assert len(outcomes) == 1 and outcomes[0]["turn_id"] == "original-turn"
        recovered = client.get("/sessions/s1/turns/original-turn")
    assert recovered.status_code == 200 and recovered.json()["status"] == status
    if status == "completed":
        assert outcomes[0]["payload"]["answer"] == recovered.json()["result"]["answer"]
    else:
        assert outcomes[0]["event"] == "error"
    assert traced["destinations"] == []


@pytest.mark.parametrize("configured", [True, False])
def test_shutdown_flushes_pending_traces_only_when_tracing_is_configured(monkeypatch, configured):
    flushed: list[bool] = []
    monkeypatch.setattr(api_app.mlflow, "flush_trace_async_logging", lambda: flushed.append(True))
    with opened_client() as (_client, app):
        if configured:
            app.state.mlflow_experiment_id = "experiment-test-id"
        assert flushed == []
    assert flushed == ([True] if configured else [])


def test_reusing_an_idempotency_key_for_another_task_names_the_conflict() -> None:
    def conflict(*_args):
        raise IdempotencyConflictError("Idempotency key already belongs to another task")

    with opened_client(runtime_with(claim_turn=conflict)) as (client, _):
        response = client.post("/sessions/session-a/turn", json={"task": "other", "idempotency_key": "k"})

    events = events_of(response)
    assert [event["event"] for event in events] == ["turn_status", "error", "done"]
    assert events[1]["payload"]["error"] == "Idempotency key already belongs to another task"


@pytest.mark.usefixtures("traced")
def test_known_empty_session_routes_before_preparing_workspace(monkeypatch) -> None:
    runtime = runtime_with()
    monkeypatch.setattr(type(runtime), "workspace_state", property(lambda _self: "known_empty"), raising=False)
    routes = []
    calls = []

    class Engine:
        execution_mode = "hybrid"
        root_lm = SimpleNamespace(model="test.root")
        sub_lm = SimpleNamespace(model="test.sub")
        compiled = SimpleNamespace(digest="")
        compiled_path = None

        def preview_route(self, **kwargs):
            routes.append(kwargs)
            return Route.DIRECT

        def __call__(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                answer="Hi!",
                route="direct",
                execution_evidence={
                    "artifacts": {"complete": True, "files": {}},
                    "children": [],
                    "children_complete": True,
                },
            )

    runtime.engine = Engine()
    runtime.skill_mgr = SimpleNamespace(build_local_catalog_xml=lambda: "<available_skills />")
    monkeypatch.setattr(
        turn_module,
        "_prepare_inputs",
        lambda *_args, **_kwargs: pytest.fail("known-empty direct route should not prepare Daytona"),
    )
    events = []
    turn_module.run_turn(
        runtime,
        api_settings(),
        session_id="session-a",
        turn_id="empty-direct",
        task="hi",
        idempotency_key="empty-direct",
        mlflow_experiment_id=None,
        callback=object(),
        emit=events.append,
    )
    assert routes and calls[0]["selected_route"] is Route.DIRECT
    assert events[-2]["event"] == "turn_complete"
    assert events[-1]["event"] == "done"


def test_known_empty_direct_turn_captures_local_skill_digests_replay_and_manifest(tmp_path: Path, monkeypatch, traced):
    from fleet_rlm.skills import SkillManager

    skill_dir = tmp_path / "skills" / "ledger-audit"
    (skill_dir / "references").mkdir(parents=True)
    skill_md = b"---\nname: ledger-audit\ndescription: Audit ledgers.\n---\n\nVerify totals.\n"
    guide_md = b"Use exact decimal arithmetic.\n"
    (skill_dir / "SKILL.md").write_bytes(skill_md)
    (skill_dir / "references" / "guide.md").write_bytes(guide_md)

    persisted: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    callback = object()

    class Engine:
        execution_mode = "hybrid"
        root_lm = SimpleNamespace(model="test.root")
        sub_lm = SimpleNamespace(model="test.sub")
        compiled = SimpleNamespace(digest="")
        compiled_path = None
        program = SimpleNamespace()

        def preview_route(self, **kwargs):
            assert callback in traced["callbacks"]
            routes.append(kwargs)
            return Route.DIRECT

        def __call__(self, **_kwargs):
            return SimpleNamespace(
                answer="Hi!",
                route="direct",
                execution_evidence={
                    "artifacts": {"complete": True, "files": {}},
                    "children": [],
                    "children_complete": True,
                },
            )

    runtime = runtime_with(
        sandbox_mgr=FakeManager(session_id="session-a"),
        commit_turn=lambda **kwargs: (
            persisted.append(kwargs) or completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])
        ),
        workspace_state="known_empty",
    )
    runtime.engine = Engine()
    runtime.skill_mgr = SkillManager(runtime.sandbox_mgr, tmp_path / "skills", None)
    monkeypatch.setattr(
        turn_module,
        "_prepare_inputs",
        lambda *_args, **_kwargs: pytest.fail("known-empty direct route should not prepare Daytona"),
    )

    events: list[dict[str, Any]] = []
    turn_module.run_turn(
        runtime,
        api_settings(),
        session_id="session-a",
        turn_id="empty-direct-skills",
        task="hi",
        idempotency_key="empty-direct-skills",
        mlflow_experiment_id=None,
        callback=callback,
        emit=events.append,
        record_replay=True,
    )

    expected_files = {
        "skills/ledger-audit/SKILL.md": skill_md,
        "skills/ledger-audit/references/guide.md": guide_md,
    }
    expected_manifest = "/workspace/skills/ledger-audit/SKILL.md, /workspace/skills/ledger-audit/references/guide.md"
    expected_digests = {path: hashlib.sha256(raw).hexdigest() for path, raw in expected_files.items()}
    assert routes[0]["workspace_manifest"] == expected_manifest
    record = persisted[0]["turn_record"]
    digest, compressed = persisted[0]["replay_snapshot"]
    snapshot = decode_snapshot(digest, compressed)
    assert record["workspace_manifest"] == snapshot["workspace_manifest"] == expected_manifest
    assert record["context_files"] == snapshot["context_files"] == expected_digests
    assert snapshot["replay_files"] == {
        path: base64.b64encode(raw).decode("ascii") for path, raw in expected_files.items()
    }
    assert events[-1]["event"] == "done"


@pytest.mark.usefixtures("traced")
def test_known_empty_execution_route_invalidates_state_before_preparation(monkeypatch) -> None:
    runtime = runtime_with()
    runtime.sandbox_mgr.session_id = "session-a"
    runtime.workspace_state = "known_empty"
    calls = []
    routes = []

    class Engine:
        execution_mode = "hybrid"
        root_lm = SimpleNamespace(model="test.root")
        sub_lm = SimpleNamespace(model="test.sub")
        compiled = SimpleNamespace(digest="")
        compiled_path = None

        def preview_route(self, **_kwargs):
            routes.append(True)
            return Route.RLM

        def __call__(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                answer="computed",
                route="rlm",
                execution_evidence={
                    "artifacts": {"complete": True, "files": {}},
                    "children": [],
                    "children_complete": True,
                },
            )

    runtime.engine = Engine()
    runtime.skill_mgr = SimpleNamespace(build_local_catalog_xml=lambda: "<available_skills />")
    runtime.load_context = lambda _session_id: pytest.fail("known-empty RLM preview should not pre-load context")
    runtime.memories = lambda *_args, **_kwargs: pytest.fail("known-empty RLM preview should not pre-load memories")
    prepared = {
        "manifest": "/workspace/files (empty)",
        "context_files": {},
        "skills_catalog": "<available_skills />",
        "memory_summary": "",
        "session_history": "",
        "replay_files": {},
        "replay_selected": False,
        "replay_skipped_bytes": None,
    }
    monkeypatch.setattr(turn_module, "_prepare_inputs", lambda *_args, **_kwargs: deepcopy(prepared))
    events = []
    turn_module.run_turn(
        runtime,
        api_settings(),
        session_id="session-a",
        turn_id="empty-execution",
        task="compute something",
        idempotency_key="empty-execution",
        mlflow_experiment_id=None,
        callback=object(),
        emit=events.append,
        record_replay=False,
    )
    assert runtime.workspace_state == "unknown", (events, routes, calls)
    assert calls[0]["selected_route"] is Route.RLM
    assert events[-2]["event"] == "turn_complete"
    assert events[-1]["event"] == "done"


def test_prepare_inputs_hashes_remotely_without_downloading_files() -> None:
    digests = {"files/a.txt": hashlib.sha256(b"a").hexdigest(), "skills/x/SKILL.md": hashlib.sha256(b"x").hexdigest()}

    def no_download(_path):
        raise AssertionError("non-replay Turns must not download workspace files")

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {"digests": dict(digests), "environment": "Sandbox environment: X."},
            read_workspace_file=no_download,
        )
    )

    runtime.sandbox_mgr.session_id = "session-a"
    runtime.workspace_state = "known_empty"
    inputs = turn_module._prepare_inputs(runtime, "session-a", "task", record_replay=False)

    assert runtime.workspace_state == "unknown"
    assert inputs["context_files"] == digests
    assert inputs["manifest"] == "/workspace/files/a.txt, /workspace/skills/x/SKILL.md"
    assert "Sandbox environment: X." in inputs["memory_summary"]
    assert inputs["replay_files"] == {} and inputs["replay_selected"] is False


def test_prepare_inputs_skips_replay_capture_for_an_oversized_workspace(monkeypatch) -> None:
    monkeypatch.setattr(turn_module, "MAX_REPLAY_FILE_BYTES", 8)
    files = {"files/a.txt": b"12345", "files/b.txt": b"67890"}
    runtime = runtime_with(
        sandbox_mgr=FakeManager(list_workspace_files=lambda: list(files), read_workspace_file=lambda path: files[path])
    )

    inputs = turn_module._prepare_inputs(runtime, "session-a", "task", record_replay=True)

    assert inputs["replay_files"] == {} and inputs["replay_selected"] is False
    assert inputs["replay_skipped_bytes"] == 10


def test_prepare_inputs_decides_replay_size_from_remote_sizes_before_downloading(monkeypatch) -> None:
    monkeypatch.setattr(turn_module, "MAX_REPLAY_FILE_BYTES", 8)
    digests = {"files/big.bin": hashlib.sha256(b"x").hexdigest()}

    def no_download(_path):
        raise AssertionError("an oversized workspace must not be downloaded")

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {"digests": digests, "sizes": {"files/big.bin": 30_000_000}, "environment": ""},
            read_workspace_file=no_download,
        )
    )

    inputs = turn_module._prepare_inputs(runtime, "session-a", "task", record_replay=True)

    assert inputs["replay_files"] == {} and inputs["replay_selected"] is False
    assert inputs["replay_skipped_bytes"] == 30_000_000


def test_prepare_inputs_replay_rejects_a_file_that_changed_after_hashing() -> None:
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {
                "digests": {"files/a.txt": hashlib.sha256(b"before").hexdigest()},
                "environment": "",
            },
            read_workspace_file=lambda _path: b"after",
        )
    )

    with pytest.raises(ValueError, match="changed"):
        turn_module._prepare_inputs(runtime, "session-a", "task", record_replay=True)


def test_format_reviewed_memories_renders_bounded_markdown_bullets(monkeypatch):
    monkeypatch.setattr(turn_module, "_MAX_MEMORIES_CHARS", 115)
    items = [
        {"kind": "fact", "content": "Workspace dataset uses UTF-8 CSV."},
        {"kind": "preference", "content": "Report percentages to 2 decimal places."},
        {"kind": "note", "content": "x" * 200},
    ]
    rendered = turn_module._format_reviewed_memories(items)
    lines = rendered.splitlines()
    assert lines[0] == "- [fact] Workspace dataset uses UTF-8 CSV."
    assert lines[1] == "- [preference] Report percentages to 2 decimal places."
    assert len(rendered) <= 115
    assert "x" * 50 not in rendered


def test_should_trace_preparation_and_persistence_under_the_turn(traced):
    runtime = runtime_with(engine=lambda **_kwargs: SimpleNamespace(answer="hello", route="direct"))
    with opened_client(runtime) as (client, _app):
        response = client.post("/sessions/session-a/turn", json={"task": "hello"})
    assert response.status_code == 200
    parents = {row["name"]: row["parent"] for row in traced["spans"]}
    assert parents["turn.prepare_inputs"] == "fleet_turn"
    for name in (
        "turn.load_context",
        "turn.sync_skills",
        "turn.workspace_inputs",
        "turn.reviewed_memories",
        "turn.skill_catalog",
    ):
        assert parents[name] == "turn.prepare_inputs"
    assert parents["turn.commit"] == parents["turn.write_learned_note"] == "fleet_turn"
    prepared = next(row["outputs"] for row in traced["spans"] if row["name"] == "turn.prepare_inputs")
    assert prepared["task_chars"] == len("hello")
    assert prepared["workspace_manifest_chars"] == len("/workspace/files (empty)")
    assert prepared["dataset_preview_chars"] == 0
    assert all(isinstance(value, int) and value >= 0 for value in prepared.values())


def _within(seconds: float, call):
    """Run `call` on a thread and fail instead of hanging when it does not return in time."""
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["value"] = call()
        except BaseException as exc:  # pragma: no cover - surfaced below
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"did not finish within {seconds}s"
    if "error" in result:
        raise result["error"]
    return result["value"]


@pytest.mark.parametrize("failure", ["worker_raises", "worker_returns_silently"])
def test_a_turn_stream_ends_with_an_error_when_the_worker_never_reports_done(monkeypatch, failure):
    def broken_turn(*_args, **_kwargs):
        if failure == "worker_raises":
            raise RuntimeError("cleanup failed before the end marker")

    monkeypatch.setattr(turns_route, "run_turn", broken_turn)
    runtime = runtime_with()
    client, _ = client_for(runtime)

    def post():
        with client:
            return client.post("/sessions/session-a/turn", json={"task": "anything"})

    response = _within(10, post)
    events = events_of(response)
    assert response.status_code == 200
    assert [event["event"] for event in events] == ["turn_status", "error"]
    assert "The Turn failed" in events[1]["payload"]["error"]
    assert "cleanup failed" not in response.text
    assert not runtime.turn_lock.locked()


@pytest.mark.usefixtures("traced")
def test_a_failing_sandbox_cleanup_still_ends_the_stream_after_the_outcome():
    threads: list[str] = []

    def engine(**_kwargs):
        threads.append(threading.current_thread().name)
        return SimpleNamespace(answer="hello", route="direct")

    def broken_end_turn():
        raise RuntimeError("sandbox went away")

    runtime = runtime_with(sandbox_mgr=FakeManager(end_turn=broken_end_turn), engine=engine)
    client, _ = client_for(runtime)

    def post():
        with client:
            return client.post("/sessions/session-a/turn", json={"task": "hello"})

    response = _within(10, post)
    assert [event["event"] for event in events_of(response)] == ["turn_status", "turn_complete", "done"]
    assert not runtime.turn_lock.locked()
    # Turns run on their own pool, never on the loop's default executor that cancellation uses.
    assert threads and threads[0].startswith("fleet-turn")


@pytest.mark.usefixtures("traced")
def test_the_stream_delivers_the_enveloped_done_after_the_outcome() -> None:
    runtime = runtime_with(engine=lambda **_kwargs: SimpleNamespace(answer="hello", route="direct"))
    with opened_client(runtime) as (client, _):
        response = client.post("/sessions/session-a/turn", json={"task": "hello"})

    events = events_of(response)
    assert [event["event"] for event in events] == ["turn_status", "turn_complete", "done"]
    done = events[-1]
    assert done["turn_id"] and done["payload"] == {}
    assert done["seq"] == events[-2]["seq"] + 1, "the terminal envelope keeps the stream's sequence"
    assert response.text.rstrip().endswith(f"id: {done['seq']}"), "the SSE id carries the same sequence"


@pytest.mark.usefixtures("traced")
def test_a_second_turn_on_a_busy_session_gets_a_prompt_409(monkeypatch):
    monkeypatch.setattr(api_deps, "_LOCK_WAIT_SECONDS", 0.05)

    def never(**_kwargs):
        raise AssertionError("a Turn on a busy Session must not run")

    runtime = runtime_with(engine=never)
    runtime.turn_lock.acquire()
    client, app = client_for(runtime)
    try:
        with client:
            started = time.monotonic()
            response = client.post("/sessions/session-a/turn", json={"task": "second"})
            elapsed = time.monotonic() - started
    finally:
        runtime.turn_lock.release()
    assert response.status_code == 409
    assert "Turn" in response.json()["detail"]
    assert elapsed < 2
    assert "409" in app.openapi()["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]


def test_turn_without_lifespan_executor_returns_503_without_creating_a_global_pool():
    runtime = runtime_with()
    client, app = client_for(runtime)
    # Deliberately exercise a TestClient request made without entering the lifespan context.
    app.state.turn_executor = None

    response = client.post("/sessions/session-a/turn", json={"task": "hello"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Fleet Turn executor is unavailable"}
    assert not runtime.turn_lock.locked()
    assert not hasattr(turns_route, "_fallback_executor")


@pytest.mark.usefixtures("traced")
def test_a_client_disconnect_mid_turn_cancels_it_and_releases_the_lock():
    started = threading.Event()
    stopped = threading.Event()
    calls: list[str] = []

    def cancel_turn() -> None:
        calls.append("cancel_turn")
        manager._cancelled.set()

    def engine(**_kwargs):
        started.set()
        try:
            if not manager._cancelled.wait(10):
                raise AssertionError("the disconnect never cancelled the Turn")
            raise RuntimeError("cancelled by the sandbox")
        finally:
            stopped.set()

    manager = FakeManager(cancel_turn=cancel_turn)
    runtime = runtime_with(sandbox_mgr=manager, engine=engine)
    _, app = client_for(runtime)
    body = json.dumps({"task": "long running"}).encode()
    sent: list[dict[str, Any]] = []

    async def drive() -> None:
        disconnected = asyncio.Event()
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        async def disconnect_once_running() -> None:
            await asyncio.to_thread(started.wait, 10)
            disconnected.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/sessions/session-a/turn",
            "raw_path": b"/sessions/session-a/turn",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
            "client": ("testclient", 50000),
            "server": ("test", 80),
            "state": {},
        }
        async with app.router.lifespan_context(app):
            trigger = asyncio.create_task(disconnect_once_running())
            await asyncio.wait_for(app(scope, receive, send), timeout=15)
            await trigger

    asyncio.run(drive())
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 200
    assert manager._cancelled.is_set()
    # The disconnect claimed the teardown; the terminal branch joined it instead of cancelling again.
    assert calls == ["cancel_turn"], "a disconnect racing the terminal branch issues one cancellation"
    # The response ended only after the cancelled Turn drained, and then released the Session.
    assert stopped.is_set()
    assert not runtime.turn_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("committed", [False, True])
async def test_blocked_send_disconnect_drains_before_unlock_even_when_generator_is_retained(
    monkeypatch, spec_version, committed
):
    import sys

    import anyio
    from starlette.requests import ClientDisconnect

    release, stopped, cleanup_started = threading.Event(), threading.Event(), threading.Event()
    calls = []
    warm_root = object()

    def cancel():
        calls.append("cancel")
        manager._cancelled.set()
        manager._sandbox = None
        cleanup_started.set()

    def work(*_args, emit, completion_committed, **_kwargs):
        try:
            manager.reset_turn_budget()
            if committed:
                completion_committed.set()
            for value in range(12):
                emit({"event": "budget_update", "lm_calls": value})
            assert release.wait(10), "the test must release the draining worker"
        finally:
            stopped.set()

    manager = FakeManager(_sandbox=warm_root, cancel_turn=cancel)
    runtime = runtime_with(sandbox_mgr=manager)
    _, app = client_for(runtime)
    monkeypatch.setattr(turns_route, "run_turn", work)
    close = SSERuntimeCallback.close

    def close_and_signal(callback):
        close(callback)
        if committed:
            cleanup_started.set()

    monkeypatch.setattr(SSERuntimeCallback, "close", close_and_signal)
    forwarded, disconnected = asyncio.Event(), asyncio.Event()
    frame = turns_route.frame_envelope
    frames = 0

    def count_frame(envelope):
        nonlocal frames
        frames += 1
        # One frame is blocked in HTTP send, with the remaining native forwarding buffers full.
        if frames == 5:
            forwarded.set()
        return frame(envelope)

    monkeypatch.setattr(turns_route, "frame_envelope", count_frame)
    generators = []
    hooks = sys.get_asyncgen_hooks()

    def retain_stream(generator):
        if hooks.firstiter:
            hooks.firstiter(generator)
        if generator.ag_code.co_name == "stream_turn":
            generators.append(generator)

    sys.set_asyncgen_hooks(firstiter=retain_stream, finalizer=hooks.finalizer)
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b'{"task":"offline"}', "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            if spec_version == "2.4":
                await disconnected.wait()
                raise OSError("offline connection closed")
            await asyncio.Event().wait()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/sessions/session-a/turn",
        "raw_path": b"/sessions/session-a/turn",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("testclient", 50000),
        "server": ("test", 80),
        "state": {},
    }

    async def serve():
        try:
            await app(scope, receive, send)
        except* (anyio.BrokenResourceError, ClientDisconnect):
            # Native SSE forwarding can report a closed receive stream during disconnect.
            pass

    try:
        async with app.router.lifespan_context(app):
            request = asyncio.create_task(serve())
            try:
                await asyncio.wait_for(forwarded.wait(), 5)
                assert len(generators) == 1
                # Framing precedes the route's disconnect poll. Wait until that poll has finished
                # and the generator is actually suspended at yield, not still inside __anext__.
                async with asyncio.timeout(5):
                    while generators[0].ag_await is not None:
                        await asyncio.sleep(0)
                disconnected.set()
                assert await asyncio.to_thread(cleanup_started.wait, 2), "request teardown must not depend on GC"
                assert not request.done() and not stopped.is_set()
                acquired = runtime.turn_lock.acquire(blocking=False)
                if acquired:
                    runtime.turn_lock.release()
                assert not acquired, "the old worker still owns this Session"
            finally:
                release.set()
                await asyncio.wait_for(request, 5)
                for generator in generators:
                    await generator.aclose()
            assert stopped.is_set() and not runtime.turn_lock.locked()
            assert calls == ([] if committed else ["cancel"])
            assert manager._sandbox is (warm_root if committed else None)
    finally:
        sys.set_asyncgen_hooks(*hooks)


_REQUEST_MARKER: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_marker", default=None)


class _MarksRequestContext:
    """ASGI middleware that sets a contextvar for the request, the way tracing or DSPy context would."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        token = _REQUEST_MARKER.set("request-context")
        try:
            await self.app(scope, receive, send)
        finally:
            _REQUEST_MARKER.reset(token)


def test_a_turn_on_the_pool_sees_the_request_context_and_streams_usage_and_tool_events(monkeypatch, traced):
    from dspy.utils.usage_tracker import UsageTracker

    seen: dict[str, Any] = {}
    real_run_turn = turns_route.run_turn

    def recording_run_turn(*args, **kwargs):
        seen["run_turn"] = _REQUEST_MARKER.get()
        seen["thread"] = threading.current_thread().name
        return real_run_turn(*args, **kwargs)

    def engine(**_kwargs):
        seen["engine"] = _REQUEST_MARKER.get()
        callback = traced["callbacks"][-1]
        assert isinstance(callback, SSERuntimeCallback)
        with dspy.context(usage_tracker=UsageTracker()):
            callback.on_lm_start("lm-1", SimpleNamespace(model="openai/m"), {})
            dspy.settings.usage_tracker.add_usage("openai/m", {"prompt_tokens": 7, "completion_tokens": 3})
            callback.on_lm_end("lm-1", ["answer"], None)
        callback.on_tool_start("tool-1", SimpleNamespace(name="lookup"), {"query": "invoice 17"})
        callback.on_tool_end("tool-1", {"result": "found invoice"}, None)
        return SimpleNamespace(answer="done", route="rlm")

    monkeypatch.setattr(turns_route, "run_turn", recording_run_turn)
    runtime = runtime_with(sandbox_mgr=FakeManager(agent_id="root-agent", parent=None), engine=engine)
    client, app = client_for(runtime)
    app.add_middleware(_MarksRequestContext)

    def post():
        with client:
            return client.post("/sessions/session-a/turn", json={"task": "find the invoice"})

    response = _within(10, post)
    events = events_of(response)
    assert seen["thread"].startswith("fleet-turn")
    assert seen["run_turn"] == seen["engine"] == "request-context"
    assert [event["event"] for event in events] == [
        "turn_status",
        "lm_start",
        "lm_end",
        "tool_start",
        "tool_end",
        "turn_complete",
        "done",
    ]
    lm_end = events[2]
    assert lm_end["payload"]["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert events[4]["payload"]["tool"] == "lookup" and events[4]["payload"]["ok"] is True
    assert events[4]["payload"]["agent_id"] == "root-agent"
    assert "found invoice" in json.dumps(events[4]["payload"]["output"])
    assert not runtime.turn_lock.locked()


def _strict_json(text: str) -> Any:
    """Parse JSON the way browsers do: NaN and Infinity are not JSON."""

    def reject(constant: str) -> Any:
        raise ValueError(f"non-standard JSON constant {constant}")

    return json.loads(text, parse_constant=reject)


def test_non_finite_floats_in_activity_stream_as_valid_json_and_the_turn_still_completes(traced):
    def engine(**_kwargs):
        callback = traced["callbacks"][-1]
        assert isinstance(callback, SSERuntimeCallback)
        callback.emit(
            {
                "event": "tool_end",
                "call_id": "tool-1",
                "score": float("nan"),
                "bounds": [float("inf"), float("-inf")],
            }
        )
        return SimpleNamespace(answer="done", route="rlm")

    runtime = runtime_with(engine=engine)
    client, _ = client_for(runtime)

    def post():
        with client:
            return client.post("/sessions/session-a/turn", json={"task": "score it"})

    response = _within(10, post)
    data = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    events = [_strict_json(line) for line in data]
    assert [event["event"] for event in events] == ["turn_status", "tool_end", "turn_complete", "done"]
    # FastAPI >= 0.143.3 serializes non-finite floats as null instead of the invalid NaN/Infinity tokens.
    assert events[1]["payload"]["score"] is None
    assert events[1]["payload"]["bounds"] == [None, None]
    assert not runtime.turn_lock.locked()


def test_a_bare_null_turn_body_is_a_422_and_never_runs_a_turn(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a rejected request must not start a Turn")

    monkeypatch.setattr(turns_route, "run_turn", forbidden)
    runtime = runtime_with()
    client, _ = client_for(runtime)

    response = client.post("/sessions/session-a/turn", content=b"null", headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]
    assert not runtime.turn_lock.locked()


@pytest.mark.usefixtures("traced")
def test_an_explicit_null_is_accepted_on_optional_turn_fields_and_rejected_on_record_replay():
    runtime = runtime_with(engine=lambda **_kwargs: SimpleNamespace(answer="done", route="direct"))
    client, _ = client_for(runtime)

    def post():
        with client:
            accepted = client.post("/sessions/session-a/turn", json={"task": "hi", "idempotency_key": None})
            rejected = client.post("/sessions/session-a/turn", json={"task": "hi", "record_replay": None})
            return accepted, rejected

    accepted, rejected = _within(10, post)
    assert accepted.status_code == 200
    assert [event["event"] for event in events_of(accepted)] == ["turn_status", "turn_complete", "done"]
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["loc"] == ["body", "record_replay"]
    assert rejected.json()["detail"][0]["type"] == "bool_type"
    assert not runtime.turn_lock.locked()


@pytest.mark.usefixtures("traced")
@pytest.mark.parametrize(
    ("value", "include"),
    [("omit", False), (None, True), (0, True), (10, True), (600, True), (3600, True)],
)
def test_turn_request_timeout_accepts_omission_null_zero_and_bounded_values(value, include):
    runtime = runtime_with(engine=lambda **_kwargs: SimpleNamespace(answer="done", route="direct"))
    client, _ = client_for(runtime)
    body = {"task": "hi"}
    if include:
        body["timeout_seconds"] = value

    def post():
        with client:
            return client.post("/sessions/session-a/turn", json=body)

    response = _within(10, post)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("value", "include", "schema_valid", "python_valid"),
    [
        (None, False, True, True),
        (None, True, True, True),
        (0, True, True, True),
        (10, True, True, True),
        (10.0, True, True, False),
        (3600, True, True, True),
        (True, True, False, False),
        (-1, True, False, False),
        (1, True, False, False),
        (9, True, False, False),
        (3601, True, False, False),
        (0.5, True, False, False),
        ("10", True, False, False),
    ],
)
def test_timeout_json_schema_and_python_strictness(value, include, schema_valid, python_valid):
    """JSON Schema allows an integer-valued JSON number while Python strict ints reject float objects."""
    schema = TurnRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    body = {"task": "hi"}
    if include:
        body["timeout_seconds"] = value

    assert validator.is_valid(body) is schema_valid
    if python_valid:
        TurnRequest.model_validate(body)
    else:
        with pytest.raises(ValueError):
            TurnRequest.model_validate(body)


@pytest.mark.parametrize("value", [True, -1, 0.5, 1, 9, 10.0, 3601, "600"])
def test_turn_request_timeout_rejects_values_outside_the_strict_contract(monkeypatch, value):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a rejected request must not start a Turn")

    monkeypatch.setattr(turns_route, "run_turn", forbidden)
    runtime = runtime_with()
    client, _ = client_for(runtime)

    response = client.post("/sessions/session-a/turn", json={"task": "hi", "timeout_seconds": value})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(item["loc"][:2] == ["body", "timeout_seconds"] for item in detail)
    assert not runtime.turn_lock.locked()


@pytest.mark.parametrize(
    ("configured", "configured_source", "body_timeout", "expected"),
    [
        (900, "default", "omit", ("default", 0)),
        (1200, "toml", "omit", ("configuration", 1200)),
        (1200, "toml", 300, ("request", 300)),
        (1200, "toml", 0, ("request", None)),
        (900, "default", None, ("default", 0)),
    ],
)
def test_deadline_provenance_matches_across_span_turn_record_and_replay(
    traced, monkeypatch, configured, configured_source, body_timeout, expected
):
    settings = api_settings(turn_timeout_seconds=configured)
    settings._configuration_sources["turn_timeout_seconds"] = configured_source
    resolved: list[tuple] = []
    original = turn_module.resolve_turn_deadline

    def counting(*args, **kwargs):
        policy = original(*args, **kwargs)
        resolved.append((policy.source, policy.effective_seconds))
        return policy

    monkeypatch.setattr(turn_module, "resolve_turn_deadline", counting)
    persisted: list[dict[str, Any]] = []
    runtime = runtime_with(
        commit_turn=lambda **kwargs: (
            persisted.append(kwargs) or completion_for(kwargs["turn_record"], replay_snapshot=kwargs["replay_snapshot"])
        ),
        engine=lambda **_kwargs: SimpleNamespace(answer="done", route="direct"),
    )
    client, _ = client_for(runtime, settings=settings)
    body: dict[str, Any] = {"task": "compute", "record_replay": True}
    if body_timeout != "omit":
        body["timeout_seconds"] = body_timeout

    def post():
        with client:
            return client.post("/sessions/session-a/turn", json=body)

    response = _within(10, post)
    assert response.status_code == 200, response.text
    assert resolved == [expected], "the policy is resolved exactly once per Turn"
    turn_record = persisted[0]["turn_record"]
    digest, compressed = persisted[0]["replay_snapshot"]
    snapshot = decode_snapshot(digest, compressed)
    terminal = next(event for event in events_of(response) if event["event"] == "turn_complete")
    assert (turn_record["deadline_source"], turn_record["effective_seconds"]) == expected
    assert (traced["attributes"]["deadline_source"], traced["attributes"]["effective_seconds"]) == expected
    assert (snapshot["deadline_source"], snapshot["effective_seconds"]) == expected
    assert (terminal["payload"]["deadline_source"], terminal["payload"]["effective_seconds"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_at", ["outcome", "poll", "close"])
async def test_disconnect_after_observed_completion_preserves_warm_root(monkeypatch, disconnect_at):
    release_worker = threading.Event()
    worker_stopped = threading.Event()
    calls = []
    warm_root = object()

    def cancel_turn():
        calls.append("cancel_turn")
        manager._cancelled.set()
        manager._sandbox = None

    def complete(*_args, emit, **_kwargs):
        try:
            emit({"event": "turn_complete", "answer": "hello", "cleanup": "not_required"})
            if not release_worker.wait(5):
                raise RuntimeError("test did not release the completed worker")
            emit({"event": "done"})
        finally:
            worker_stopped.set()

    disconnected = disconnect_at == "outcome"

    async def is_disconnected():
        if disconnected:
            release_worker.set()
        return disconnected

    manager = FakeManager(_sandbox=warm_root, cancel_turn=cancel_turn)
    runtime = runtime_with(sandbox_mgr=manager)
    monkeypatch.setattr(turns_route, "run_turn", complete)
    monkeypatch.setattr(turns_route, "_DISCONNECT_POLL_SECONDS", 0.01)
    with turns_route.create_turn_executor() as executor:
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(turn_executor=executor, mlflow_experiment_id=None)),
            is_disconnected=is_disconnected,
        )
        stream = turns_route.stream_turn(
            "session-a",
            turns_route.TurnRequest(task="hello"),
            request,
            runtime,
            api_settings(),
            executor,
            AsyncExitStack(),
        )
        try:
            status = await anext(stream)
            assert status.data["event"] == "turn_status"
            if disconnect_at == "outcome":
                remaining = [event async for event in stream]
                assert remaining == []
            else:
                completion = await anext(stream)
                assert completion.data["event"] == "turn_complete"
                assert completion.data["payload"]["cleanup"] == "not_required"
                if disconnect_at == "poll":
                    disconnected = True
                    remaining = [event async for event in stream]
                    assert remaining == []
                else:
                    asyncio.get_running_loop().call_later(0.05, release_worker.set)
                    await stream.aclose()
        finally:
            release_worker.set()
            await stream.aclose()
    assert worker_stopped.is_set()
    assert calls == []
    assert not manager._cancelled.is_set()
    assert manager._sandbox is warm_root


def test_expanded_replay_size_limit_has_a_distinct_skip_reason(monkeypatch):
    from fleet_rlm import replay

    monkeypatch.setattr(replay, "MAX_REPLAY_JSON_BYTES", 5)
    with pytest.raises(replay.ReplaySizeError) as raised:
        replay.encode_snapshot({"task": "large"})
    assert raised.value.size > raised.value.limit == 5


def test_prepare_inputs_reads_replay_files_in_one_batch():
    files = {"files/a.txt": b"a", "files/b.txt": b"b"}
    calls = []

    def batch(paths):
        calls.append(paths)
        return {path: files[path] for path in paths}

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {
                "digests": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()},
                "sizes": {p: len(b) for p, b in files.items()},
                "environment": "",
            },
            read_workspace_files=batch,
        )
    )
    inputs = turn_module._prepare_inputs(runtime, "session-a", "task")
    assert calls == [list(files)]
    assert inputs["replay_files"] == {p: base64.b64encode(b).decode("ascii") for p, b in files.items()}


@pytest.mark.parametrize("downloaded", [{}, {"files/a.txt": b"changed"}])
def test_prepare_inputs_refuses_invalid_batch_replay(downloaded):
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {
                "digests": {"files/a.txt": hashlib.sha256(b"original").hexdigest()},
                "sizes": {"files/a.txt": 8},
                "environment": "",
            },
            read_workspace_files=lambda _paths: downloaded,
        )
    )
    with pytest.raises((KeyError, ValueError)):
        turn_module._prepare_inputs(runtime, "session-a", "task")


# Execution events and usage


def test_dspy_activity_stream_correlates_calls_without_predictor_contents() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)

        def run() -> None:
            def lookup(query: str, api_key: str) -> dict[str, str]:  # noqa: ARG001
                return {"result": "found invoice", "access_token": "result-secret", "reasoning": "private-result"}

            with dspy.context(callbacks=[callback], lm=dspy.utils.DummyLM([{"answer": "approved"}])):
                dspy.Tool(lookup)(query="invoice 17", api_key="input-secret")
                dspy.Predict("question -> answer")(question="private-prompt")

        await asyncio.to_thread(run)
        # A completed executor future need not yield; drain its scheduled activity before close discards it.
        await asyncio.sleep(0)
        callback.close()
        return [queue.get_nowait() for _ in range(queue.qsize())]

    events = asyncio.run(scenario())
    starts = {event["call_id"]: event for event in events if event["event"].endswith("_start")}
    ends = [event for event in events if event["event"].endswith("_end")]
    assert {event["event"] for event in events} >= {"tool_start", "tool_end", "module_start", "module_end"}
    for end in ends:
        assert end["call_id"] in starts
        assert end["duration_ms"] >= 0
        assert end["ok"] is True
    serialized = json.dumps(events)
    assert "invoice 17" in serialized and "found invoice" in serialized
    assert all(
        secret not in serialized for secret in ("input-secret", "result-secret", "private-result", "private-prompt")
    )
    assert "[redacted]" in serialized


def _usage_callback() -> tuple[Any, list[dict]]:
    import asyncio

    import fleet_rlm.api.events as server

    events: list[dict] = []
    callback = server.SSERuntimeCallback(None, asyncio.Queue())  # type: ignore[arg-type]
    callback._emit = events.append  # type: ignore[method-assign]
    return callback, events


def test_lm_end_event_reports_usage_from_dspys_tracker() -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    tracker = UsageTracker()
    lm = SimpleNamespace(model="openai/m")
    with dspy.context(usage_tracker=tracker):
        callback.on_lm_start("c1", lm, {})
        dspy.settings.usage_tracker.add_usage(
            "openai/m",
            {
                "prompt_tokens": 40,
                "completion_tokens": 900,
                "total_tokens": 940,
                "completion_tokens_details": {"reasoning_tokens": 850},
            },
        )
        callback.on_lm_end("c1", ["answer"], None)
    assert events[-1]["usage"] == {
        "prompt_tokens": 40,
        "completion_tokens": 900,
        "total_tokens": 940,
        "reasoning_tokens": 850,
    }


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_lm_end_event_derives_total_and_rolls_up_once(fails) -> None:
    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        callback.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12, "completion_tokens": 5})
        callback.on_lm_end("call", None, RuntimeError("failed") if fails else None)
        assert dspy.settings.usage_tracker is parent
    assert events[-1]["event"] == "lm_end"
    assert events[-1]["ok"] is not fails
    assert events[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    assert parent.get_total_tokens()["model"]["total_tokens"] == 17
    assert len(parent.usage_data["model"]) == 1
    assert callback._usage_scopes == {}


def test_lm_end_event_streams_provider_reasoning_redacted() -> None:
    callback, events = _usage_callback()

    callback.on_lm_end("c2", [{"text": "42", "reasoning_content": "Plan: use api_key=hidden"}], None)
    assert events[-1]["reasoning"] == "Plan: use api_key=[redacted]"

    callback.on_lm_end("c3", ["42"], None)
    assert "reasoning" not in events[-1]


def test_lm_end_event_isolates_usage_between_concurrent_tasks() -> None:
    import asyncio

    from dspy.utils.usage_tracker import UsageTracker

    callback, events = _usage_callback()
    parent = UsageTracker()

    async def call(call_id, count):
        callback.on_lm_start(call_id, SimpleNamespace(model="openai/m"), {})
        await asyncio.sleep(0)
        dspy.settings.usage_tracker.add_usage(
            "openai/m", {"prompt_tokens": count, "completion_tokens": count, "total_tokens": 2 * count}
        )
        callback.on_lm_end(call_id, ["answer"], None)

    async def run():
        with dspy.context(usage_tracker=parent):
            await asyncio.gather(call("c1", 1), call("c2", 5))
            assert dspy.settings.usage_tracker is parent

    asyncio.run(run())
    usages = {event["call_id"]: event["usage"]["total_tokens"] for event in events if event["event"] == "lm_end"}
    assert usages == {"c1": 2, "c2": 10}
    assert parent.get_total_tokens()["openai/m"]["total_tokens"] == 12
    assert callback._usage_scopes == {}


def test_lm_end_event_omits_usage_when_provider_reports_none() -> None:
    callback, events = _usage_callback()
    callback.on_lm_start("c1", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c1", ["x"], None)
    callback.on_lm_start("c2", SimpleNamespace(model="openai/m"), {})
    callback.on_lm_end("c2", None, RuntimeError("boom"))
    assert all("usage" not in event for event in events if event["event"] == "lm_end")


class _FakeClock:
    """A monotonic clock the test advances explicitly; no real sleeping is involved."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_checkpoint_flood_is_coalesced_and_never_delays_cancellation_or_done() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        clock = _FakeClock()
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue, clock=clock)
        received: list[dict[str, Any]] = []

        async def consume() -> None:
            while True:
                callback.pump_progress()
                event = await asyncio.wait_for(queue.get(), timeout=10)
                received.append(event)
                if event.get("event") == "done":
                    return
                await asyncio.sleep(0.001)

        consumer = asyncio.create_task(consume())

        def produce() -> None:
            for value in range(3_000):
                clock.now += 0.001
                callback.emit(
                    {
                        "event": "progress",
                        "phase": "checkpoint",
                        "scope": "scan",
                        "expect_seconds": 5,
                        "checkpoint": value,
                    }
                )
            clock.now += 1.0
            callback.emit({"event": "cancellation_requested"})
            callback.emit({"event": "turn_cancelled", "error": "cancelled"})
            callback.emit({"event": "done"})

        await asyncio.to_thread(produce)
        await asyncio.wait_for(consumer, timeout=20)
        return received

    events = asyncio.run(scenario())
    names = [event.get("event") for event in events]
    progress = [event for event in events if event.get("event") == "progress"]
    assert len(progress) <= 5, f"more than one progress event per second: {len(progress)}"
    # Cancellation no longer waits for activity delivery; an outcome discards unsent progress.
    assert all(type(event["checkpoint"]) is int for event in progress)
    for lifecycle in ("cancellation_requested", "turn_cancelled", "done"):
        assert lifecycle in names
    assert names.index("done") == len(names) - 1
    if progress:
        assert names.index("cancellation_requested") > names.index("progress")
    serialized = json.dumps(events)
    assert not any(token in serialized for token in ("%", "percent", "complete", "remaining", "ETA"))


def test_terminal_outcome_discards_withheld_and_late_progress() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> list[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        clock = _FakeClock()
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue, clock=clock)
        progress = {"event": "progress", "phase": "start", "scope": "scan", "expect_seconds": 5}
        await asyncio.to_thread(callback.emit, progress)
        await asyncio.sleep(0)
        assert (await queue.get())["event"] == "progress"
        clock.now = 0.5
        await asyncio.to_thread(callback.emit, {**progress, "phase": "checkpoint", "checkpoint": 1})
        clock.now = 0.75
        await asyncio.to_thread(callback.emit, {**progress, "phase": "checkpoint", "checkpoint": 2})
        assert queue.empty()
        clock.now = 1.0
        callback.pump_progress()
        assert queue.get_nowait()["checkpoint"] == 2, "coalescing keeps the newest checkpoint while active"
        clock.now = 1.5
        await asyncio.to_thread(callback.emit, {**progress, "phase": "checkpoint", "checkpoint": 3})
        await asyncio.to_thread(callback.emit, {"event": "turn_complete"})
        clock.now = 2.0
        callback.pump_progress()
        await asyncio.to_thread(callback.emit, {**progress, "phase": "checkpoint", "checkpoint": 4})
        await asyncio.to_thread(callback.emit, {"event": "done"})
        return [queue.get_nowait() for _ in range(queue.qsize())]

    assert [event["event"] for event in asyncio.run(scenario())] == ["turn_complete", "done"]


def test_progress_emission_never_raises_the_slow_consumer_timeout_on_a_full_queue() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback

    async def scenario() -> int:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        for _ in range(100):
            queue.put_nowait({"event": "filler"})
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)

        def flood() -> None:
            for value in range(1_000):
                callback.emit(
                    {
                        "event": "progress",
                        "phase": "checkpoint",
                        "scope": "scan",
                        "expect_seconds": 5,
                        "checkpoint": value,
                    }
                )

        await asyncio.to_thread(flood)
        return queue.qsize()

    assert asyncio.run(scenario()) == 100, "progress is dropped when full, never put to the queue"


def test_full_activity_queue_cannot_interrupt_budget_tools_or_cancellation() -> None:
    import asyncio

    from fleet_rlm.api.events import SSERuntimeCallback
    from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
    from fleet_rlm.sandbox.manager import CLEANUP_STOPPED, DaytonaSandboxManager

    async def scenario() -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        for _ in range(100):
            queue.put_nowait({"event": "filler"})
        callback = SSERuntimeCallback(asyncio.get_running_loop(), queue)
        manager = DaytonaSandboxManager("offline", "offline", "offline", "offline", event_callback=callback.emit)
        manager.reset_turn_budget(deadline_seconds=None)
        stopped: list[str] = []
        manager._sandbox = SimpleNamespace(id="offline-root")
        manager._client = SimpleNamespace(stop=lambda sandbox, **_kwargs: stopped.append(sandbox.id))
        interpreter = DaytonaInterpreter(manager, tools={"subtract": lambda a, b: a - b})

        def work() -> dict[str, Any]:
            manager.consume_budget(3)
            result = interpreter.answer_host_call({"id": "tool-1", "tool": "subtract", "args": [19, 7]})
            manager.request_cancellation()
            return result

        producer = asyncio.create_task(asyncio.to_thread(work))
        try:
            done, _ = await asyncio.wait({producer}, timeout=2)
            assert producer in done, "activity delivery must not wait for the consumer before doing real work"
            assert producer.result() == {"value": 12}
            assert manager.turn_usage()["lm_calls"] == 3
            assert manager.cleanup_outcome == CLEANUP_STOPPED
            assert stopped == ["offline-root"]
            assert queue.qsize() == 100
        finally:
            callback.close()
            await producer
            manager.end_turn()

    asyncio.run(scenario())


# SSE wire contracts


def wire(event: str, **payload: object) -> dict[str, object]:
    """Build the exact nested shape produced by Envelope.wrap."""
    return {
        "version": 1,
        "event": event,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "agent_id": None,
        "parent_agent_id": None,
        "sandbox_id": None,
        "seq": 9,
        "timestamp": 1_798_000_000.25,
        "payload": payload,
    }


@pytest.mark.parametrize("event", ["turn_complete", "error", "turn_cancelled"])
def test_terminal_contract_accepts_integer_deadline_provenance(event: str) -> None:
    framed = frame_envelope(wire(event, effective_seconds=900))
    assert framed.data["payload"]["effective_seconds"] == 900


def test_envelope_forbids_flattened_top_level_and_reserved_payload_keys() -> None:
    flattened = wire("progress", nested={"value": 1})
    flattened["nested"] = {"value": 1}
    with pytest.raises(ValidationError):
        FleetEnvelope.model_validate(flattened)

    for forbidden_key in ("event", "payload"):
        overwriting = wire("progress")
        overwriting["payload"][forbidden_key] = "override"
        with pytest.raises(ValidationError, match="overwrites an envelope field"):
            FleetEnvelope.model_validate(overwriting)


def test_malformed_completion_is_rejected_by_its_terminal_contract() -> None:
    malformed = wire("turn_complete", turn_id="turn-1", answer={"unexpected": "object"})

    with pytest.raises(ValidationError, match="answer"):
        FleetEnvelope.model_validate(malformed)


def test_error_done_and_progress_contracts_are_distinct() -> None:
    malformed_error = wire("error", request_id={"unexpected": "object"})
    with pytest.raises(ValidationError, match="request_id"):
        FleetEnvelope.model_validate(malformed_error)
    assert FleetEnvelope.model_validate(wire("progress", request_id={"extension": True})).event == "progress"

    with pytest.raises(ValidationError):
        FleetEnvelope.model_validate(wire("done", reason="not part of the done marker"))

    dynamic = FleetEnvelope.model_validate(
        wire("progress", phase="checkpoint", scope="scan", nested={"extension": [None, 4]})
    )
    assert dynamic.payload["nested"] == {"extension": [None, 4]}


def test_terminal_history_preserves_null_absence_and_unknown_values() -> None:
    legacy = wire("turn_complete", answer=None, route="rlm", historical_extension={"keep": [1, None]})
    validated = FleetEnvelope.model_validate(legacy)
    dumped = validated.model_dump(mode="json", exclude_unset=True)

    assert dumped["payload"] == legacy["payload"]
    assert dumped["payload"]["answer"] is None
    assert "artifacts" not in dumped["payload"]
    assert dumped["payload"]["historical_extension"] == {"keep": [1, None]}
    assert "answer" not in dumped
    assert "historical_extension" not in dumped


def test_native_sse_consumer_receives_existing_id_and_json_data_framing() -> None:
    app = FastAPI()

    @app.get("/events", response_class=EventSourceResponse)
    async def events():
        yield frame_envelope(wire("progress", phase="checkpoint", checkpoint=3))

    with TestClient(app) as client:
        response = client.get("/events")

    assert response.headers["content-type"].startswith("text/event-stream")
    lines = response.text.splitlines()
    assert "id: 9" in lines
    assert not any(line.startswith("event:") for line in lines)
    data_line = next(line for line in lines if line.startswith("data: "))
    assert lines[-1] == ""
    data = json.loads(data_line[6:])
    assert data["seq"] == 9
    assert data["event"] == "progress"
    assert data["payload"] == {"phase": "checkpoint", "checkpoint": 3}


def test_native_terminal_frame_is_consumed_by_the_tui_parser() -> None:
    app = FastAPI()

    @app.get("/events", response_class=EventSourceResponse)
    async def events():
        yield frame_envelope(
            wire(
                "turn_complete",
                turn_id="turn-1",
                answer="Done",
                route="rlm",
                artifacts=[],
                outcome="completed",
            )
        )

    with TestClient(app) as client:
        native_response = client.get("/events")

    response = httpx.Response(200, content=native_response.content)
    state = SessionState()
    tracker = TurnTracker()
    consumed = list(_turn_events(response, state, tracker))

    assert len(consumed) == 1
    assert consumed[0]["payload"] == {
        "turn_id": "turn-1",
        "answer": "Done",
        "route": "rlm",
        "artifacts": [],
        "outcome": "completed",
    }
    assert state.last_turn_id == "turn-1"
    assert tracker.terminal_event["answer"] == "Done"


def test_framing_validation_error_does_not_expose_rejected_payload() -> None:
    rejected = wire("turn_complete", answer={"private": "not-for-logs"})

    with pytest.raises(RuntimeError, match="Turn event contract validation failed") as error:
        frame_envelope(rejected)

    assert "not-for-logs" not in str(error.value)


# Consolidated from api/test_routes.py


# HTTP routes


def test_openapi_documents_session_controls_and_the_sse_stream():
    _, app = client_for()
    schema = app.openapi()
    assert schema["info"]["version"] == "0.12.0"
    assert {"get", "put"} <= set(schema["paths"]["/sessions/{session_id}/mode"])
    for path in ("/sessions/{session_id}/history", "/sessions/{session_id}/turns/{turn_id}"):
        assert "get" in schema["paths"][path]
    assert {"get", "put", "delete"} <= set(schema["paths"]["/sessions/{session_id}/workspace/files/{rel_path}"])
    # Approved programs are installed by `fleet optimize --approve`; the engine reloads them per Turn.
    assert "/sessions/{session_id}/reload" not in schema["paths"]
    assert "post" in schema["paths"]["/skills/push"]
    turn = schema["paths"]["/sessions/{session_id}/turn"]["post"]["responses"]["200"]["content"]["text/event-stream"]
    assert turn["schema"] == {"type": "string"} and "itemSchema" not in turn
    assert (
        schema["components"]["schemas"]["ModeRequest"]["properties"]["mode"]["pattern"] == "^(direct|flex|rlm|hybrid)$"
    )
    config = schema["paths"]["/sessions/{session_id}/config"]["get"]["responses"]["200"]["content"]
    assert config["application/json"]["schema"] == {"$ref": "#/components/schemas/SessionConfig"}
    workspace_request = schema["components"]["schemas"]["WorkspaceWriteRequest"]
    assert len(workspace_request["oneOf"]) == 2
    timeout = schema["components"]["schemas"]["TurnRequest"]["properties"]["timeout_seconds"]
    assert {branch.get("minimum") for branch in timeout["anyOf"] if branch.get("type") == "integer"} == {0, 10}
    assert any(branch.get("maximum") == 0 for branch in timeout["anyOf"])
    assert any(branch.get("maximum") == 3600 for branch in timeout["anyOf"])


def test_health_reports_the_package_version():
    with opened_client() as (client, _):
        health = client.get("/health").json()
        assert (health["status"], health["version"]) == ("ok", "0.12.0")
        assert health["mlflow"]["mode"] == "local"
        assert health["mlflow"]["ready"] is False


@pytest.mark.parametrize("path", ["files", "workspace/tree"])
def test_workspace_listing_keeps_other_requests_responsive(path):
    entered, release = threading.Event(), threading.Event()

    def list_files():
        entered.set()
        assert release.wait(5)
        return ["artifacts/report.txt"]

    runtime = runtime_with(sandbox_mgr=FakeManager(list_workspace_files=list_files))
    with opened_client(runtime) as (client, _), ThreadPoolExecutor(max_workers=2) as pool:
        listing = pool.submit(client.get, f"/sessions/session-a/{path}")
        try:
            assert entered.wait(5)
            health = pool.submit(client.get, "/health")
            assert health.result(timeout=2).status_code == 200
        finally:
            release.set()
        response = listing.result(timeout=5)
        assert response.status_code == 200
        assert response.json() == {
            "session_id": "session-a",
            "files": ["artifacts/report.txt"],
            "artifacts": ["artifacts/report.txt"],
        }


def test_context_selection_and_workspace_stat_use_remote_metadata_without_downloading():
    import hashlib

    digest = hashlib.sha256(b"selected").hexdigest()
    manager = FakeManager(
        stat_workspace_file=lambda path: {"path": path, "size_bytes": 8, "sha256": digest},
        read_workspace_file=lambda _path: pytest.fail("metadata must not download file contents"),
    )
    with opened_client(runtime_with(sandbox_mgr=manager)) as (client, _):
        selected = client.put("/sessions/s1/context", json={"path": "files/selected.csv", "description": "selected"})
        stat = client.get("/sessions/s1/workspace/stat/files/selected.csv")
        descriptor = client.get("/sessions/s1/config").json()["context"]
    assert selected.status_code == stat.status_code == 200
    assert selected.json() == stat.json() == {"path": "files/selected.csv", "size_bytes": 8, "sha256": digest}
    assert descriptor == {
        "workspace_path": "files/selected.csv",
        "description": "selected",
        "size_bytes": 8,
        "sha256": digest,
    }


def test_an_invalid_session_id_is_a_422_before_any_runtime_is_built():
    with opened_client(runtime_with()) as (client, _):
        assert client.get("/sessions/bad id/mode").status_code == 422


def test_readonly_session_endpoints_read_store_without_materializing_cold_runtimes():
    saved_settings = {
        "mode": "rlm",
        "models": {"root": "openai/system.ai.custom-root", "sub": "openai/system.ai.custom-sub"},
        "context": {"workspace_path": "files/cold.csv", "description": "cold context"},
    }
    saved_memories = [{"memory_id": "m1", "kind": "note", "content": "persisted memory", "source": "api"}]
    store = store_with(
        runtime_settings=lambda session_id: dict(saved_settings) if session_id == "cold-s1" else {},
        memories=lambda session_id, _search="", **_kw: list(saved_memories) if session_id == "cold-s1" else [],
    )
    app = create_app(
        settings=api_settings(),
        initialize_db=False,
        configure_observability=False,
        store=store,
    )
    cold_registry = SessionRuntimeRegistry(api_settings(), store)  # type: ignore[arg-type]
    cold_registry.get = lambda _session_id: pytest.fail("read-only endpoints must not materialize a cold runtime")  # type: ignore[method-assign]
    app.dependency_overrides[get_registry] = lambda: cold_registry
    with TestClient(app, raise_server_exceptions=False) as client:
        config = client.get("/sessions/cold-s1/config")
        mode = client.get("/sessions/cold-s1/mode")
        memory = client.get("/sessions/cold-s1/memory")
    assert config.status_code == 200
    assert config.json()["mode"] == "rlm"
    assert config.json()["models"] == saved_settings["models"]
    assert config.json()["context"] == {**saved_settings["context"], "size_bytes": 0, "sha256": ""}
    assert mode.status_code == 200 and mode.json()["mode"] == "rlm"
    assert memory.status_code == 200 and memory.json() == {"memories": saved_memories}


@pytest.mark.parametrize("operation", ["mode", "select", "clear"])
@pytest.mark.parametrize("fail_save", [False, True])
def test_session_settings_only_change_after_successful_persistence(operation, fail_save):
    saved: dict = {}

    def save(data):
        if fail_save:
            raise RuntimeError("private database detail")
        saved.update(data)

    runtime = runtime_with(
        store=store_with(save_runtime_settings=lambda _session, data: save(data)),
        sandbox_mgr=FakeManager(read_workspace_file=lambda _path: b"new data"),
        context=WorkspaceDataset("files/old.csv", sha256="a" * 64),
    )
    with opened_client(runtime) as (client, _):
        before = client.get("/sessions/s1/config").json()
        if operation == "mode":
            response = client.put("/sessions/s1/mode", json={"mode": "rlm"})
        elif operation == "select":
            response = client.put("/sessions/s1/context", json={"path": "files/new.csv"})
        else:
            response = client.delete("/sessions/s1/context")
        after = client.get("/sessions/s1/config").json()
    if fail_save:
        assert response.status_code >= 500
        assert after == before and saved == {}
        assert "private database detail" not in response.text
    else:
        assert response.status_code == 200
        assert after["mode"] == saved["mode"] == ("rlm" if operation == "mode" else "hybrid")
        assert after["context"] == saved["context"]
        if operation == "select":
            assert after["context"]["workspace_path"] == "files/new.csv"
        elif operation == "clear":
            assert after["context"] is None
        else:
            assert after["context"] == before["context"]


@pytest.mark.parametrize("failure", [None, "busy", "persistence"])
def test_model_selection_is_validated_atomic_and_secret_free(failure):
    saved: dict = {}
    old_engine = engine_with("openai/system.ai.old", "openai/system.ai.child")

    def save(data):
        if failure == "persistence":
            raise RuntimeError("private database detail")
        saved.update(data)

    runtime = runtime_with(
        engine=old_engine,
        store=store_with(save_runtime_settings=lambda _session, data: save(data)),
        execution_mode="rlm",
        sandbox_mgr=FakeManager(
            sub_lm=old_engine.sub_lm, max_llm_calls=7, max_depth=2, max_children=3, max_active_children=1
        ),
    )
    if failure == "busy":
        runtime.turn_lock.acquire()
    client, _ = client_for(
        runtime,
        settings=api_settings(),
        engine_for_models=lambda _runtime, models: engine_with(models["root"], models["sub"]),
    )
    with client:
        before = client.get("/sessions/s1/config").json()
        assert before["models"]["sub"] == "openai/system.ai.child"
        response = client.put("/sessions/s1/model", json={"role": "root", "model": "openai/system.ai.new"})
        after = client.get("/sessions/s1/config").json()
    expected = {None: 200, "unlisted": 422, "busy": 409, "persistence": 503}[failure]
    assert response.status_code == expected
    assert after["models"] == (saved["models"] if failure is None else before["models"])
    assert after["limits"]["lm_calls"] == 7
    assert "private" not in response.text
    assert set(after) == {"models", "mode", "context", "limits", "configuration"}
    assert "fleet_database_url" not in after["configuration"]["values"]
    assert "databricks_token" not in after["configuration"]["values"]
    if failure is None:
        assert after["configuration"]["sources"]["fleet_root_model"] == "session"
    else:
        assert runtime.engine is old_engine and saved == {}
    if failure == "busy":
        runtime.turn_lock.release()


def test_attach_streams_to_the_provider_path_and_cleans_temp_files():
    uploaded: list[tuple[str, bytes]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            write_workspace_file_from_path=lambda rel_path, path: (
                uploaded.append((rel_path, Path(path).read_bytes())) or f"/workspace/{rel_path}"
            )
        ),
    )
    existing = set(Path("/tmp").glob("fleet-upload-*"))
    with opened_client(runtime, settings=api_settings(max_upload_bytes=4)) as (client, _):
        accepted = client.post("/sessions/session-a/attach", files={"file": ("sample.bin", b"1234")})
        rejected = client.post("/sessions/session-a/attach", files={"file": ("too-large.bin", b"12345")})
    assert accepted.status_code == 200
    assert accepted.json() == {"mounted_path": "/workspace/files/sample.bin", "rel_path": "files/sample.bin"}
    assert uploaded == [("files/sample.bin", b"1234")]
    assert rejected.status_code == 413
    assert set(Path("/tmp").glob("fleet-upload-*")) == existing


def test_workspace_download_streams_from_the_provider_path_and_cleans_its_temp_file():
    downloaded: list[tuple[str, str]] = []

    def download(rel_path, local_path, *, max_bytes):
        assert max_bytes == 25_000_000
        downloaded.append((rel_path, local_path))
        Path(local_path).write_bytes(b"downloaded data")
        return 15

    with opened_client(runtime_with(sandbox_mgr=FakeManager(read_workspace_file_to_path=download))) as (client, _):
        response = client.get("/sessions/session-a/workspace/files/files/result.bin")
    assert response.status_code == 200 and response.content == b"downloaded data"
    assert downloaded[0][0] == "files/result.bin"
    assert not Path(downloaded[0][1]).exists()


def test_a_missing_workspace_file_is_a_404_and_cleans_its_temp_file():
    seen: list[str] = []

    def download(_rel_path, local_path, *, max_bytes):  # noqa: ARG001
        seen.append(local_path)
        raise FileNotFoundError("workspace file is unavailable")

    with opened_client(runtime_with(sandbox_mgr=FakeManager(read_workspace_file_to_path=download))) as (client, _):
        response = client.get("/sessions/session-a/workspace/files/files/missing.bin")
    assert response.status_code == 404 and not Path(seen[0]).exists()


def test_workspace_write_validation_does_not_echo_rejected_file_content():
    with opened_client(runtime_with(sandbox_mgr=FakeManager())) as (client, _):
        response = client.put(
            "/sessions/s1/workspace/files/files/a.txt",
            json={"content": {"private-file-text": "invalid type"}},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"type": "string_type", "loc": ["body", "content"], "msg": "Input should be a valid string"}
    ]
    assert "private-file-text" not in response.text


def test_workspace_write_requires_exactly_one_content_field():
    runtime = runtime_with(
        sandbox_mgr=FakeManager(write_workspace_file=lambda rel_path, _content: f"/workspace/{rel_path}")
    )
    with opened_client(runtime) as (client, _):
        both = client.put(
            "/sessions/s1/workspace/files/files/a.txt",
            json={"content": "secret-file-text", "content_base64": "c2VjcmV0"},
        )
        neither = client.put("/sessions/s1/workspace/files/files/a.txt", json={"content": None})
        empty_text = client.put("/sessions/s1/workspace/files/files/empty.txt", json={"content": ""})
        empty_base64 = client.put("/sessions/s1/workspace/files/files/empty-base64.txt", json={"content_base64": ""})
    assert both.status_code == neither.status_code == 422
    assert both.json() == neither.json() == {"detail": "Supply exactly one of content or content_base64"}
    assert "secret-file-text" not in both.text and "c2VjcmV0" not in both.text
    assert empty_text.status_code == empty_base64.status_code == 200


def test_workspace_write_invalidates_known_empty_before_provider_call_but_not_on_invalid_path():
    saved: dict[str, str] = {}

    def fail_write(_rel_path: str, _content: bytes) -> str:
        raise RuntimeError("Daytona FUSE partial write")

    runtime = runtime_with(
        sandbox_mgr=FakeManager(session_id="s1", write_workspace_file=fail_write),
        store=store_with(save_runtime_settings=lambda _session, data: saved.update(data)),
        workspace_state="known_empty",
    )
    with opened_client(runtime) as (client, _):
        invalid = client.put("/sessions/s1/workspace/files/%2E%2E/escape", json={"content": "x"})
        assert invalid.status_code == 422
        assert runtime.workspace_state == "known_empty" and saved == {}

        failed = client.put("/sessions/s1/workspace/files/files/out.txt", json={"content": "x"})
        assert failed.status_code == 503
        assert runtime.workspace_state == saved["workspace_state"] == "unknown"


@pytest.mark.parametrize(
    ("body", "valid"),
    [
        ({}, False),
        ({"content": None}, False),
        ({"content": ""}, True),
        ({"content_base64": ""}, True),
        ({"content": None, "content_base64": ""}, True),
        ({"content": "", "content_base64": None}, True),
        ({"content": "text", "content_base64": "dGV4dA=="}, False),
    ],
)
def test_workspace_write_schema_xor_matches_runtime(body, valid):
    schema = WorkspaceWriteRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    assert validator.is_valid(body) is valid
    if valid:
        WorkspaceWriteRequest.model_validate(body)
    else:
        with pytest.raises(ValueError):
            WorkspaceWriteRequest.model_validate(body)


@pytest.mark.parametrize(
    "turn_id, result, expected_result",
    [
        (
            "turn-1",
            {"answer": "stored"},
            {"answer": "stored", "provenance_state": "pre-provenance"},
        ),
        (
            "turn-2",
            {
                "event": "turn_complete",
                "answer": "5",
                "route": "flex",
                "outcome": "completed",
                "deadline_source": "request",
                "effective_seconds": None,
                "duration_seconds": 12.5,
                "cleanup": "not_required",
                "snapshot_name": "fleet-rlm-python313-v10",
                "snapshot_id": "snap-1",
                "snapshot_cpu": 4,
                "snapshot_mem": 8,
                "snapshot_disk": 8,
                "snapshot_definition_digest": "d" * 64,
                "snapshot_build_digest": "c" * 64,
            },
            {
                "event": "turn_complete",
                "answer": "5",
                "route": "flex",
                "outcome": "completed",
                "deadline_source": "request",
                "effective_seconds": None,
                "duration_seconds": 12.5,
                "cleanup": "not_required",
                "snapshot_name": "fleet-rlm-python313-v10",
                "snapshot_id": "snap-1",
                "snapshot_cpu": 4,
                "snapshot_mem": 8,
                "snapshot_disk": 8,
                "snapshot_definition_digest": "d" * 64,
                "snapshot_build_digest": "c" * 64,
                "provenance_state": "recorded",
            },
        ),
    ],
    ids=["pre-provenance", "recorded-provenance"],
)
def test_turn_status_and_history_read_durable_store_without_runtime(turn_id, result, expected_result):
    store = store_with(
        history=lambda _session: [{"turn_id": "turn-1", "answer": "stored"}],
        turn_status=lambda _session, requested_id: (
            {
                "turn_id": requested_id,
                "status": "completed",
                "result": dict(result),
                "updated_at": "2026-10-20T00:00:00",
            }
            if requested_id == turn_id
            else None
        ),
    )
    client, app = client_for(store=store)
    app.dependency_overrides[get_registry] = lambda: pytest.fail("durable reads must not build a runtime")
    with client:
        history = client.get("/sessions/session-1/history")
        status = client.get(f"/sessions/session-1/turns/{turn_id}")
        missing = client.get("/sessions/session-1/turns/missing")
    assert history.status_code == 200
    assert history.json() == {
        "session_id": "session-1",
        "turns": [{"turn_id": "turn-1", "answer": "stored"}],
    }
    assert status.status_code == 200
    assert status.json() == {
        "turn_id": turn_id,
        "status": "completed",
        "result": {**expected_result, "turn_id": turn_id},
        "updated_at": "2026-10-20T00:00:00",
    }
    assert missing.status_code == 404


@pytest.mark.parametrize("partial_snapshot", [False, True])
def test_new_failed_turn_without_resolved_snapshot_is_not_historical(partial_snapshot):
    result = {"outcome": "failed", "deadline_source": "default", "effective_seconds": 900, "cleanup": "stopped"}
    if partial_snapshot:
        result["snapshot_name"] = "unresolved-snapshot"
    store = store_with(
        turn_status=lambda _session, turn_id: {
            "turn_id": turn_id,
            "status": "failed",
            "result": result,
            "updated_at": "2026-10-23T00:00:00",
        }
    )
    with opened_client(store=store) as (client, _):
        status = client.get("/sessions/session-1/turns/turn-1")
    assert status.status_code == 200
    assert status.json()["result"] == {**result, "provenance_state": "unresolved"}


@pytest.mark.parametrize("path", ["/sessions/session-1/history", "/sessions/session-1/turns/turn-1"])
def test_durable_reads_need_storage(path):
    with opened_client() as (client, _):
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Session storage is unavailable"}


def test_session_listing_needs_storage():
    with opened_client() as (client, _):
        response = client.get("/sessions")
    assert response.status_code == 503 and response.json() == {"detail": "Session storage is unavailable"}


def test_memory_routes_translate_invalid_input():
    def save(memory_id, *_args, **_kwargs):
        raise ValueError(f"invalid {memory_id}")

    with opened_client(runtime_with(save_memory=save)) as (client, _):
        response = client.put("/sessions/s1/memory/m1", json={"content": "note"})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid memory"}


def test_skills_use_the_default_session_and_report_counts():
    runtime = runtime_with(
        skill_mgr=SimpleNamespace(sync_local_skills_to_volume=lambda: 3, build_catalog_xml=lambda: "<skills />")
    )
    runtime.sandbox_mgr.session_id = "default"
    with opened_client(runtime) as (client, _):
        runtime.workspace_state = "known_empty"
        assert client.post("/skills/push").json() == {"pushed": 3}
        assert runtime.workspace_state == "unknown"
        runtime.workspace_state = "known_empty"
        assert client.get("/skills").json() == {"catalog_xml": "<skills />"}
        assert runtime.workspace_state == "unknown"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/sessions/s1/memory/m1", {"content": "x"}),
        ("put", "/sessions/s1/mode", {"mode": "rlm"}),
        ("put", "/sessions/s1/model", {"role": "root", "model": "openai/a.b.c"}),
        ("put", "/sessions/s1/context", {"path": "files/a.csv"}),
        ("post", "/sessions/s1/turn", {"task": "hi"}),
    ],
)
def test_request_bodies_reject_unknown_fields(method: str, path: str, body: dict) -> None:
    client, _ = client_for(runtime_with())

    response = getattr(client, method)(path, json={**body, "unexpected": 1})

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/sessions/s1/mode", {"mode": "rlm"}),
        ("put", "/sessions/s1/context", {"path": "files/a.csv"}),
        ("delete", "/sessions/s1/context", None),
        ("put", "/sessions/s1/memory/m1", {"content": "x"}),
        ("delete", "/sessions/s1/memory/m1", None),
        ("get", "/sessions/s1/files", None),
        ("get", "/sessions/s1/workspace/stat/files/a.csv", None),
        ("put", "/sessions/s1/workspace/files/files/a.csv", {"content": "x"}),
        ("delete", "/sessions/s1/workspace/files/files/a.csv", None),
        ("get", "/skills?session_id=s1", None),
        ("post", "/skills/push?session_id=s1", None),
    ],
)
def test_session_routes_return_409_instead_of_waiting_on_a_running_turn(monkeypatch, method, path, body):
    from fleet_rlm.api import deps

    monkeypatch.setattr(deps, "_LOCK_WAIT_SECONDS", 0.01)
    runtime = runtime_with()
    runtime.turn_lock.acquire()
    client, _ = client_for(runtime)
    try:
        with client:
            response = client.request(method.upper(), path, json=body)
    finally:
        runtime.turn_lock.release()
    assert response.status_code == 409
    assert "Turn" in response.json()["detail"]


def test_openapi_documents_the_409_on_lock_guarded_routes():
    _, app = client_for()
    schema = app.openapi()
    for path, method in (
        ("/sessions/{session_id}/mode", "put"),
        ("/sessions/{session_id}/workspace/files/{rel_path}", "put"),
        ("/skills/push", "post"),
    ):
        assert "409" in schema["paths"][path][method]["responses"], (path, method)
    assert "409" not in schema["paths"]["/sessions/{session_id}/mode"]["get"]["responses"]


def test_deleting_a_session_releases_its_runtime():
    with opened_client(runtime_with()) as (client, _):
        response = client.delete("/sessions/s1")
    assert response.status_code == 200 and response.json() == {"deleted": "s1"}


def test_deleting_a_session_without_a_runtime_is_a_404_and_a_busy_one_a_409():

    with opened_client(runtime_with()) as (client, _):
        FakeRegistry.evict = lambda _self, _sid: False
        assert client.delete("/sessions/s1").status_code == 404

        def busy(_self, _sid):
            raise TimeoutError("busy")

        FakeRegistry.evict = busy
        assert client.delete("/sessions/s1").status_code == 409


@pytest.mark.parametrize(
    "filename, content_type, expected_status",
    [("", "application/octet-stream", 422), ("empty.txt", "text/plain", 200)],
    ids=["no-selection", "named-zero-byte-file"],
)
def test_attach_distinguishes_no_selection_from_a_named_zero_byte_file(filename, content_type, expected_status):
    uploaded: list[tuple[str, bytes]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            write_workspace_file_from_path=lambda rel_path, path: (
                uploaded.append((rel_path, Path(path).read_bytes())) or f"/workspace/{rel_path}"
            )
        ),
    )
    client, _ = client_for(runtime)
    boundary = "fleet-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    response = client.post(
        "/sessions/session-a/attach",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == expected_status
    if filename:
        assert response.json() == {"mounted_path": "/workspace/files/empty.txt", "rel_path": "files/empty.txt"}
        assert uploaded == [("files/empty.txt", b"")]
    else:
        assert response.json()["detail"][0]["loc"] == ["body", "file"]
        assert response.json()["detail"][0]["type"] == "missing"
        assert uploaded == []


@pytest.mark.parametrize("failure", ["timeout", "malformed"])
def test_models_discovery_errors_are_sanitized_and_manual_selection_still_works(monkeypatch, failure):
    from contextlib import contextmanager

    import httpx

    from fleet_rlm.config import FleetSettings

    @contextmanager
    def stream(*_args, **_kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("private credential body")
        yield httpx.Response(
            200,
            json={"private credential body": "bad"},
            request=httpx.Request("GET", "https://models.example/v1/models"),
        )

    monkeypatch.setattr(httpx, "stream", stream)
    runtime = runtime_with()
    client, _ = client_for(
        runtime,
        settings=FleetSettings(_env_file=None, fleet_llm_api_base="https://models.example/v1"),
        engine_for_models=lambda _runtime, models: engine_with(models["root"], models["sub"]),
    )
    with client:
        discovered = client.get("/models", params={"scope": "ignored", "role": "root"})
        assert discovered.status_code == (503 if failure == "timeout" else 422)
        assert "private credential body" not in discovered.text
        assert client.put("/sessions/s1/model", json={"role": "root", "model": "anthropic/manual"}).status_code == 200


def test_workspace_reads_context_selection_and_skill_pull_invalidate_known_empty():
    saved: list[dict] = []
    files: list[str] = []
    exported: list[str] = []

    def download(_rel_path: str, local_path: str, *, max_bytes: int) -> int:  # noqa: ARG001
        if not files:
            raise FileNotFoundError("missing")
        Path(local_path).write_bytes(b"data")
        return 4

    def stat(path: str) -> dict:
        if not files:
            raise FileNotFoundError("missing")
        return {"path": path, "size_bytes": 4, "sha256": "a" * 64}

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            session_id="s1",
            list_workspace_files=lambda: list(files),
            stat_workspace_file=stat,
            read_workspace_file_to_path=download,
        ),
        skill_mgr=SimpleNamespace(
            sync_local_skills_to_volume=lambda: 0,
            build_catalog_xml=lambda: "<skills />",
            export_volume_skills_to_local=lambda: list(exported),
        ),
        store=store_with(save_runtime_settings=lambda _sid, data: saved.append(dict(data))),
        workspace_state="known_empty",
    )
    with opened_client(runtime) as (client, _):
        assert client.get("/sessions/s1/files").status_code == 200
        assert client.get("/sessions/s1/workspace/stat/files/a.csv").status_code == 404
        assert client.get("/sessions/s1/workspace/files/files/a.csv").status_code == 404
        assert client.post("/skills/pull?session_id=s1").json() == {"exported": []}
        assert runtime.workspace_state == "known_empty" and saved == []

        files.append("files/a.csv")
        assert client.get("/sessions/s1/files").status_code == 200
        assert runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        assert client.get("/sessions/s1/workspace/stat/files/a.csv").status_code == 200
        assert runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        assert client.get("/sessions/s1/workspace/files/files/a.csv").status_code == 200
        assert runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        selected = client.put("/sessions/s1/context", json={"path": "files/a.csv"})
        removed = client.delete("/sessions/s1/context")
        assert selected.status_code == 200
        assert removed.status_code == 200
        assert runtime.context is None and runtime.workspace_state == "unknown"

        runtime.workspace_state = "known_empty"
        exported.append("custom-skill")
        assert client.post("/skills/pull?session_id=s1").json() == {"exported": ["custom-skill"]}
        assert runtime.workspace_state == "unknown"


# Application lifecycle


@pytest.mark.parametrize("failure", [None, "database", "tracing", "registry", "experiments"])
def test_lifespan_unwinds_partial_startup_and_waits_for_experiments_before_storage(monkeypatch, failure):
    app_module = importlib.import_module("fleet_rlm.api.app")
    events = []

    class Store:
        @classmethod
        def open(cls, *_args, **_kwargs):
            events.append("store.acquire")
            return cls()

        def initialize(self):
            events.append("store.initialize")
            if failure == "database":
                raise RuntimeError("startup failed")

        def close_migration_pool(self):
            events.append("store.ddl.close")

        def close(self):
            events.append("store.close")

    class Registry:
        def __init__(self, *_args):
            events.append("registry.acquire")
            if failure == "registry":
                raise RuntimeError("startup failed")

        def close(self):
            events.append("registry.close")

    class Executor:
        def shutdown(self, **kwargs):
            assert kwargs == {"wait": False, "cancel_futures": True}
            events.append("executor.close")

    class Experiments:
        def __init__(self, *_args):
            events.append("experiments.acquire")

        async def initialize(self):
            events.append("experiments.initialize")
            if failure == "experiments":
                raise RuntimeError("startup failed")

        async def close(self):
            events.append("experiments.close")

    def tracing(_settings):
        events.append("tracing.initialize")
        if failure == "tracing":
            raise RuntimeError("startup failed")
        return "experiment-id"

    monkeypatch.setattr(app_module, "Store", Store)
    monkeypatch.setattr(app_module, "SessionRuntimeRegistry", Registry)
    monkeypatch.setattr(app_module.turns, "create_turn_executor", Executor)
    monkeypatch.setattr(app_module, "ExperimentManager", Experiments)
    monkeypatch.setattr(app_module, "_configure_tracing", tracing)
    monkeypatch.setattr(app_module.mlflow, "flush_trace_async_logging", lambda: events.append("tracing.flush"))
    app = app_module.create_app(settings=FleetSettings(_env_file=None))
    if failure:
        with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
            pass
    else:
        with TestClient(app):
            assert "experiments.close" not in events
    assert events.index("store.ddl.close") < events.index("store.close")
    if "experiments.acquire" in events:
        assert events.index("experiments.close") < events.index("executor.close")
    if "registry.close" in events:
        assert events.index("executor.close") < events.index("registry.close") < events.index("store.close")
    if failure not in {"database", "tracing"}:
        assert events[-1] == "tracing.flush"


# OpenAPI contract


def test_openapi_is_version_valid_and_deterministic():
    app = create_app(settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False)
    schema = app.openapi()
    validate(schema)
    regenerated = create_app(
        settings=FleetSettings(_env_file=None), initialize_db=False, configure_observability=False
    ).openapi()
    assert schema == regenerated
    assert schema["openapi"] == "3.1.0"
    streams = []
    for path in schema["paths"].values():
        for operation in path.values():
            for response in operation.get("responses", {}).values():
                media = response.get("content", {}).get("text/event-stream")
                if media is not None:
                    streams.append(media)
                    assert media["schema"] == {"type": "string"}
                    assert "itemSchema" not in media
    assert len(streams) == 1


# Doctor probes and configuration checks


@pytest.mark.parametrize("sub_scope", ["team.models", "other.serving"])
@pytest.mark.parametrize("missing", [False, True])
def test_model_readiness_checks_configured_allowlist_without_discovery(monkeypatch, sub_scope, missing):
    from fleet_rlm.doctor import check_models

    settings = FleetSettings(_env_file=None, fleet_llm_model=f"openai/{sub_scope}.model")

    def discover(_settings, *, role):
        assert role == "shared"
        if missing:
            raise RuntimeError("private provider error")
        return ["openai/discovered"]

    monkeypatch.setattr(FleetSettings, "list_models", discover)
    result = check_models(settings)
    assert result["status"] == "ready"
    assert result["models"] == ([] if missing else ["openai/discovered"])
    assert "private provider error" not in result["message"]


@pytest.mark.parametrize("json_mode", [False, True])
def test_model_probe_uses_normalized_request_and_response(monkeypatch, json_mode):
    import dspy
    from dspy.lm15 import Request

    from fleet_rlm.doctor import check_models

    calls = []

    class FakeLM:
        def __init__(self, model):
            self.model = model

        def copy(self, **kwargs):
            calls.append(("copy", kwargs))
            return self

        def __call__(self, request):
            assert isinstance(request, Request)
            calls.append(("request", request))
            return SimpleNamespace(text='{"status":"OK"}' if json_mode else "OK")

        def close(self):
            calls.append(("close", None))

    def fake_lm(model, **kwargs):
        lm = FakeLM(model)
        lm.kwargs = {
            key: value for key, value in kwargs.items() if key in {"response_format", "max_tokens", "temperature"}
        }
        return lm

    monkeypatch.setattr(dspy, "LM", fake_lm)
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/test-root",
        fleet_root_adapter="json" if json_mode else "chat",
        fleet_root_response_format="json_object" if json_mode else "auto",
    )
    result = check_models(settings, probe_models=True)
    request = next(value for kind, value in calls if kind == "request")
    assert request.model == "openai/test-root"
    expected = 'Return exactly this JSON object: {"status":"OK"}' if json_mode else "Reply with exactly OK"
    assert request.messages[0].text == expected
    assert request.config.response_format == ({"type": "json_object"} if json_mode else None)
    assert request.config.max_tokens == 16
    assert calls[0] == ("copy", {"num_retries": 0, "max_tokens": 16, "cache": False, "callbacks": []})
    assert calls[-1] == ("close", None)
    assert result["status"] == "ready"
    assert result["inference_tested"] is True
    assert "probe succeeded" in result["message"]


def test_lakebase_probe_uses_candidate_tracking_auth_without_mutating_environment(monkeypatch):
    import os

    import databricks.sdk
    import databricks.sdk.core

    import fleet_rlm.store as db
    from fleet_rlm.doctor import check_database

    observed = []

    def workspace_client(**kwargs):
        observed.append(kwargs["config"])
        return object()

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", workspace_client)
    # Config's normal constructor probes workspace metadata and retries on network failure.
    # This test checks candidate credentials are wired through, not SDK discovery behavior.
    monkeypatch.setattr(databricks.sdk.core, "Config", lambda **kw: SimpleNamespace(**kw))

    class Connection:
        @classmethod
        def connect(cls, *_a, **_kw):
            raise RuntimeError("probe reached connection")

    monkeypatch.setattr(db, "lakebase_connection_class", lambda *_a: Connection)
    settings = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        databricks_host="https://candidate.test",
        databricks_token="candidate-token",
        fleet_database_url="postgresql://reader@db.invalid/fleet?sslmode=require",
        fleet_lakebase_endpoint="projects/a/branches/b/endpoints/c",
    )
    result = check_database(settings)
    assert result["status"] == "blocked"
    assert "RuntimeError" in result["message"]
    assert observed[0].host == "https://candidate.test"
    assert observed[0].token == "candidate-token"
    assert "DATABRICKS_TOKEN" not in os.environ


def test_settings_validates_json_mode():
    with pytest.raises(ValueError, match="JSON adapter"):
        FleetSettings(_env_file=None, fleet_root_response_format="json_object")
    settings = FleetSettings(
        _env_file=None,
        fleet_root_adapter="json",
        fleet_root_response_format="json_object",
    )
    assert settings.resolved_model("root").response_format == "json_object"


# Consolidated from api/test_improve.py


def test_worker_log_fd_uses_private_regular_file_and_rejects_symlinks(tmp_path: Path):
    import os
    import stat

    fd = _open_worker_log(tmp_path, "optimize-run1")
    try:
        log_path = tmp_path / "runs" / "optimize-run1" / "worker.log"
        assert log_path.is_file()
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    finally:
        os.close(fd)

    evil = tmp_path / "outside.log"
    evil.write_text("do not clobber\n")
    symlink_run_dir = tmp_path / "runs" / "optimize-sym"
    symlink_run_dir.mkdir(parents=True)
    (symlink_run_dir / "worker.log").symlink_to(evil)
    with pytest.raises(RuntimeError, match="symlink"):
        _open_worker_log(tmp_path, "optimize-sym")
    assert evil.read_text() == "do not clobber\n"


def test_failed_approval_exposes_sanitized_failure_and_success_clears_it(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run("s1-run", {"status": "approving", "candidate_version": "s1-run"})
        directory = tmp_path / "candidates"
        directory.mkdir()
        manifest = {
            "version": "s1-run",
            "digest": hashlib.sha256(b"candidate").hexdigest(),
            "dependencies": {"incumbent_digest": hashlib.sha256(b"incumbent").hexdigest()},
            "gate": {"passed": True},
        }
        (directory / "s1-run.manifest.json").write_text(json.dumps(manifest))
        active = tmp_path / "fleet.json"
        active.write_bytes(b"incumbent")
        failure = {"type": "ValueError", "detail": "Approval failed; exception details omitted."}
        store.update_optimization_run("s1-run", {"worker_outcome": {"status": "failed", "failure": failure}})
        await manager._finish("s1-run", SimpleNamespace(returncode=1), None, approval=True)
        record = store.get_optimization_run("s1-run")
        assert record["status"] == "completed" and record["failure"] == failure
        active.write_bytes(b"candidate")
        store.update_optimization_run("s1-run", {"status": "approving", "worker_outcome": {"status": "approved"}})
        await manager._finish("s1-run", SimpleNamespace(returncode=0), None, approval=True)
        record = store.get_optimization_run("s1-run")
        assert record["status"] == "approved" and record["failure"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("manifest", [[], {"version": "s1-run", "gate": None}, {"version": "s1-run", "digest": ""}])
def test_invalid_approval_manifest_becomes_interrupted_without_install(tmp_path, manifest):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run("s1-run", {"status": "approving"})
        directory = tmp_path / "candidates"
        directory.mkdir()
        (directory / "s1-run.manifest.json").write_text(json.dumps(manifest))
        await manager._recover_approval(store.get_optimization_run("s1-run"))
        assert store.get_optimization_run("s1-run")["status"] == "interrupted"
        assert not (tmp_path / "fleet.json").exists()

    asyncio.run(scenario())


def test_worker_log_redirection_never_writes_progress_even_when_storage_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    async def scenario() -> None:
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)

        class FakeStdin:
            def write(self, _data: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

        class FakeProcess:
            stdin = FakeStdin()
            returncode = 0

            def __init__(self, output: bytes) -> None:
                self.stdout = asyncio.StreamReader()
                self.stdout.feed_data(output)
                self.stdout.feed_eof()

            async def wait(self) -> int:
                return 0

        captured: dict[str, Any] = {}
        outputs = iter((b"search diagnostics\n", b"x" * 1_100_000, b"approval retry\n"))

        async def fake_spawn(*_args: str, **kwargs: Any) -> FakeProcess:
            captured.update(kwargs)
            return FakeProcess(next(outputs))

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        await manager._execute("s1-run", "optimize", ExperimentOptions(), approval=False)
        assert captured["stdout"] == asyncio.subprocess.PIPE
        assert captured["stderr"] is asyncio.subprocess.STDOUT
        log_path = tmp_path / "runs" / "s1-run" / "worker.log"
        assert log_path.read_bytes() == b"search diagnostics\n"
        await manager._execute("s1-run", "optimize", ExperimentOptions(), approval=True)
        expected = b"search diagnostics\n" + b"x" * (1_000_000 - len(b"search diagnostics\n"))
        assert log_path.read_bytes() == expected
        await manager._execute("s1-run", "optimize", ExperimentOptions(), approval=True)
        assert log_path.read_bytes() == expected
        assert not (tmp_path / "runs" / "s1-run" / "gepa.stop").exists()

    asyncio.run(scenario())


class MemoryStore:
    """Small ledger fake with the same ownership/status fences as Store."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._lease = threading.Lock()
        self._owners: dict[str, threading.Lock] = {}
        self.settings: dict[str, dict[str, Any]] = {}

    def global_settings(self, scope: str) -> dict[str, Any]:
        return dict(self.settings.get(scope, {}))

    def update_global_settings(self, scope: str, changes: dict[str, Any]) -> None:
        merged = {**self.settings.get(scope, {}), **changes}
        self.settings[scope] = {name: value for name, value in merged.items() if value is not None}

    def acquire_optimization_owner_lease(self, owner_id):
        with self._lock:
            lock = self._owners.setdefault(owner_id, threading.Lock())
            if not lock.acquire(blocking=False):
                raise RuntimeError("API owner lease already held")
            return _Lease(lock)

    def optimization_owner_alive(self, owner_id):
        with self._lock:
            return owner_id in self._owners and self._owners[owner_id].locked()

    def acquire_optimization_lease(self):
        if not self._lease.acquire(blocking=False):
            raise RuntimeError("An optimization run is already owned")
        return _Lease(self._lease)

    def create_optimization_run(
        self,
        run_id: str,
        request_id: str,
        options: dict[str, Any],
        owner_id: str,
        *,
        kind: str = "optimize",
        managed: bool = False,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        with self._lock:
            if any(row["status"] in {"running", "approving"} for row in self.rows.values()):
                raise ValueError("An optimization run is already active")
            if any(row["request_id"] == request_id for row in self.rows.values()):
                raise ValueError("optimization request already exists")
            now = datetime.now(UTC).isoformat()
            record = {
                "run_id": run_id,
                "request_id": request_id,
                "kind": kind,
                "options": dict(options),
                "owner_id": owner_id,
                "managed": managed,
                "trigger": trigger,
                "status": "running",
                "stage": "preparing",
                "stop_requested": False,
                "created_at": now,
                "updated_at": now,
            }
            self.rows[run_id] = record
            return dict(record)

    def get_optimization_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get(run_id)
            return dict(row) if row else None

    def list_optimization_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self.rows.values()]

    def update_optimization_run(
        self,
        run_id: str,
        updates: dict[str, Any],
        *,
        owner_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None or (owner_id is not None and row["owner_id"] != owner_id):
                return None
            if expected_statuses is not None and row["status"] not in expected_statuses:
                return None
            status = updates.get("status", row["status"])
            if status in {"running", "approving"} and any(
                other is not row and other["status"] in {"running", "approving"} for other in self.rows.values()
            ):
                raise ValueError("Another optimization run is active")
            row.update(updates)
            row["updated_at"] = datetime.now(UTC).isoformat()
            return dict(row)

    def request_optimization_stop(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None:
                raise ValueError("optimization run was not found")
            if row["status"] != "running":
                raise ValueError("only a running optimization can be stopped")
            row["stop_requested"] = True
            return dict(row)

    def claim_optimization_review(self, run_id: str, owner_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None or row["status"] not in {"completed", "approving"}:
                raise ValueError("only a completed or recoverable approving run can be reviewed")
            if any(other is not row and other["status"] in {"running", "approving"} for other in self.rows.values()):
                raise ValueError("Another optimization run is active")
            row.update(status="approving", owner_id=owner_id)
            return dict(row)

    def reject_optimization_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.rows.get(run_id)
            if row is None or row["status"] not in {"completed", "rejected"}:
                raise ValueError("only a completed optimization run can be rejected")
            row.update(status="rejected", decision="rejected")
            return dict(row)

    def import_optimization_run(self, record: dict[str, Any]) -> bool:
        with self._lock:
            if record["run_id"] in self.rows:
                return False
            if record.get("status") in {"running", "approving"} and any(
                row["status"] in {"running", "approving"} for row in self.rows.values()
            ):
                raise ValueError("Another optimization run is active")
            self.rows[record["run_id"]] = {"kind": "optimize", **record}
            return True


class _Lease:
    def __init__(self, lock: threading.Lock) -> None:
        self.lock = lock

    def close(self) -> None:
        if self.lock.locked():
            self.lock.release()


def _settings(root: Path) -> FleetSettings:
    return FleetSettings(_env_file=None, compiled_program_dir=str(root), min_split_examples=1)


def _manager_with_store(root: Path) -> tuple[MemoryStore, ExperimentManager]:
    store = MemoryStore()
    return store, ExperimentManager(_settings(root), store)  # type: ignore[arg-type]


def _app_client(root: Path, store: MemoryStore) -> tuple[Any, TestClient]:
    app = create_app(
        settings=_settings(root),
        initialize_db=False,
        configure_observability=False,
        store=store,  # type: ignore[arg-type]
    )
    return app, TestClient(app, raise_server_exceptions=False)


def _optimization_run(run_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "request_id": f"request-{run_id}",
        "options": {},
        "owner_id": "old-api-owner",
        "managed": True,
        "status": "running",
        "stage": "search",
        "stop_requested": False,
        **overrides,
    }


def _completed(store: MemoryStore, run_id: str, *, gate: bool = True, **overrides: Any) -> bool:
    now = datetime.now(UTC).isoformat()
    return store.import_optimization_run(
        {
            "run_id": run_id,
            "request_id": f"request-{run_id}",
            "options": {},
            "owner_id": "imported",
            "managed": False,
            "status": "completed",
            "stage": "completed",
            "created_at": now,
            "updated_at": now,
            "candidate_version": run_id,
            "gate": {"passed": gate, "reasons": []},
            **overrides,
        }
    )


def test_api_owned_run_is_idempotent_global_and_drain_fences_admission(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_execute(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
            _ = kind, options, approval
            started.set()
            await finish.wait()
            store.update_optimization_run(
                run_id,
                {"status": "approved" if approval else "completed", "stage": "completed"},
                owner_id=manager.owner_id,
                expected_statuses=("approving",) if approval else ("running",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        options = ExperimentOptions(max_metric_calls=8)
        first = await manager.start("request-1", options)
        await started.wait()
        retry = await manager.start("request-1", options)
        assert retry["run_id"] == first["run_id"]
        with pytest.raises(ExperimentBusyError):
            await manager.start("request-2", options)

        draining = await manager.begin_drain()
        assert draining["accepting"] is False and draining["busy"] is True
        with pytest.raises(ExperimentBusyError):
            await manager.start("request-3", options)
        finish.set()
        await manager.close()
        final = await manager.get(first["run_id"])
        assert final["status"] == "completed"
        assert manager.busy is False

    asyncio.run(scenario())


def test_api_owned_cancel_persists_stop_intent_and_cooperatively_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def fake_execute(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
            _ = kind, options, approval
            started.set()
            while not (store.get_optimization_run(run_id) or {}).get("stop_requested"):
                await asyncio.sleep(0.01)
            stopped.set()
            store.update_optimization_run(
                run_id,
                {"status": "cancelled", "stage": "cancelled", "candidate_version": None},
                owner_id=manager.owner_id,
                expected_statuses=("running",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.start("cancel-request", ExperimentOptions())
        await started.wait()
        accepted = await manager.cancel(record["run_id"])
        assert accepted["stop_requested"] is True
        assert (tmp_path / "runs" / record["run_id"] / "gepa.stop").is_file()
        await stopped.wait()
        await manager._task
        assert (await manager.get(record["run_id"]))["status"] == "cancelled"
        await manager.close()

    asyncio.run(scenario())


def test_review_retry_reuses_claim_and_manager_close_waits_for_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        _completed(store, "review-candidate")
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_execute(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> None:
            assert approval is True
            _ = kind, options
            started.set()
            await finish.wait()
            store.update_optimization_run(
                run_id,
                {"status": "approved", "stage": "approved", "decision": "approved"},
                owner_id=manager.owner_id,
                expected_statuses=("approving",),
            )

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.review("review-candidate", "approved", "review-key")
        await started.wait()
        retry = await manager.review("review-candidate", "approved", "review-key")
        assert retry["status"] == "approving"
        assert retry["review_request_id"] == "review-key"
        with pytest.raises(ExperimentBusyError):
            await manager.review("review-candidate", "rejected", "other-key")

        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert not closing.done()
        finish.set()
        await closing
        assert (await manager.get(record["run_id"]))["status"] == "approved"

    asyncio.run(scenario())


def test_initialize_fences_abandoned_managed_worker_as_interrupted(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        run_id = "recovery-run"
        store.import_optimization_run(_optimization_run(run_id, request_id="old-request"))
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        record = store.get_optimization_run(run_id)
        assert record["status"] == "interrupted"
        assert record["stop_requested"] is False
        assert record["finished_at"]
        assert record["candidate_version"] is None
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_started", [False, True])
@pytest.mark.parametrize("status", ["running", "approving"])
def test_second_api_preserves_live_foreign_owner_including_pending_launch(tmp_path, worker_started, status):
    async def scenario():
        store, owner = _manager_with_store(tmp_path)
        await owner.initialize()
        record = store.create_optimization_run("s1-run", "request", {}, owner.owner_id, managed=True)
        store.update_optimization_run("s1-run", {"status": status})
        worker = store.acquire_optimization_lease() if worker_started else None
        observer = ExperimentManager(_settings(tmp_path), store)
        try:
            await observer.initialize()
            await observer.list()
            current = store.get_optimization_run(record["run_id"])
            assert current["status"] == status
            assert current["stop_requested"] is False
            assert not (tmp_path / "runs" / record["run_id"] / "gepa.stop").exists()
        finally:
            if worker:
                worker.close()
            await observer.close()
            await owner.close()
        assert not store.optimization_owner_alive(owner.owner_id)

    asyncio.run(scenario())


def test_owner_lease_survives_drain_until_worker_joined(tmp_path):
    async def scenario():
        store, manager = _manager_with_store(tmp_path)
        await manager.initialize()
        finish = asyncio.Event()
        manager._task = asyncio.create_task(finish.wait())
        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert manager.accepting is False
        assert store.optimization_owner_alive(manager.owner_id)
        finish.set()
        await closing
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


@pytest.mark.parametrize("approval", [False, True])
def test_manager_recovers_own_finished_worker_after_final_write_failure(tmp_path, monkeypatch, approval):
    async def scenario():
        store, manager = _manager_with_store(tmp_path)
        await manager.initialize()
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run(
            "s1-run",
            {"status": "approving" if approval else "running", "worker_outcome": {"status": "completed"}},
        )
        if approval:
            directory = tmp_path / "candidates"
            directory.mkdir()
            (directory / "s1-run.manifest.json").write_text(
                json.dumps(
                    {
                        "version": "s1-run",
                        "digest": hashlib.sha256(b"candidate").hexdigest(),
                        "gate": {"passed": True},
                        "dependencies": {},
                    }
                )
            )
            (tmp_path / "fleet.json").write_bytes(b"candidate")
        update = store.update_optimization_run

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("final write unavailable")

        monkeypatch.setattr(store, "update_optimization_run", unavailable)
        manager._task = asyncio.create_task(
            manager._finish("s1-run", SimpleNamespace(returncode=0), None, approval=approval)
        )
        with pytest.raises(RuntimeError, match="final write unavailable"):
            await manager._task
        assert not manager.busy and store.optimization_owner_alive(manager.owner_id)
        monkeypatch.setattr(store, "update_optimization_run", update)
        await manager.list()
        record = store.get_optimization_run("s1-run")
        assert record["status"] == ("approved" if approval else "interrupted")
        if not approval:
            assert record["candidate_version"] is None
        # close() still propagates the original task failure after releasing its lease.
        with pytest.raises(RuntimeError, match="final write unavailable"):
            await manager.close()
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


def test_initialization_failure_releases_owner_lease_on_cleanup(tmp_path, monkeypatch):
    async def scenario():
        store, manager = _manager_with_store(tmp_path)

        def fail(*_args, **_kwargs):
            raise RuntimeError("history unavailable")

        # initialize() reconciles unthrottled, so the failure is injected on the shared body.
        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", fail)
        with pytest.raises(RuntimeError, match="history unavailable"):
            await manager.initialize()
        assert store.optimization_owner_alive(manager.owner_id)
        await manager.close()
        assert not store.optimization_owner_alive(manager.owner_id)

    asyncio.run(scenario())


def test_recovery_keeps_workers_active_until_global_lease_is_released(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        managed = _optimization_run("managed-orphan-run", request_id="managed-request")
        standalone = _optimization_run(
            "cli-orphan-run",
            request_id="cli-request",
            owner_id="cli-owner",
            managed=False,
        )
        store.import_optimization_run(managed)
        store._lease.acquire()
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        await manager.initialize()
        assert store.get_optimization_run(managed["run_id"])["status"] == "running"
        assert store.get_optimization_run(managed["run_id"])["stop_requested"] is True
        assert (tmp_path / "runs" / managed["run_id"] / "gepa.stop").is_file()
        assert store.get_optimization_run(managed["run_id"])["status"] == "running"
        store._lease.release()
        await manager.list()
        assert store.get_optimization_run(managed["run_id"])["status"] == "interrupted"
        await manager.close()

        cli_store = MemoryStore()
        cli_store.import_optimization_run(standalone)
        cli_store._lease.acquire()
        cli_manager = ExperimentManager(_settings(tmp_path), cli_store)  # type: ignore[arg-type]
        await cli_manager.initialize()
        assert cli_store.get_optimization_run(standalone["run_id"])["status"] == "running"
        cli_store._lease.release()
        snapshot = await cli_manager.list()
        assert snapshot["jobs"][0]["status"] == "interrupted"
        await cli_manager.close()

    asyncio.run(scenario())


def test_same_approval_retry_reconciles_after_prior_owner_exits(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        run_id = "retry-recovery-candidate"
        installed = b"approved candidate"
        digest = hashlib.sha256(installed).hexdigest()
        candidates = tmp_path / "candidates"
        candidates.mkdir()
        (candidates / f"{run_id}.manifest.json").write_text(
            json.dumps(
                {
                    "version": run_id,
                    "digest": digest,
                    "gate": {"passed": True},
                    "dependencies": {"incumbent_digest": "old-incumbent"},
                }
            )
        )
        (tmp_path / "fleet.json").write_bytes(installed)
        store.import_optimization_run(
            _optimization_run(
                run_id,
                request_id="candidate-request",
                owner_id="old-review-owner",
                status="approving",
                stage="approving",
                review_request_id="approve-once",
                review_decision="approved",
            )
        )
        store._lease.acquire()
        manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
        with pytest.raises(ExperimentBusyError):
            await manager.review(run_id, "approved", "approve-once")
        assert store.get_optimization_run(run_id)["status"] == "approving"
        store._lease.release()
        result = await manager.review(run_id, "approved", "approve-once")
        assert result["status"] == "approved"
        assert manager._task is None
        await manager.close()

    asyncio.run(scenario())


def test_interrupted_run_keeps_its_saved_comparison_when_terminal_candidate_version_is_cleared(
    tmp_path: Path,
) -> None:
    run_id = "interrupted-evidence-partial"
    store = MemoryStore()
    store.import_optimization_run(
        {
            "run_id": run_id,
            "request_id": "partial-request",
            "options": {},
            "owner_id": "imported",
            "managed": False,
            "status": "interrupted",
            "stage": "interrupted",
            "candidate_version": None,
        }
    )
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f"{run_id}.comparison.json").write_text('{"report":"partial diagnostics"}')
    manager = ExperimentManager(_settings(tmp_path), store)  # type: ignore[arg-type]
    detail = asyncio.run(manager.detail(run_id))
    assert detail["comparison"] == {"report": "partial diagnostics"}


def test_managed_subprocess_drains_noisy_output_without_replaying_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store = MemoryStore()
        settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path), databricks_token="secret-token")
        manager = ExperimentManager(settings, store)  # type: ignore[arg-type]
        command: list[str] = []
        captured: dict[str, Any] = {}
        run_id_holder: dict[str, str] = {}

        class FakeStdin:
            def write(self, data: bytes) -> None:
                captured["stdin"] = data

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                captured["stdin_closed"] = True

        class FakeProcess:
            stdin = FakeStdin()

            def __init__(self, stdout: asyncio.StreamReader, stderr: asyncio.StreamReader) -> None:
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = 0

            async def wait(self) -> int:
                run_id = run_id_holder["run_id"]
                row = store.get_optimization_run(run_id)
                store.update_optimization_run(
                    run_id,
                    {"worker_outcome": {"status": "completed", "candidate_version": run_id}},
                    owner_id=row["owner_id"],
                    expected_statuses=("running",),
                )
                return self.returncode

        async def fake_spawn(*args: str, **kwargs: Any) -> FakeProcess:
            command.extend(args)
            captured["spawn_kwargs"] = kwargs
            run_id = args[args.index("--managed-run-id") + 1]
            run_id_holder["run_id"] = run_id
            from fleet_rlm.optimize.gepa import GEPAProgressReporter

            reporter = GEPAProgressReporter(run_id, store=store, owner_id=manager.owner_id, emit_events=False)
            reporter.on_budget_updated({"metric_calls_used": 20, "iteration": 2})
            assert store.get_optimization_run(run_id)["progress"]["metric_calls_used"] == 20
            stdout = asyncio.StreamReader()
            stdout.feed_data(
                b"x" * 90_000
                + b"\n"
                + (
                    f'FLEET_EVENT {{"type":"progress","run_id":"{run_id}","stage":"search",'
                    '"metric_calls_used":10,"prompt":"must not persist"}\n'
                    f'FLEET_EVENT {{"type":"stage","run_id":"{run_id}","stage":"validation"}}\n'
                    f'FLEET_EVENT {{"type":"terminal","run_id":"{run_id}","status":"completed"}}\n'
                ).encode()
            )
            stdout.feed_eof()
            stderr = asyncio.StreamReader()
            stderr.feed_data(b"Authorization: Bearer secret-token\n")
            stderr.feed_eof()
            return FakeProcess(stdout, stderr)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        real_command = manager._command

        def capture_command(run_id: str, kind: str, options: ExperimentOptions, *, approval: bool) -> list[str]:
            run_id_holder["run_id"] = run_id
            result = real_command(run_id, kind, options, approval=approval)
            result.insert(result.index("--structured-events"), "--synthetic-output")
            return result

        manager._command = capture_command  # type: ignore[method-assign]
        await manager.start("worker-request", ExperimentOptions())
        await manager._task
        record = await manager.get(run_id_holder["run_id"])
        assert record["status"] == "completed"
        assert record["progress"] == {"metric_calls_used": 20, "iteration": 2}
        assert captured["stdin_closed"] is True
        assert b"secret-token" in captured["stdin"]
        assert "secret-token" not in command
        assert "--managed-owner-id" in command
        assert captured["spawn_kwargs"]["stdin"] is asyncio.subprocess.PIPE
        assert captured["spawn_kwargs"]["stdout"] == asyncio.subprocess.PIPE
        assert captured["spawn_kwargs"]["stderr"] is asyncio.subprocess.STDOUT
        assert captured["spawn_kwargs"]["start_new_session"] is True

    asyncio.run(scenario())


def test_terminal_event_does_not_release_global_slot_until_worker_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        spawned = threading.Event()
        allow_exit = threading.Event()
        run_id: dict[str, str] = {}

        class FakeStdin:
            def write(self, _data: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

        class FakeProcess:
            stdin = FakeStdin()
            returncode = 0

            def __init__(self, stdout: asyncio.StreamReader, stderr: asyncio.StreamReader) -> None:
                self.stdout = stdout
                self.stderr = stderr

            async def wait(self) -> int:
                await asyncio.to_thread(allow_exit.wait)
                row = store.get_optimization_run(run_id["value"])
                store.update_optimization_run(
                    run_id["value"],
                    {"worker_outcome": {"status": "completed", "candidate_version": run_id["value"]}},
                    owner_id=row["owner_id"],
                    expected_statuses=("running",),
                )
                return 0

        async def fake_spawn(*args: str, **_kwargs: Any) -> FakeProcess:
            run_id["value"] = args[args.index("--managed-run-id") + 1]
            stdout = asyncio.StreamReader()
            stdout.feed_data(
                (
                    f'FLEET_EVENT {{"type":"progress","run_id":"{run_id["value"]}","stage":"search",'
                    '"metric_calls_used":1}\n'
                    f'FLEET_EVENT {{"type":"terminal","run_id":"{run_id["value"]}","status":"completed"}}\n'
                ).encode()
            )
            stdout.feed_eof()
            stderr = asyncio.StreamReader()
            stderr.feed_eof()
            spawned.set()
            return FakeProcess(stdout, stderr)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        record = await manager.start("reap-request", ExperimentOptions())
        assert await asyncio.to_thread(spawned.wait, 2)
        # Output has no authority while the managed child is still running.
        await asyncio.sleep(0.01)
        current = store.get_optimization_run(record["run_id"])
        assert "progress" not in current
        assert current["status"] == "running"
        with pytest.raises(ExperimentBusyError):
            await manager.start("second", ExperimentOptions())

        allow_exit.set()
        await manager._task
        assert (await manager.get(record["run_id"]))["status"] == "completed"
        await manager.close()

    asyncio.run(scenario())


def test_experiment_routes_keep_run_after_response_and_cancel_only_by_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    started = threading.Event()

    async def fake_execute(
        manager: ExperimentManager, run_id: str, kind: str, options: ExperimentOptions, *, approval: bool
    ) -> None:
        _ = kind, options
        assert not approval
        started.set()
        deadline = asyncio.get_running_loop().time() + 5
        while (
            not (manager.store.get_optimization_run(run_id) or {}).get("stop_requested")
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.01)
        manager.store.update_optimization_run(
            run_id,
            {"status": "cancelled", "stage": "cancelled", "candidate_version": None},
            owner_id=manager.owner_id,
            expected_statuses=("running",),
        )

    monkeypatch.setattr(ExperimentManager, "_execute", fake_execute)
    app, client = _app_client(tmp_path, store)
    with client:
        schema = app.openapi()
        operations = schema["paths"]
        assert operations["/improve/jobs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/JobList"
        }
        for path, method in (
            ("/improve/jobs", "post"),
            ("/improve/jobs/{run_id}/cancel", "post"),
            ("/improve/jobs/{run_id}/review", "post"),
        ):
            assert operations[path][method]["responses"]["202"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/JobEnvelope"
            }
        assert operations["/improve/jobs/{run_id}"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/JobDetail"}
        for path, method in (
            ("/improve/jobs", "get"),
            ("/improve/jobs", "post"),
            ("/improve/jobs/{run_id}", "get"),
            ("/improve/jobs/{run_id}/cancel", "post"),
            ("/improve/jobs/{run_id}/review", "post"),
        ):
            responses = operations[path][method]["responses"]
            for status in ("404", "409", "422", "503"):
                assert status in responses
                assert (
                    responses[status]["content"]["application/json"]["schema"]["$ref"]
                    == "#/components/schemas/ApiError"
                )
        request = {"request_id": "run_once", "max_metric_calls": 9}
        response = client.post("/improve/jobs", json=request)
        assert response.status_code == 202, response.text
        record = response.json()["job"]
        run_id = record["run_id"]
        assert record["status"] == "running"
        assert not {"owner_id", "managed", "worker_settings", "pid"}.intersection(record)
        assert started.wait(timeout=2)

        repeated = client.post("/improve/jobs", json=request)
        assert repeated.status_code == 202
        assert repeated.json()["job"]["run_id"] == run_id
        conflict = client.post("/improve/jobs", json={"request_id": "other", "max_metric_calls": 3})
        assert conflict.status_code == 409
        invalid = client.post("/improve/jobs", json={"request_id": "bad", "unknown": "field"})
        assert invalid.status_code == 422

        # The response/request connection has ended, but the detached manager task remains alive.
        snapshot = client.get("/improve/jobs")
        assert snapshot.status_code == 200
        assert snapshot.json()["active_run_id"] == run_id
        detail = client.get(f"/improve/jobs/{run_id}")
        assert detail.status_code == 200 and detail.json()["job"]["status"] == "running"
        premature_review = client.post(
            f"/improve/jobs/{run_id}/review",
            json={"decision": "approved", "request_id": "too_early"},
        )
        assert premature_review.status_code == 409

        cancel = client.post(f"/improve/jobs/{run_id}/cancel")
        assert cancel.status_code == 202, cancel.text
        assert cancel.json()["job"]["stop_requested"] is True
        for _ in range(50):
            latest = client.get(f"/improve/jobs/{run_id}").json()["job"]
            if latest["status"] == "cancelled":
                break
            time.sleep(0.01)
        assert latest["status"] == "cancelled"
        assert (tmp_path / "runs" / run_id / "gepa.stop").is_file()


def test_experiment_review_route_claims_idempotently_and_exposes_saved_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore()
    run_id = "candidate-123"
    _completed(store, run_id)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / f"{run_id}.comparison.json").write_text('{"manifest":{"evaluation":{}}}')
    approval_started = threading.Event()
    finish = threading.Event()

    async def fake_execute(
        manager: ExperimentManager, selected_run_id: str, kind: str, options: ExperimentOptions, *, approval: bool
    ) -> None:
        _ = kind, options
        assert approval
        approval_started.set()
        deadline = asyncio.get_running_loop().time() + 2
        while not finish.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        manager.store.update_optimization_run(
            selected_run_id,
            {"status": "approved", "stage": "approved", "decision": "approved"},
            owner_id=manager.owner_id,
            expected_statuses=("approving",),
        )

    monkeypatch.setattr(ExperimentManager, "_execute", fake_execute)
    _app, client = _app_client(tmp_path, store)
    try:
        with client:
            detail = client.get(f"/improve/jobs/{run_id}")
            assert detail.status_code == 200
            assert detail.json()["comparison"] == {"manifest": {"evaluation": {}}}

            review = {"decision": "approved", "request_id": "approve_once"}
            first = client.post(f"/improve/jobs/{run_id}/review", json=review)
            assert first.status_code == 202, first.text
            assert first.json()["job"]["status"] == "approving"
            assert approval_started.wait(timeout=2)
            retry = client.post(f"/improve/jobs/{run_id}/review", json=review)
            assert retry.status_code == 202
            assert retry.json()["job"]["status"] == "approving"
            assert "review_request_id" not in retry.json()["job"]
            busy = client.post("/improve/jobs", json={"request_id": "second_run", "gepa_budget": "light"})
            assert busy.status_code == 409
            finish.set()
            for _ in range(50):
                final = client.get(f"/improve/jobs/{run_id}").json()["job"]
                if final["status"] == "approved":
                    break
                time.sleep(0.01)
            assert final["status"] == "approved"
    finally:
        finish.set()


def test_rejection_records_decision_and_preserves_candidate_and_comparison(tmp_path: Path) -> None:
    store = MemoryStore()
    run_id = "reject-http-run"
    _completed(store, run_id)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    candidate = candidates / f"{run_id}.json"
    candidate.write_bytes(b"candidate artifact")
    comparison = candidates / f"{run_id}.comparison.json"
    comparison.write_text('{"manifest":{"review":"evidence"}}')
    _app, client = _app_client(tmp_path, store)
    with client:
        path = f"/improve/jobs/{run_id}/review"
        request = {"decision": "rejected", "request_id": "reject_once"}
        response = client.post(path, json=request)
        assert response.status_code == 202
        assert response.json()["job"]["status"] == "rejected"
        retry = client.post(path, json=request)
        assert retry.status_code == 202
        assert candidate.read_bytes() == b"candidate artifact"
        detail = client.get(f"/improve/jobs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["comparison"] == {"manifest": {"review": "evidence"}}


def test_database_cancel_remains_authoritative_when_stop_file_signal_fails(tmp_path, monkeypatch):
    from fleet_rlm.optimize.gepa import _check_cancelled, _database_stop_requested, _DatabaseStopper

    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)

        def fail_signal(*_args):
            raise OSError("cannot create stop marker")

        monkeypatch.setattr("fleet_rlm.experiments.request_run_stop", fail_signal)
        with pytest.raises(OSError, match="stop marker"):
            await manager.cancel("s1-run")
        stop_file = tmp_path / "runs" / "s1-run" / "gepa.stop"
        assert not stop_file.exists()
        # The stop file is absent, so the durable ledger fence must carry the cancellation on its own.
        _check_cancelled(stop_file)
        assert _database_stop_requested(store, "s1-run")
        assert _DatabaseStopper(store, "s1-run")(None)
        store.update_optimization_run("s1-run", {"worker_outcome": {"status": "completed"}})
        await manager._finish("s1-run", SimpleNamespace(returncode=0), None, approval=False)
        assert store.get_optimization_run("s1-run")["status"] == "cancelled"

    asyncio.run(scenario())


def test_managed_persistence_failure_keeps_sanitized_worker_diagnostic(tmp_path):
    async def scenario():
        store = MemoryStore()
        manager = ExperimentManager(FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path)), store)
        store.create_optimization_run("s1-run", "request", {}, manager.owner_id, managed=True)
        failure = {"type": "OptimizationPersistenceError", "detail": "Optimization failed; exception details omitted."}
        store.update_optimization_run("s1-run", {"worker_outcome": {"status": "failed", "failure": failure}})
        await manager._finish("s1-run", SimpleNamespace(returncode=1), None, approval=False)
        record = store.get_optimization_run("s1-run")
        assert record["status"] == "failed"
        assert record["failure"] == failure
        assert record["candidate_version"] is None

    asyncio.run(scenario())


def test_polled_recovery_is_throttled_to_one_attempt_per_interval(tmp_path: Path, monkeypatch) -> None:
    """Repeated polls cost one reconcile, not one per client refresh."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        attempts: list[float] = []
        real = ExperimentManager._reconcile_stale_runs

        async def counted(manager_self) -> None:
            attempts.append(time.monotonic())
            await real(manager_self)

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        for _ in range(5):
            await manager.list()
        assert len(attempts) == 1

    asyncio.run(scenario())


def test_recovery_interval_governs_when_the_next_attempt_is_due(tmp_path: Path, monkeypatch) -> None:
    """An elapsed interval admits exactly one new attempt; an unexpired one admits none."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        attempts: list[float] = []

        async def counted(*_args) -> None:
            attempts.append(time.monotonic())

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        clock = [1000.0]
        monkeypatch.setattr("fleet_rlm.experiments.time.monotonic", lambda: clock[0])
        await manager.list()
        assert len(attempts) == 1
        clock[0] += _RECOVERY_INTERVAL_SECONDS - 0.1
        await manager.list()
        assert len(attempts) == 1, "an unexpired interval must not reconcile again"
        clock[0] += 0.2
        await manager.list()
        assert len(attempts) == 2, "an expired interval must reconcile exactly once"

    asyncio.run(scenario())


def test_failed_recovery_still_counts_toward_the_interval(tmp_path: Path, monkeypatch) -> None:
    """A broken database is retried on the next interval, not on every poll."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        calls: list[int] = []

        async def fail(*_args) -> None:
            calls.append(1)
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", fail)
        with pytest.raises(RuntimeError, match="database unavailable"):
            await manager.list()
        await manager.list()
        assert len(calls) == 1

    asyncio.run(scenario())


def test_drain_bypasses_the_poll_throttle_so_shutdown_is_not_delayed(tmp_path: Path, monkeypatch) -> None:
    """A closed admission keeps reconciling on every status call during launcher drain."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        calls: list[int] = []

        async def counted(*_args) -> None:
            calls.append(1)

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        await manager.begin_drain()
        await manager.status()
        await manager.status()
        assert len(calls) == 3

    asyncio.run(scenario())


def test_admission_reconciles_without_consuming_the_poll_interval(tmp_path: Path, monkeypatch) -> None:
    """start() bypasses the poll throttle. Admission is refused so no worker is spawned."""

    async def scenario() -> None:
        _store, manager = _manager_with_store(tmp_path)
        calls: list[int] = []

        async def counted(*_args) -> None:
            calls.append(1)

        async def refuse() -> None:
            raise ExperimentBusyError("admission refused for the test")

        monkeypatch.setattr(ExperimentManager, "_reconcile_stale_runs", counted)
        monkeypatch.setattr(ExperimentManager, "_available", staticmethod(lambda: refuse()))
        clock = [1000.0]
        monkeypatch.setattr("fleet_rlm.experiments.time.monotonic", lambda: clock[0])
        await manager.initialize()
        assert calls == [1], "startup reconciles once"
        # The first poll reconciles and opens the window; a later one inside it does not.
        await manager.list()
        assert len(calls) == 2
        clock[0] += 0.1
        await manager.list()
        assert len(calls) == 2, "a poll inside the interval is throttled"
        with pytest.raises(ExperimentBusyError, match="admission refused"):
            await manager.start("request-1", ExperimentOptions())
        assert len(calls) == 3, "admission must reconcile even during the poll interval"
        assert not manager.busy, "no worker may be spawned by this test"

    asyncio.run(scenario())


class _LabelStore(MemoryStore):
    """MemoryStore plus the read models the Improve routes use."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: list[dict[str, Any]] = []
        self.references: dict[str, dict[str, Any]] = {}
        self.new_labels_since: list[str | None] = []
        self.ledger: dict[str, Any] = {"approved": None, "approving": []}

    def approved_program(self) -> dict[str, Any]:
        return self.ledger

    def improve_queue(self, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        return self.queue[offset : offset + limit]

    def turn_reference(self, turn_id: str) -> dict[str, Any] | None:
        return self.references.get(turn_id)

    def new_review_labels(self, since: str | None) -> int:
        self.new_labels_since.append(since)
        return 4


def _example(group: str, critical: bool = False) -> SimpleNamespace:
    return SimpleNamespace(source_group=group, critical=critical)


def _queue_row(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": "sess_a",
        "turn_id": "turn-1",
        "task": "Total revenue?",
        "answer": "Revenue was $1,250.50 across 42 orders",
        "review_status": None,
        "expected_answer": None,
        "expected_values": None,
        "critical": None,
        "source_group": None,
        "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
        **overrides,
    }


def test_readiness_reports_missing_requirements_and_new_labels_since_the_last_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _LabelStore()
    _completed(store, "optimize-earlier", created_at="2026-10-01T00:00:00+00:00")
    examples = [_example(f"sess_{index % 2}") for index in range(5)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    _app, client = _app_client(tmp_path, store)
    with client:
        report = client.get("/improve/readiness").json()
    assert report["ready"] is False
    assert (report["examples"], report["groups"], report["critical"]) == (5, 2, 0)
    assert len(report["missing"]) == 3
    assert report["new_labels_since_last_run"] == 4
    assert store.new_labels_since == ["2026-10-01T00:00:00+00:00"]


def test_new_labels_count_from_the_latest_run_that_finished_not_a_failed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _LabelStore()
    _completed(store, "optimize-finished", created_at="2026-10-01T00:00:00+00:00")
    _completed(store, "optimize-failed", status="failed", created_at="2026-10-09T00:00:00+00:00")
    _completed(store, "optimize-cancelled", status="cancelled", created_at="2026-10-10T00:00:00+00:00")
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: [])
    _app, client = _app_client(tmp_path, store)
    with client:
        assert client.get("/improve/readiness").status_code == 200
    assert store.new_labels_since == ["2026-10-01T00:00:00+00:00"]


@pytest.mark.parametrize("installed", ["missing", "changed", "matching", "never_approved"])
def test_readiness_reports_an_installed_program_that_contradicts_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: str
) -> None:
    store = _LabelStore()
    store.ledger = {
        "approved": None if installed == "never_approved" else hashlib.sha256(b"approved").hexdigest(),
        "approving": [],
    }
    if installed in {"changed", "matching"}:
        (tmp_path / "fleet.json").write_bytes(b"approved" if installed == "matching" else b"tampered")
    examples = [_example(f"sess_{index % 4}", critical=index == 0) for index in range(8)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    _app, client = _app_client(tmp_path, store)
    with client:
        report = client.get("/improve/readiness").json()
    broken = installed in {"missing", "changed"}
    assert report["ready"] is not broken
    assert any("approved global program" in item for item in report["missing"]) is broken


def test_readiness_counts_source_groups_the_way_the_split_does() -> None:
    from fleet_rlm.optimize.dataset import readiness

    examples = [_example(f"sess_{index % 4}" + (" " if index % 2 else ""), critical=index == 0) for index in range(8)]
    assert readiness(examples).groups == 4


def test_readiness_is_green_with_enough_groups_and_a_critical_holdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    examples = [_example(f"sess_{index % 4}", critical=index == 0) for index in range(8)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    _app, client = _app_client(tmp_path, _LabelStore())
    with client:
        report = client.get("/improve/readiness").json()
    assert report["ready"] is True and report["missing"] == []
    assert sum(report["split_sizes"].values()) == 8 and all(report["split_sizes"].values())


def test_queue_lists_turns_across_sessions_with_label_state_and_extracted_figures(tmp_path: Path) -> None:
    store = _LabelStore()
    store.queue = [
        _queue_row(),
        _queue_row(
            session_id="sess_b",
            turn_id="turn-2",
            review_status="approved",
            expected_answer="1250.5",
            expected_values=["1250.5"],
            critical=True,
            source_group="sess_b",
        ),
    ]
    _app, client = _app_client(tmp_path, store)
    with client:
        items = client.get("/improve/queue").json()["items"]
        assert client.get("/improve/queue?limit=0").status_code == 422
    assert [item["session_id"] for item in items] == ["sess_a", "sess_b"]
    assert items[0]["label"] == "unlabelled" and items[0]["figures"] == ["1250.50", "42"]
    assert items[1]["label"] == "approved" and items[1]["critical"] is True and items[1]["group"] == "sess_b"


def test_label_route_resolves_the_trace_from_the_stored_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _LabelStore()
    store.references["turn-1"] = {
        "session_id": "sess_a",
        "turn_id": "turn-1",
        "trace_id": "tr-1",
        "answer": "Full stored answer",
    }
    store.references["turn-2"] = {"session_id": "sess_a", "turn_id": "turn-2", "trace_id": None}
    calls: list[tuple[Any, ...]] = []

    def fake_label(store_arg: Any, trace_id: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((store_arg, trace_id, kwargs))
        return {
            "review_status": kwargs["status"] or "rejected",
            "expected_answer": kwargs["answer"],
            "expected_values": kwargs["values"] or [],
            "critical": bool(kwargs["critical"]),
            "source_group": kwargs["group"] or "sess_a",
        }

    monkeypatch.setattr("fleet_rlm.optimize.evaluation.label_turn", fake_label)
    _app, client = _app_client(tmp_path, store)
    with client:
        saved = client.post(
            "/improve/labels",
            json={"turn_id": "turn-1", "expected_answer": "1250.5", "expected_values": ["1250.5"], "critical": True},
        )
        assert saved.status_code == 200, saved.text
        # An omitted status stays omitted: a partial edit must not silently approve an existing label.
        assert calls[0][2]["status"] is None
        assert saved.json() == {
            "turn_id": "turn-1",
            "trace_id": "tr-1",
            "status": "rejected",
            "expected_answer": "1250.5",
            "expected_values": ["1250.5"],
            "critical": True,
            "group": "sess_a",
        }
        assert client.post("/improve/labels", json={"turn_id": "missing"}).status_code == 404
        assert client.post("/improve/labels", json={"turn_id": "turn-2"}).status_code == 422
        assert client.post("/improve/labels", json={"turn_id": "turn-1", "status": "maybe"}).status_code == 422
        assert (
            client.post("/improve/labels", json={"turn_id": "turn-1", "expected_values": ["x" * 100_001]}).status_code
            == 422
        )
    assert [(call[0], call[1]) for call in calls] == [(store, "tr-1")]
    assert calls[0][2]["default_answer"] == "Full stored answer"


def test_label_route_reports_a_failed_synchronization_with_its_recovery_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleet_rlm.optimize.evaluation import EvaluationError

    store = _LabelStore()
    store.references["turn-1"] = {"session_id": "sess_a", "turn_id": "turn-1", "trace_id": "tr-1"}

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise EvaluationError("MLflow labels were saved, but Lakebase curation failed (OperationalError)")

    monkeypatch.setattr("fleet_rlm.optimize.evaluation.label_turn", fail)
    _app, client = _app_client(tmp_path, store)
    with client:
        response = client.post("/improve/labels", json={"turn_id": "turn-1"})
    assert response.status_code == 503 and "Lakebase curation failed" in response.text


def test_history_lists_approved_versions_and_completed_evaluations(tmp_path: Path) -> None:
    store = _LabelStore()
    scores = {"baseline": {"dev": 0.4, "holdout": 0.5}, "candidate": {"dev": 0.6, "holdout": 0.7}}
    _completed(store, "optimize-approved", status="approved", scores=scores)
    _completed(store, "optimize-waiting")
    _completed(store, "sess_old-approved", status="approved", legacy_session_id="sess_old", scores=scores)
    _completed(store, "evaluate-rescore-done", kind="evaluate_rescore", scores=scores)
    _app, client = _app_client(tmp_path, store)
    with client:
        history = client.get("/improve/history").json()
    assert [job["run_id"] for job in history["versions"]] == ["optimize-approved"]
    assert history["versions"][0]["scores"] == scores
    assert [job["run_id"] for job in history["evaluations"]] == ["evaluate-rescore-done"]


def test_unknown_job_kinds_are_rejected_and_evaluate_kinds_are_accepted(tmp_path: Path) -> None:
    _app, client = _app_client(tmp_path, _LabelStore())
    with client:
        assert client.post("/improve/jobs", json={"request_id": "bad", "kind": "deploy"}).status_code == 422


@pytest.mark.parametrize(
    ("kind", "flags"),
    [
        ("evaluate_rescore", ["--evaluate", "rescore"]),
        ("evaluate_replay", ["--evaluate", "replay"]),
        ("optimize", []),
    ],
)
def test_each_job_kind_launches_the_same_worker_with_its_own_body(tmp_path: Path, kind: str, flags: list[str]) -> None:
    store, manager = _manager_with_store(tmp_path)
    command = list(manager._command("run-1", kind, ExperimentOptions(max_total_tokens=9), approval=False))
    assert command[1:4] == ["-m", "fleet_rlm.cli", "optimize"]
    assert command[4 : 4 + len(flags)] == flags
    assert "--managed-run-id" in command and command[command.index("--max-total-tokens") + 1] == "9"

    async def scenario() -> None:
        async def fake_execute(run_id: str, kind_arg: str, _options: ExperimentOptions, *, approval: bool) -> None:
            assert (kind_arg, approval) == (kind, False)
            store.update_optimization_run(run_id, {"status": "completed"}, owner_id=manager.owner_id)

        manager._execute = fake_execute  # type: ignore[method-assign]
        record = await manager.start(f"request-{kind}", ExperimentOptions(), kind)
        await manager.close()
        assert record["kind"] == kind and record["trigger"] == "manual"
        assert record["run_id"].startswith(kind.replace("_", "-") + "-")

    asyncio.run(scenario())


def test_an_evaluate_job_has_no_candidate_to_approve(tmp_path: Path) -> None:
    store = _LabelStore()
    _completed(store, "evaluate-replay-done", kind="evaluate_replay", candidate_version=None)
    _app, client = _app_client(tmp_path, store)
    with client:
        refused = client.post(
            "/improve/jobs/evaluate-replay-done/review", json={"decision": "approved", "request_id": "approve-eval"}
        )
    assert refused.status_code == 409 and "optimize" in refused.json()["detail"]
    assert store.get_optimization_run("evaluate-replay-done")["status"] == "completed"


def test_history_trend_carries_the_program_each_evaluation_measured(tmp_path: Path) -> None:
    store = _LabelStore()
    result = {
        "kind": "replay",
        "program": {"digest": "a" * 64, "version": "optimize-approved"},
        "splits": {"holdout": {"n": 3, "mean": 0.8, "errors": 0, "critical": {"passed": 1, "n": 1}}},
        "headline": {"holdout": 0.8, "critical_pass_rate": 1.0},
    }
    _completed(store, "evaluate-replay-done", kind="evaluate_replay", evaluation=result, trigger="manual")
    _app, client = _app_client(tmp_path, store)
    with client:
        evaluations = client.get("/improve/history").json()["evaluations"]
        listed = client.get("/improve/jobs").json()["jobs"]
    assert evaluations[0]["evaluation"] == result and evaluations[0]["trigger"] == "manual"
    assert listed[0]["evaluation"]["headline"] == {"holdout": 0.8, "critical_pass_rate": 1.0}


def test_settings_default_to_the_environment_and_saved_values_override_them(tmp_path: Path) -> None:
    store = _LabelStore()
    _app, client = _app_client(tmp_path, store)
    with client:
        assert client.get("/improve/settings").json() == {
            "auto_run": False,
            "min_new_labels": 5,
            "max_metric_calls": None,
            "max_total_tokens": 0,
            "auto_run_blocked": None,
        }
        # Unattended jobs need a spending cap, so auto-run cannot be enabled while the cap is unlimited.
        refused = client.put("/improve/settings", json={"auto_run": True})
        assert refused.status_code == 422 and "token cap" in refused.json()["detail"]
        assert store.settings == {}
        client.put("/improve/settings", json={"max_total_tokens": 5000})
        saved = client.put("/improve/settings", json={"auto_run": True, "min_new_labels": 2, "max_metric_calls": 30})
        assert saved.status_code == 200 and saved.json()["auto_run"] is True and saved.json()["min_new_labels"] == 2
        assert client.get("/improve/settings").json() == {
            "auto_run": True,
            "min_new_labels": 2,
            "max_metric_calls": 30,
            "max_total_tokens": 5000,
            "auto_run_blocked": None,
        }
        # Removing the cap while auto-run is on is refused too, and leaves the saved values untouched.
        assert client.put("/improve/settings", json={"max_total_tokens": 0}).status_code == 422
        assert client.get("/improve/settings").json()["max_total_tokens"] == 5000
        cleared = client.put("/improve/settings", json={"max_metric_calls": None})
        assert cleared.json()["max_metric_calls"] is None and cleared.json()["auto_run"] is True
        assert client.put("/improve/settings", json={"min_new_labels": 0}).status_code == 422
        assert client.put("/improve/settings", json={"surprise": 1}).status_code == 422
    assert store.settings["improve"] == {"auto_run": True, "min_new_labels": 2, "max_total_tokens": 5000}


def test_an_environment_change_that_removes_the_cap_blocks_auto_run_and_says_why(tmp_path: Path) -> None:
    store = _LabelStore()
    store.update_global_settings("improve", {"auto_run": True})
    _app, client = _app_client(tmp_path, store)
    with client:
        settings = client.get("/improve/settings").json()
    assert settings["auto_run"] is True and "token cap" in settings["auto_run_blocked"]


def test_a_job_request_inherits_the_saved_token_cap_unless_it_sets_its_own(tmp_path: Path) -> None:
    store = _LabelStore()
    store.update_global_settings("improve", {"max_total_tokens": 777})
    _app, client = _app_client(tmp_path, store)
    with client:
        assert (
            client.post("/improve/jobs", json={"request_id": "inherit"}).json()["job"]["options"]["max_total_tokens"]
            == 777
        )


def _auto_ready(monkeypatch: pytest.MonkeyPatch, store: _LabelStore, *, ready: bool = True) -> None:
    count = 8 if ready else 3
    examples = [_example(f"sess_{index % 4}", critical=index == 0) for index in range(count)]
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    store.references["turn-1"] = {"session_id": "sess_a", "turn_id": "turn-1", "trace_id": "tr-1"}
    monkeypatch.setattr(
        "fleet_rlm.optimize.evaluation.label_turn",
        lambda *_args, **_kwargs: {
            "review_status": "approved",
            "expected_answer": "1",
            "expected_values": [],
            "critical": False,
            "source_group": "sess_a",
        },
    )


@pytest.mark.parametrize("case", ["enabled", "disabled", "not_ready", "below_threshold", "active_job", "uncapped"])
def test_auto_run_starts_one_optimize_job_only_when_every_condition_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    store = _LabelStore()  # four new labels since the last run
    _auto_ready(monkeypatch, store, ready=case != "not_ready")
    store.update_global_settings(
        "improve",
        {
            "auto_run": case != "disabled",
            "min_new_labels": 5 if case == "below_threshold" else 4,
            "max_metric_calls": 12,
            "max_total_tokens": 0 if case == "uncapped" else 4000,
        },
    )
    if case == "active_job":
        store.create_optimization_run("busy-run", "busy", {}, "someone", managed=True)
        store.acquire_optimization_owner_lease("someone")  # a live owner keeps its run active
    _app, client = _app_client(tmp_path, store)
    started: list[tuple[str, ExperimentOptions, str, str]] = []

    async def fake_start(request_id, options, kind="optimize", *, trigger="manual"):
        started.append((request_id, options, kind, trigger))
        return {}

    with client:
        client.app.state.experiments.start = fake_start
        saved = client.post("/improve/labels", json={"turn_id": "turn-1"})
        assert saved.status_code == 200
        repeat = client.post("/improve/labels", json={"turn_id": "turn-1"})
        assert repeat.status_code == 200
    if case != "enabled":
        assert started == []
        return
    assert len(started) == 2 and started[0][0] == started[1][0] and started[0][0].startswith("auto-")
    _request, options, kind, trigger = started[0]
    assert (kind, trigger) == ("optimize", "auto")
    assert (options.max_metric_calls, options.max_total_tokens) == (12, 4000)


def test_auto_run_records_its_trigger_and_never_approves_or_installs(tmp_path: Path, monkeypatch) -> None:
    store = _LabelStore()
    _auto_ready(monkeypatch, store)
    store.update_global_settings("improve", {"auto_run": True, "min_new_labels": 1, "max_total_tokens": 900})
    _app, client = _app_client(tmp_path, store)

    async def fake_execute(run_id: str, _kind: str, _options: ExperimentOptions, *, approval: bool) -> None:
        assert approval is False
        store.update_optimization_run(run_id, {"status": "completed", "stage": "completed"})

    with client:
        client.app.state.experiments._execute = fake_execute
        assert client.post("/improve/labels", json={"turn_id": "turn-1"}).status_code == 200
        jobs = client.get("/improve/jobs").json()["jobs"]
    assert [(job["kind"], job["trigger"], job["status"]) for job in jobs] == [("optimize", "auto", "completed")]
    assert jobs[0].get("decision") is None and not (tmp_path / "fleet.json").exists()


def test_a_failing_auto_run_check_never_fails_the_saved_label(tmp_path: Path, monkeypatch) -> None:
    store = _LabelStore()
    _auto_ready(monkeypatch, store)
    store.update_global_settings("improve", {"auto_run": True, "max_total_tokens": 900})
    monkeypatch.setattr(store, "new_review_labels", lambda _since: (_ for _ in ()).throw(RuntimeError("db down")))
    _app, client = _app_client(tmp_path, store)
    with client:
        assert client.post("/improve/labels", json={"turn_id": "turn-1"}).status_code == 200


def test_a_legacy_per_session_candidate_stays_readable_and_rejectable_but_never_approvable(tmp_path: Path) -> None:
    store = _LabelStore()
    _completed(store, "sess_old-run", legacy_session_id="sess_old")
    _app, client = _app_client(tmp_path, store)
    with client:
        listed = client.get("/improve/jobs").json()["jobs"]
        assert listed[0]["legacy_session_id"] == "sess_old"
        refused = client.post(
            "/improve/jobs/sess_old-run/review", json={"decision": "approved", "request_id": "approve-legacy"}
        )
        assert refused.status_code == 409 and "globally" in refused.json()["detail"]
        assert store.get_optimization_run("sess_old-run")["status"] == "completed"
        rejected = client.post(
            "/improve/jobs/sess_old-run/review", json={"decision": "rejected", "request_id": "reject-legacy"}
        )
        assert rejected.status_code == 202 and rejected.json()["job"]["status"] == "rejected"


def test_budget_exhausted_is_a_terminal_worker_outcome_that_keeps_no_candidate(tmp_path: Path) -> None:
    async def scenario() -> None:
        store, manager = _manager_with_store(tmp_path)
        store.create_optimization_run("budget-run", "request", {}, manager.owner_id, managed=True)
        store.update_optimization_run(
            "budget-run", {"worker_outcome": {"status": "budget_exhausted", "candidate_version": None}}
        )
        await manager._finish("budget-run", SimpleNamespace(returncode=0), None, approval=False)
        record = store.get_optimization_run("budget-run")
        assert record["status"] == "budget_exhausted" and record["stage"] == "budget_exhausted"
        assert record["candidate_version"] is None and not manager.busy

    asyncio.run(scenario())


def test_improve_routes_replace_the_session_scoped_experiment_routes(tmp_path: Path) -> None:
    app, _client = _app_client(tmp_path, _LabelStore())
    paths = set(app.openapi()["paths"])
    assert {
        "/improve/readiness",
        "/improve/queue",
        "/improve/labels",
        "/improve/jobs",
        "/improve/jobs/{run_id}",
        "/improve/jobs/{run_id}/cancel",
        "/improve/jobs/{run_id}/review",
        "/improve/history",
        "/improve/settings",
    } <= paths
    assert not any("/experiments" in path and path.startswith("/sessions") for path in paths)


def test_readiness_lists_tunable_components_and_what_could_still_improve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    examples = [_example(f"sess_{index % 4}", critical=index == 0) for index in range(8)]
    for index, example in enumerate(examples):
        example.replay_turn_id = f"turn-{index}"
    monkeypatch.setattr("fleet_rlm.optimize.dataset.load_examples", lambda *_args: examples)
    store = _LabelStore()
    _app, client = _app_client(tmp_path, store)
    with client:
        unknown = client.get("/improve/readiness").json()
        assert unknown["improvable_examples"] is None and unknown["note"] is None
        assert unknown["min_split_examples"] == 1
        names = {item["name"] for item in unknown["components"]}
        assert {"routing", "skill_selection", "reasoning.rlm", "reasoning.flex", "child_policies"} <= names
        assert all(item["description"] for item in unknown["components"])
        _completed(
            store,
            "evaluate-replay-1",
            kind="evaluate_replay",
            example_scores={"turn-0": 1.0, "turn-1": 0.4, "turn-2": 0.0},
        )
        assert client.get("/improve/readiness").json()["improvable_examples"] == 2
        _completed(
            store,
            "evaluate-replay-2",
            kind="evaluate_replay",
            example_scores={"turn-0": 1.0, "turn-1": 1.0},
            updated_at="2999-01-01T00:00:00+00:00",
        )
        passing = client.get("/improve/readiness").json()
    assert passing["improvable_examples"] == 0
    assert "already pass" in passing["note"] and "wrong, slow, or misrouted" in passing["note"]
    assert passing["ready"] is True  # advisory only, never a blocker


def test_queue_flags_trouble_and_weak_scores_and_lists_flagged_turns_first(tmp_path: Path) -> None:
    store = _LabelStore()
    store.queue = [
        _queue_row(turn_id="calm"),
        _queue_row(turn_id="errored", sandbox_errors="2"),
        _queue_row(turn_id="rejected", submit_rejections="1"),
        _queue_row(turn_id="weak", review_status="approved", expected_answer="1"),
        _queue_row(turn_id="strong", review_status="approved", expected_answer="1"),
    ]
    _completed(store, "evaluate-replay-1", kind="evaluate_replay", example_scores={"weak": 0.5, "strong": 1.0})
    _app, client = _app_client(tmp_path, store)
    with client:
        items = client.get("/improve/queue").json()["items"]
    assert [item["turn_id"] for item in items] == ["errored", "rejected", "weak", "calm", "strong"]
    by_id = {item["turn_id"]: item for item in items}
    assert by_id["errored"]["flags"] == ["2 sandbox errors"]
    assert by_id["rejected"]["flags"] == ["SUBMIT was rejected"]
    assert (
        by_id["weak"]["flags"] == ["scored 0.50 in the latest evaluation"] and by_id["weak"]["evaluation_score"] == 0.5
    )
    assert by_id["strong"]["flags"] == [] and by_id["strong"]["evaluation_score"] == 1.0
    assert by_id["calm"]["flags"] == [] and by_id["calm"]["evaluation_score"] is None


def test_job_detail_carries_the_computed_summary_beside_the_comparison(tmp_path: Path) -> None:
    store = _LabelStore()
    _completed(store, "optimize-summarized")
    _app, client = _app_client(tmp_path, store)
    comparison = {
        "manifest": {
            "evaluation": {
                "baseline": {"dev_scores": [0.5, 0.5]},
                "candidate": {
                    "changed_components": ["routing.predict"],
                    "components_total": 8,
                    "dev_scores": [1.0, 0.5],
                },
            }
        }
    }

    async def detail(run_id: str) -> dict[str, Any]:
        return {"job": {"run_id": run_id, "status": "completed"}, "comparison": comparison}

    with client:
        client.app.state.experiments.detail = detail
        body = client.get("/improve/jobs/optimize-summarized").json()
    assert body["summary"]["splits"]["dev"]["improved"] == 1
    assert body["summary"]["headline"].startswith(
        "Changed 1 of 8: routing.predict · dev 0.50→0.75 (1 improved, 0 regressed of 2)"
    )


def test_review_queue_pages_every_captured_turn(tmp_path):
    store = _LabelStore()
    store.queue = [_queue_row(turn_id=f"turn-{index}") for index in range(53)]
    _app, client = _app_client(tmp_path, store)
    with client:
        first = client.get("/improve/queue").json()
        second = client.get("/improve/queue?offset=50").json()
        assert len(first["items"]) == 50 and first["next_offset"] == 50
        assert len(second["items"]) == 3 and second["next_offset"] is None
        assert client.get("/improve/queue?offset=-1").status_code == 422
