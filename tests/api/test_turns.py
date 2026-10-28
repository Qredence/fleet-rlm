"""The Turn stream: persistence order, completion, failure, replay, and trace flushing."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import AdapterParseError
from fakes import FakeHarness, FakeManager, api_settings, client_for, opened_client, runtime_with, store_with

import fleet_rlm.api.app as api_app
import fleet_rlm.api.deps as api_deps
import fleet_rlm.api.routes.turns as turns_route
import fleet_rlm.engine.turn as turn_module
import fleet_rlm.observability as observability
from fleet_rlm.api.events import SSERuntimeCallback
from fleet_rlm.replay import decode_snapshot
from fleet_rlm.sandbox.manager import CLEANUP_NOT_REQUIRED
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.store import IdempotencyConflictError
from fleet_rlm.workspace import WorkspaceDataset


def events_of(response) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("complete", [False, True])
def test_artifact_changes_distinguish_retained_outputs_and_incomplete_evidence(complete):
    before = {"artifacts/prior.txt": "same", "artifacts/report.txt": "old", "artifacts/gone.txt": "gone"}
    evidence = {
        "artifacts": {
            "complete": complete,
            "files": {
                "artifacts/prior.txt": {"sha256": "same"},
                "artifacts/report.txt": {"sha256": "new"},
                "artifacts/created.txt": {"sha256": "created"},
            },
        }
    }
    changes = turn_module._artifact_changes(before, evidence)
    assert changes["complete"] is complete
    assert {path: item["state"] for path, item in changes["files"].items()} == {
        "artifacts/prior.txt": "retained",
        "artifacts/report.txt": "modified",
        "artifacts/created.txt": "created",
        "artifacts/gone.txt": "deleted" if complete else "unknown",
    }
    assert changes["files"]["artifacts/report.txt"] == {
        "state": "modified",
        "input_sha256": "old",
        "sha256": "new",
    }


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

    import fleet_rlm.lm_usage as lm_usage

    seen["global_callback"] = object()
    fake_dspy = SimpleNamespace(settings=SimpleNamespace(callbacks=[seen["global_callback"]]), context=context)
    monkeypatch.setattr(turn_module, "dspy", fake_dspy)
    # Callback composition lives in lm_usage, so the same ambient list must be visible there.
    monkeypatch.setattr(lm_usage, "dspy", fake_dspy)
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

    def run_engine(**kwargs):
        assert "- older.md: prior hint" in kwargs["memory_summary"]
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
            notes=[{"file": "older.md", "summary": "prior hint"}],
            write_learned_note=write_note,
        ),
        harness=FakeHarness(load_context=lambda _session: ("summary", "prior history"), commit_turn=persist),
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
        "effective_seconds": 900,
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
            "complete": False,
            "files": {},
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
    evidence = {"artifacts": {"complete": True, "files": {path: {"sha256": digest}}}}
    runtime = runtime_with(
        sandbox_mgr=FakeManager(list_workspace_files=lambda: [path], read_workspace_file=lambda _path: raw),
        harness=FakeHarness(commit_turn=lambda **kwargs: records.append(kwargs["turn_record"]) or [path]),
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
                "artifacts": [{"path": final_path, "root_path": final_path, "sha256": digest}],
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
        harness=FakeHarness(commit_turn=lambda **_kwargs: paths),
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
                "notes": [],
                "environment": environment,
            },
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
        harness=FakeHarness(commit_turn=lambda **kwargs: persisted.append(kwargs) or []),
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
    assert {key: terminal[key] for key in joined} == {key: turn_record[key] for key in joined}
    span_joined = {key: traced["attributes"][key] for key in joined if key != "cleanup"}
    assert span_joined == {key: turn_record[key] for key in joined if key != "cleanup"}
    assert traced["cleanup"] == turn_record["cleanup"]
    assert traced["outputs"]["turn_id"] == terminal["turn_id"] == turn_record["turn_id"]
    assert traced["outputs"]["answer"] == terminal["answer"] == turn_record["answer"]
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
        return {"digests": {}, "notes": [], "environment": ""}

    def oversized_replay(_snapshot):
        raise ValueError("Replay snapshot exceeds the 8 MB limit")

    manager.turn_inputs = resolve
    monkeypatch.setattr(turn_module, "encode_snapshot", oversized_replay)
    runtime = runtime_with(
        sandbox_mgr=manager,
        harness=FakeHarness(store=store_with(finish_turn=lambda _session, *args: finished.append(args))),
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
        harness=FakeHarness(store=store_with(finish_turn=lambda _session, *args: finished.append(args))),
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
        harness=FakeHarness(store=store_with(finish_turn=lambda _session, *args: finished.append(args))),
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
        harness=FakeHarness(
            load_context=hydrate,
            store=store_with(finish_turn=lambda _session, *args: finished.append(args)),
        ),
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
    assert "The Turn failed" in failure["payload"]["error"]
    assert finished[0][1] == "failed"
    assert not runtime.turn_lock.locked()


def _run_turn_direct(
    runtime: Any,
    *,
    emitted: list[dict[str, Any]],
    order: list[str] | None = None,
    turn_id: str = "turn-teardown",
    timeout_seconds: int | None = None,
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
        idempotency_key=turn_id,
        mlflow_experiment_id=None,
        callback=object(),
        emit=emit,
        timeout_seconds=timeout_seconds,
    )


def _failed_turn_runtime(manager: FakeManager, stored: list[tuple[str, str, dict[str, Any]]], engine: Any):
    return runtime_with(sandbox_mgr=manager, harness=FakeHarness(store=_recording_store(stored)), engine=engine)


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

    def persist(**kwargs: Any) -> list[str]:
        records.append(kwargs["turn_record"])
        return []

    manager = FakeManager(cancel_turn=cancel_turn, end_turn=lambda: order.append("end_turn"))
    runtime = runtime_with(
        sandbox_mgr=manager,
        harness=FakeHarness(commit_turn=persist),
        engine=engine,
    )
    emitted: list[dict[str, Any]] = []
    _run_turn_direct(runtime, emitted=emitted, order=order)

    assert "cancel_turn" not in order, "a completed Turn must not stop its root sandbox"
    assert order == ["turn_complete", "end_turn", "done"]
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

    def persist(**kwargs: Any) -> list[str]:
        records.append(kwargs["turn_record"])
        return []

    runtime = runtime_with(
        sandbox_mgr=FakeManager(provider_bounds=lambda: bounds),
        harness=FakeHarness(commit_turn=persist),
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

    def persist(**kwargs: Any) -> list[str]:
        records.append(kwargs["turn_record"])
        return []

    runtime = runtime_with(
        sandbox_mgr=FakeManager(live_sandbox_id="sandbox-warm-root"),
        harness=FakeHarness(commit_turn=persist),
        engine=lambda **_kwargs: SimpleNamespace(answer="kept warm", route="direct"),
    )
    for index in range(2):
        emitted: list[dict[str, Any]] = []
        _run_turn_direct(runtime, emitted=emitted, turn_id=f"turn-warm-{index}")
        assert emitted[0]["cleanup"] == CLEANUP_NOT_REQUIRED
        assert emitted[0]["root_sandbox_id"] == "sandbox-warm-root"

    assert [record["root_sandbox_id"] for record in records] == ["sandbox-warm-root"] * 2
    assert traced["attributes"]["root_sandbox_id"] == "sandbox-warm-root"


def test_a_failed_turn_stops_the_root_before_its_terminal_event_and_done(traced) -> None:
    order: list[str] = []
    stored: list[tuple[str, str, dict[str, Any]]] = []
    manager = FakeManager(live_sandbox_id="sandbox-failed-root")

    def cancel_turn() -> None:
        order.append("cancel_turn")
        # The real manager discards the stopped handle once teardown has run.
        manager.live_sandbox_id = None

    def engine(**_kwargs: Any) -> SimpleNamespace:
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
        harness=FakeHarness(claim_turn=lambda *_args: prior),
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
        harness=FakeHarness(store=store_with(finish_turn=lambda _session, *args: finished.append(args))),
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
        assert "Activate snapshot" in message
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
        harness=FakeHarness(store=store_with(finish_turn=lambda _session, *args: finished.append(args))),
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
        harness=FakeHarness(claim_turn=lambda *_args: prior),
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
        assert outcomes[0]["answer"] == recovered.json()["result"]["answer"]
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

    with opened_client(runtime_with(harness=FakeHarness(claim_turn=conflict))) as (client, _):
        response = client.post("/sessions/session-a/turn", json={"task": "other", "idempotency_key": "k"})

    events = events_of(response)
    assert [event["event"] for event in events] == ["turn_status", "error", "done"]
    assert events[1]["payload"]["error"] == "Idempotency key already belongs to another task"


# Summaries recorded in trace tr-661be20e: seven restatements of one Pi recipe and two register-machine notes.
_TRACE_NOTE_SUMMARIES = [
    "Pi 1-indexed decimal digit n=1492252 is 5; recipe: sys.set_int_max_str_digits(0);"
    " mp.mp.dps=n+500..1000; s=mp.nstr(mp.pi",
    "Pi nth decimal digit (1-indexed, n=1492252): sys.set_int_max_str_digits(0); mp.mp.dps = n + 500; s"
    " = mp.nstr(mp.pi, n +",
    "Pi 1-indexed decimal digit at n=1492252 is 5. Robust recipe: sys.set_int_max_str_digits(0);"
    " mp.dps=n+200; s=mp.nstr(mp.p",
    "Pi nth decimal digit (1-indexed), n=1492252: call sys.set_int_max_str_digits(0); mp.dps = n + 200;"
    " s = mp.nstr(mp.pi, n",
    "Register-machine trace tasks: parse numbered instructions from the task text with a regex,"
    " normalize Unicode, then simul",
    "Register-machine trace tasks: implement the instruction semantics in a simulator (dict registers,"
    " recursive/nested condi",
    "Pi nth decimal digit (1-indexed): sys.set_int_max_str_digits(0); mp.dps=n+120;"
    " s=mp.nstr(mp.pi,n+120); digit=s[n+1] with",
    "Far-out digit of Pi at 1-indexed decimal position n=1492252: call sys.set_int_max_str_digits(0)"
    " first. Method A: mpmath",
    "Far-out digits of Pi: mpmath with mp.dps = n + 60 is sufficient to get decimal digit n; convert pi"
    " to string, drop the '",
]


def test_shared_notes_drop_near_duplicates_but_keep_distinct_topics() -> None:
    notes = [{"file": f"note-{index}.md", "summary": summary} for index, summary in enumerate(_TRACE_NOTE_SUMMARIES)]

    kept = turn_module._distinct_notes(notes)

    # Four restatements of the first Pi recipe collapse; both register-machine notes and the differently
    # phrased Pi notes survive, in their original newest-first order.
    assert [note["file"] for note in kept] == ["note-0.md", "note-4.md", "note-5.md", "note-7.md", "note-8.md"]


def test_shared_notes_are_capped() -> None:
    notes = [{"file": f"{i}.md", "summary": f"distinct topic {i} alpha{i} beta{i}"} for i in range(20)]

    assert [note["file"] for note in turn_module._distinct_notes(notes, limit=8)] == [f"{i}.md" for i in range(8)]


def test_shared_notes_header_says_hints_are_never_verification_references() -> None:
    runtime = runtime_with(sandbox_mgr=FakeManager(notes=[{"file": "a.md", "summary": "Pi digit is 5"}]))

    inputs = turn_module._prepare_inputs(runtime, "session-a", "task")

    assert "never a verification reference" in inputs["memory_summary"]
    assert "- a.md: Pi digit is 5" in inputs["memory_summary"]


def test_prepare_inputs_hashes_remotely_without_downloading_files() -> None:
    digests = {"files/a.txt": hashlib.sha256(b"a").hexdigest(), "skills/x/SKILL.md": hashlib.sha256(b"x").hexdigest()}

    def no_download(_path):
        raise AssertionError("non-replay Turns must not download workspace files")

    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {"digests": dict(digests), "notes": [], "environment": "Sandbox environment: X."},
            read_workspace_file=no_download,
        )
    )

    inputs = turn_module._prepare_inputs(runtime, "session-a", "task")

    assert inputs["context_files"] == digests
    assert inputs["manifest"] == "/workspace/files/a.txt, /workspace/skills/x/SKILL.md"
    assert "Sandbox environment: X." in inputs["memory_summary"]
    assert inputs["replay_files"] == {}


def test_prepare_inputs_replay_rejects_a_file_that_changed_after_hashing() -> None:
    runtime = runtime_with(
        sandbox_mgr=FakeManager(
            turn_inputs=lambda: {
                "digests": {"files/a.txt": hashlib.sha256(b"before").hexdigest()},
                "notes": [],
                "environment": "",
            },
            read_workspace_file=lambda _path: b"after",
        )
    )

    with pytest.raises(ValueError, match="changed"):
        turn_module._prepare_inputs(runtime, "session-a", "task", record_replay=True)


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
    assert lm_end["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert events[4]["tool"] == "lookup" and events[4]["ok"] is True
    assert events[4]["payload"]["agent_id"] == "root-agent"
    assert "found invoice" in json.dumps(events[4]["output"])
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
    assert events[1]["score"] is None and events[1]["payload"]["score"] is None
    assert events[1]["bounds"] == [None, None]
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


@pytest.mark.parametrize("value", [True, -1, 0.5, 9, 3601, "600"])
def test_turn_request_timeout_rejects_values_outside_the_strict_contract(monkeypatch, value):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a rejected request must not start a Turn")

    monkeypatch.setattr(turns_route, "run_turn", forbidden)
    runtime = runtime_with()
    client, _ = client_for(runtime)

    response = client.post("/sessions/session-a/turn", json={"task": "hi", "timeout_seconds": value})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "timeout_seconds"]
    assert "0 or between 10 and 3600" in detail[0]["msg"]
    assert not runtime.turn_lock.locked()


@pytest.mark.parametrize(
    ("configured", "configured_source", "body_timeout", "expected"),
    [
        (900, "default", "omit", ("default", 900)),
        (1200, "toml", "omit", ("configuration", 1200)),
        (1200, "toml", 300, ("request", 300)),
        (1200, "toml", 0, ("request", None)),
        (900, "default", None, ("default", 900)),
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
        harness=FakeHarness(commit_turn=lambda **kwargs: persisted.append(kwargs) or []),
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
    assert (terminal["deadline_source"], terminal["effective_seconds"]) == expected


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
            "session-a", turns_route.TurnRequest(task="hello"), request, runtime, api_settings(), executor, None
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
