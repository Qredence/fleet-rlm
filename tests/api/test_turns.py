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
from dspy import AdapterParseError
from fakes import FakeHarness, FakeManager, client_for, runtime_with, store_with

import fleet_rlm.api.app as api_app
import fleet_rlm.engine.turn as turn_module
from fleet_rlm.api.events import SSERuntimeCallback
from fleet_rlm.replay import decode_snapshot
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
                yield SimpleNamespace(set_attribute=lambda k, v: row.__setitem__(k, v))
                return
            yield SimpleNamespace(
                trace_id=seen.get("span_trace_id"),
                set_inputs=lambda value: seen.__setitem__("inputs", value),
                set_outputs=lambda value: seen.__setitem__("outputs", value),
                set_attributes=lambda _value: None,
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
    monkeypatch.setattr(
        turn_module,
        "dspy",
        SimpleNamespace(settings=SimpleNamespace(callbacks=[seen["global_callback"]]), context=context),
    )
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
        "lm_calls": 4,
        "sandbox_errors": 1,
        "submit_rejections": 2,
        "budget_breakdown": {"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
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
    client, _app = client_for(runtime)
    with client:
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
    from fleet_rlm.observability import sanitize_trace_span
    from fleet_rlm.optimize.evidence import artifact_correctness, delegation_correctness
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
    client, _app = client_for(runtime)
    with client:
        response = client.post("/sessions/session-a/turn", json={"task": "Produce reports"})
    assert response.status_code == 200
    sanitized = {}
    sanitize_trace_span(SimpleNamespace(name="fleet_turn", outputs=traced["outputs"], set_outputs=sanitized.update))
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
    client, app = client_for(runtime)
    with client:
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
    client, _ = client_for(runtime, store=store)
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
    assert traced["destinations"] == []


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
    client, _app = client_for(runtime)
    with client:
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
