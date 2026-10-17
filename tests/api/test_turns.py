"""The Turn stream: persistence order, completion, failure, replay, and trace flushing."""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError
from fakes import FakeHarness, FakeManager, client_for, runtime_with, store_with

import fleet_rlm.api.app as api_app
import fleet_rlm.engine.turn as turn_module
from fleet_rlm.api.events import SSERuntimeCallback
from fleet_rlm.replay import decode_snapshot
from fleet_rlm.store import IdempotencyConflictError
from fleet_rlm.workspace import WorkspaceDataset


def events_of(response) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def traced(monkeypatch):
    """Replace MLflow and dspy.context in the Turn module; record what the Turn reports."""
    seen: dict[str, Any] = {"updates": [], "callbacks": []}

    @contextmanager
    def span(**_kwargs):
        yield SimpleNamespace(
            trace_id=seen.get("span_trace_id"),
            set_inputs=lambda value: seen.__setitem__("inputs", value),
            set_outputs=lambda value: seen.__setitem__("outputs", value),
            set_attributes=lambda _value: None,
            set_attribute=lambda name, value: seen.__setitem__(name, value),
        )

    @contextmanager
    def context(*, callbacks, disable_history, **_extra):
        assert disable_history is True
        seen["callbacks"].extend(callbacks)
        yield

    seen["global_callback"] = object()
    monkeypatch.setattr(
        turn_module,
        "dspy",
        SimpleNamespace(settings=SimpleNamespace(callbacks=[seen["global_callback"]]), context=context),
    )
    monkeypatch.setattr(turn_module.mlflow, "start_span", span)
    monkeypatch.setattr(turn_module.mlflow, "update_current_trace", lambda **kwargs: seen["updates"].append(kwargs))
    monkeypatch.setattr(turn_module.mlflow, "set_experiment", lambda **_kwargs: None)
    monkeypatch.setattr(turn_module.mlflow, "get_last_active_trace_id", lambda: seen.get("last_trace_id"))
    return seen


@pytest.mark.parametrize("write_fails", [False, True])
def test_a_turn_persists_then_writes_its_note_and_streams_one_completion(traced, write_fails):
    files = {"files/input.txt": b"initial input", "files/rates.csv": b"rate\n3\n"}
    records: list[dict] = []
    order: list[str] = []
    written: list[tuple] = []
    traced["last_trace_id"] = "trace-test-id"

    def run_engine(**kwargs):
        assert "- older.md: prior hint" in kwargs["memory_summary"]
        files["files/input.txt"] = b"modified during execution"
        return SimpleNamespace(
            answer="processed locally",
            route="flex",
            learned_skill_note="Reuse the digit window check.",
            lm_calls=4,
            sandbox_errors=1,
        )

    def persist(**kwargs):
        records.append(kwargs["turn_record"])
        order.append("sync")
        return ["artifacts/result.csv"]

    def write_note(turn_id, note, trace_id=None):
        order.append("write")
        if write_fails:
            raise RuntimeError("shared mount is gone")
        written.append((turn_id, note, trace_id))

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            list_workspace_files=lambda: list(reversed(files)),
            read_workspace_file=files.__getitem__,
            list_learned_notes=lambda: [{"file": "older.md", "summary": "prior hint"}],
            write_learned_note=write_note,
        ),
        harness=FakeHarness(load_context=lambda _session: ("summary", "prior history"), commit_turn=persist),
        engine=run_engine,
        context=WorkspaceDataset("files/input.txt"),
    )
    client, app = client_for(runtime)
    with client:
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "summarize this file"})

    events = events_of(response)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert [event["event"] for event in events] == ["turn_status", "turn_complete"]
    assert events[0]["payload"] == {"phase": "preparation"}
    assert events[1]["payload"] == {
        "answer": "processed locally",
        "route": "flex",
        "learned_skill_note": "Reuse the digit window check.",
        "artifacts": ["artifacts/result.csv"],
        "trace_id": "trace-test-id",
    }
    assert {"metadata": {"mlflow.trace.session": "session-a"}, "request_preview": "summarize this file"} in traced[
        "updates"
    ]
    assert {"response_preview": "processed locally"} in traced["updates"]
    assert traced["global_callback"] in traced["callbacks"]
    # The root span is a self-contained optimization example and says which program served it.
    assert traced["inputs"]["session_id"] == "session-a"
    assert traced["inputs"]["workspace_manifest"] == "/workspace/files/input.txt, /workspace/files/rates.csv"
    assert traced["inputs"]["context_files"] == {
        "files/input.txt": hashlib.sha256(b"initial input").hexdigest(),
        "files/rates.csv": hashlib.sha256(b"rate\n3\n").hexdigest(),
    }
    assert traced["outputs"]["lm_calls"] == 4 and traced["outputs"]["sandbox_errors"] == 1
    assert traced["compiled_program_digest"] == ""
    assert any(isinstance(callback, SSERuntimeCallback) for callback in traced["callbacks"])
    assert records[0]["context_files"] == {
        "files/input.txt": hashlib.sha256(b"initial input").hexdigest(),
        "files/rates.csv": hashlib.sha256(b"rate\n3\n").hexdigest(),
    }
    assert records[0]["workspace_manifest"] == "/workspace/files/input.txt, /workspace/files/rates.csv"
    assert order == ["sync", "write"]
    assert written == ([] if write_fails else [(records[0]["turn_id"], "Reuse the digit window check.", None)])
    assert not runtime.turn_lock.locked()


