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
from fakes import FakeHarness, FakeManager, client_for, runtime_with, store_with

import fleet_rlm.api.app as api_app
import fleet_rlm.api.deps as api_deps
import fleet_rlm.api.routes.turns as turns_route
import fleet_rlm.engine.turn as turn_module
from fleet_rlm.api.events import SSERuntimeCallback
from fleet_rlm.replay import decode_snapshot
from fleet_rlm.sandbox.repl import SandboxActivity
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
    assert [event["event"] for event in events_of(response)] == ["turn_status", "turn_complete"]
    assert not runtime.turn_lock.locked()
    # Turns run on their own pool, never on the loop's default executor that cancellation uses.
    assert threads and threads[0].startswith("fleet-turn")


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


@pytest.mark.usefixtures("traced")
def test_a_client_disconnect_mid_turn_cancels_it_and_releases_the_lock():
    started = threading.Event()
    stopped = threading.Event()

    def cancel_turn() -> None:
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
    assert [event["event"] for event in events] == ["turn_status", "tool_end", "turn_complete"]
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
    assert [event["event"] for event in events_of(accepted)] == ["turn_status", "turn_complete"]
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["loc"] == ["body", "record_replay"]
    assert rejected.json()["detail"][0]["type"] == "bool_type"
    assert not runtime.turn_lock.locked()


# The Turn stall watchdog: a fake sandbox activity and a controllable heartbeat clock, never real 300 s waits.


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _activity(
    clock: _Clock, *, busy: bool = False, alive: bool | None = True, checked: float | str | None = "now"
) -> SandboxActivity:
    """A sandbox snapshot whose liveness verdict was observed `checked` (default: now) on the heartbeat clock."""
    observed = None if alive is None else (clock.now if checked == "now" else checked)
    return SandboxActivity(
        busy=busy,
        executions=int(busy),
        busy_since=time.time() if busy else None,
        busy_since_monotonic=clock.now if busy else None,
        alive=alive,
        alive_checked_at=None if observed is None else time.time(),
        alive_checked_monotonic=observed,
        last_output_at=None,
        last_output_monotonic=None,
    )


def _lm_call(callback: SSERuntimeCallback, call_id: str = "lm-1", *, close: bool = True) -> None:
    callback.on_lm_start(call_id, SimpleNamespace(model="openai/m"), {})
    if close:
        callback.on_lm_end(call_id, ["answer"], None)


class _StallHarness:  # `activity` takes the heartbeat clock and returns a SandboxActivity
    """One Session whose engine plays a script on the Turn's callback, may jump the clock, then idles."""

    def __init__(self, traced: dict[str, Any], monkeypatch, activity=None) -> None:
        self.clock = _Clock()
        monkeypatch.setattr(turns_route, "_clock", self.clock)
        monkeypatch.setattr(turns_route, "_STALL_CHECK_SECONDS", 0.01)
        self.traced = traced
        self.cancelled = threading.Event()
        self.lock_held_at_exit: list[bool] = []
        probe = {} if activity is None else {"sandbox_activity": lambda **_kwargs: activity(self.clock)}
        self.manager = FakeManager(agent_id="root-agent", parent=None, cancel_turn=self.cancelled.set, **probe)
        self.scripts: list[Any] = []
        self.runtime = runtime_with(sandbox_mgr=self.manager, engine=self._engine)
        self.client, _ = client_for(self.runtime)

    def _engine(self, **_kwargs):
        script, jump = self.scripts.pop(0)
        try:
            script(self.traced["callbacks"][-1])
            self.clock.now += jump
            # Idle long enough for many watchdog checks; a stall's cancel ends the wait early.
            if self.cancelled.wait(0.5):
                raise RuntimeError("cancelled by the sandbox")
            return SimpleNamespace(answer="done", route="rlm")
        finally:
            self.lock_held_at_exit.append(self.runtime.turn_lock.locked())

    def turn(self, script, *, jump: float = 1000.0) -> list[dict[str, Any]]:
        """Run one Turn; the default jump is past the default FLEET_TURN_STALL_SECONDS (300)."""
        self.cancelled.clear()
        self.scripts.append((script, jump))

        def post():
            with self.client:
                return self.client.post("/sessions/session-a/turn", json={"task": "work"})

        response = _within(10, post)
        assert response.status_code == 200
        return events_of(response)


def _outcome(events: list[dict[str, Any]]) -> tuple[str, str | None]:
    return events[-1]["event"], events[-1].get("code")


def test_an_idle_turn_silent_past_the_stall_limit_after_lm_start_is_stopped(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=True))

    events = harness.turn(_lm_call)

    assert [event["event"] for event in events] == ["turn_status", "lm_start", "lm_end", "error"]
    assert _outcome(events) == ("error", "turn_stalled")
    assert events[-1]["payload"]["code"] == "turn_stalled"
    assert harness.cancelled.is_set()
    assert not harness.runtime.turn_lock.locked()


def test_a_lost_sandbox_stalls_the_turn_without_waiting_for_the_limit(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=False))

    events = harness.turn(_lm_call, jump=0)

    assert _outcome(events) == ("error", "turn_stalled")
    assert harness.cancelled.is_set()


def test_busy_sandbox_code_is_never_a_stall_even_when_silent_past_the_limit(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=True, alive=True))

    events = harness.turn(_lm_call)

    assert _outcome(events) == ("turn_complete", None)
    assert not harness.cancelled.is_set()


def test_the_watchdog_stays_off_until_the_first_lm_start(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=True))

    events = harness.turn(lambda _callback: None)

    assert [event["event"] for event in events] == ["turn_status", "turn_complete"]
    assert not harness.cancelled.is_set()


@pytest.mark.parametrize(
    "activity", [None, lambda clock: _activity(clock, busy=True, alive=None)], ids=["no-probe", "alive-none"]
)
def test_unknown_sandbox_activity_is_never_a_stall(traced, monkeypatch, activity):
    harness = _StallHarness(traced, monkeypatch, activity=activity)

    events = harness.turn(_lm_call)

    assert _outcome(events) == ("turn_complete", None)
    assert not harness.cancelled.is_set()


def test_a_stalled_turn_releases_the_session_lock_only_after_its_work_drains(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=True))

    first = harness.turn(_lm_call)
    second = harness.turn(lambda _callback: None)

    assert _outcome(first) == ("error", "turn_stalled")
    # The cancelled engine was still holding the lock when it exited; the stream released it afterwards,
    # so the next Turn on the same Session runs normally.
    assert harness.lock_held_at_exit[0] is True
    assert _outcome(second) == ("turn_complete", None)
    assert not harness.runtime.turn_lock.locked()


def test_an_open_lm_call_past_the_limit_is_not_a_stall_and_does_not_leak_into_the_next_turn(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=True))

    # A slow model call runs on the host, where the sandbox reports nothing; its lm_end never arrives here.
    first = harness.turn(lambda callback: _lm_call(callback, close=False))
    second = harness.turn(_lm_call)

    assert _outcome(first) == ("turn_complete", None)
    # Each Turn has its own callback, so the missing lm_end cannot keep the next Turn's watchdog off.
    assert _outcome(second) == ("error", "turn_stalled")


def test_an_open_tool_call_past_the_limit_is_not_a_stall(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=True))

    def script(callback: SSERuntimeCallback) -> None:
        _lm_call(callback)
        callback.on_tool_start("tool-1", SimpleNamespace(name="host_lookup"), {"query": "slow"})

    events = harness.turn(script)

    assert _outcome(events) == ("turn_complete", None)
    assert events[-2]["event"] == "tool_start"
    assert not harness.cancelled.is_set()


def _open_tool(callback: SSERuntimeCallback, name: str) -> None:
    _lm_call(callback)
    callback.on_tool_start("tool-1", SimpleNamespace(name=name), {"task": "slow"})


def test_an_open_tool_call_does_not_hide_a_lost_root_sandbox(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=False))

    events = harness.turn(lambda callback: _open_tool(callback, "host_lookup"), jump=0)

    assert events[-2]["event"] == "tool_start"
    assert _outcome(events) == ("error", "turn_stalled")
    assert harness.cancelled.is_set()


@pytest.mark.parametrize("spawn_tool", ["spawn_agent", "spawn_agents"])
def test_an_open_spawn_call_is_not_a_stall_even_when_the_root_sandbox_is_lost(traced, monkeypatch, spawn_tool):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=False))

    # Child agents (single or batch) work in their own sandboxes; the root's verdict says nothing about them.
    events = harness.turn(lambda callback: _open_tool(callback, spawn_tool))

    assert events[-2]["event"] == "tool_start" and events[-2]["tool"] == spawn_tool
    assert _outcome(events) == ("turn_complete", None)
    assert not harness.cancelled.is_set()


class _FailingLM(dspy.BaseLM):
    def forward(self, prompt=None, messages=None, **kwargs):  # noqa: ARG002
        raise RuntimeError("gateway unavailable")


@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # BaseLM.forward is DSPy's legacy custom-LM interface
def test_lm_and_tool_calls_that_raise_still_close_and_the_idle_turn_then_stalls(traced, monkeypatch):
    harness = _StallHarness(traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=True))

    def explode(query: str) -> str:
        """A host tool that always fails."""
        raise RuntimeError(f"lookup failed for {query}")

    def script(callback: SSERuntimeCallback) -> None:
        # The real DSPy callback path: each call's end hook fires with the exception.
        with dspy.context(callbacks=[callback]):
            with pytest.raises(RuntimeError, match="gateway unavailable"):
                _FailingLM(model="openai/failing")("hello")
            with pytest.raises(RuntimeError, match="lookup failed"):
                dspy.Tool(explode)(query="invoice 17")
        beat = callback.heartbeat()
        assert (beat.open_lm_calls, beat.open_spawn_calls, beat.open_tool_calls) == (0, 0, 0)

    events = harness.turn(script)

    assert [event["event"] for event in events] == [
        "turn_status",
        "lm_start",
        "lm_end",
        "tool_start",
        "tool_end",
        "error",
    ]
    assert [(e["ok"], e["error_type"]) for e in events if e["event"].endswith("_end")] == [
        (False, "RuntimeError"),
        (False, "RuntimeError"),
    ]
    # Both counts went back to zero, so the idle Turn past the limit is flagged.
    assert _outcome(events) == ("error", "turn_stalled")
    assert not harness.runtime.turn_lock.locked()


@pytest.mark.parametrize("checked", [999.0, None], ids=["before-turn-start", "unknown-age"])
def test_a_lost_sandbox_verdict_from_before_the_turn_is_not_a_stall(traced, monkeypatch, checked):
    # The clock starts at 1000, so 999 is a verdict left over from an earlier Turn (e.g. its cancel_turn stop).
    harness = _StallHarness(
        traced, monkeypatch, activity=lambda clock: _activity(clock, busy=False, alive=False, checked=checked)
    )

    events = harness.turn(_lm_call, jump=0)

    assert _outcome(events) == ("turn_complete", None)
    assert not harness.cancelled.is_set()