def test_selected_replay_persists_the_exact_pre_turn_inputs(traced) -> None:
    content = b"id,value\n1,17\n"
    files = {"files/input.csv": content}
    persisted: list[dict[str, Any]] = []
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            list_workspace_files=lambda: list(files),
            read_workspace_file=lambda path: files[path],
        ),
        harness=FakeHarness(
            commit_turn=lambda **kwargs: persisted.append(kwargs) or [],
        ),
        engine=lambda **_kwargs: SimpleNamespace(
            answer="17", route="flex", learned_skill_note="", lm_calls=1, sandbox_errors=0
        ),
        context=WorkspaceDataset(
            "files/input.csv", "selected source", len(content), hashlib.sha256(content).hexdigest()
        ),
    )
    client, _app = client_for(runtime)
    with client:
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


@pytest.mark.parametrize("fail_preparation", [False, True])
def test_a_failed_turn_reports_an_error_with_its_own_trace_id(traced, fail_preparation):
    traced["span_trace_id"] = "trace-failed-id"
    traced["last_trace_id"] = "stale-previous-trace"
    finished: list[tuple] = []

    def hydrate(_session):
        if fail_preparation:
            raise RuntimeError("preparation failed")
        return "summary", "history"

    def failing_engine(**_kwargs):
        raise AdapterParseError(adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[")

    runtime = runtime_with(
        harness=FakeHarness(
            load_context=hydrate,
            store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
        ),
        engine=failing_engine,
    )
    client, app = client_for(runtime)
    with client:
        app.state.mlflow_experiment_id = "experiment-test-id"
        response = client.post("/sessions/session-a/turn", json={"task": "anything"})
    failure = next(event for event in events_of(response) if event["event"] == "error")
    if fail_preparation:
        assert "trace_id" not in failure["payload"]
    else:
        assert failure["payload"]["trace_id"] == "trace-failed-id"
    assert "The Turn failed" in failure["payload"]["error"]
    assert finished[0][1] == "failed"
    assert not runtime.turn_lock.locked()


@pytest.mark.parametrize("status", ["completed", "running", "failed"])
def test_a_replayed_turn_returns_the_original_outcome_without_running(status):
    prior = {
        "turn_id": "original-turn",
        "status": status,
        "result": {"event": "turn_complete", "answer": "Verified result", "route": "rlm"},
    }

    def never(**_kwargs):
        raise AssertionError("a replayed turn must not run the engine")

    runtime = runtime_with(
        harness=FakeHarness(
            claim_turn=lambda *_args: prior,
            turn_status=lambda turn_id: (
                {**prior, "updated_at": "2026-10-16T00:00:00"} if turn_id == "original-turn" else None
            ),
        ),
        engine=never,
    )
    client, _ = client_for(runtime)
    with client:
        response = client.post("/sessions/s1/turn", json={"task": "same task", "idempotency_key": "same-key"})
        outcomes = [event for event in events_of(response) if event["event"] in {"turn_complete", "error"}]
        assert len(outcomes) == 1 and outcomes[0]["turn_id"] == "original-turn"
        recovered = client.get("/sessions/s1/turns/original-turn")
    assert recovered.status_code == 200 and recovered.json()["status"] == status
    if status == "completed":
        assert outcomes[0]["answer"] == recovered.json()["result"]["answer"]
    else:
        assert outcomes[0]["event"] == "error"


@pytest.mark.parametrize("configured", [True, False])
def test_shutdown_flushes_pending_traces_only_when_tracing_is_configured(monkeypatch, configured):
    flushed: list[bool] = []
    monkeypatch.setattr(api_app.mlflow, "flush_trace_async_logging", lambda: flushed.append(True))
    client, app = client_for()
    with client:
        if configured:
            app.state.mlflow_experiment_id = "experiment-test-id"
        assert flushed == []
    assert flushed == ([True] if configured else [])


def test_reusing_an_idempotency_key_for_another_task_names_the_conflict() -> None:
    def conflict(*_args):
        raise IdempotencyConflictError("Idempotency key already belongs to another task")

    client, _ = client_for(runtime_with(harness=FakeHarness(claim_turn=conflict)))
    with client:
        response = client.post("/sessions/session-a/turn", json={"task": "other", "idempotency_key": "k"})

    events = events_of(response)
    assert [event["event"] for event in events] == ["turn_status", "error"]
    assert events[1]["payload"]["error"] == "Idempotency key already belongs to another task"
