"""Scorer portability and registry compare-before-write tests, with no live services."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.optimize.scorers import execution_evidence_diagnostics

SPEC = importlib.util.spec_from_file_location(
    "reconcile_mlflow_scorers", Path(__file__).parents[2] / "scripts/reconcile_mlflow_scorers.py"
)
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)


def test_registered_source_runs_in_fresh_process_without_closure_or_provider(tmp_path):
    definition = tmp_path / "definition.json"
    definition.write_text(json.dumps(execution_evidence_diagnostics.model_dump()))
    program = """
import json,sys
from types import SimpleNamespace as NS
from mlflow.genai.scorers import Scorer
import fleet_rlm.optimize.scorers as module
from mlflow.entities import SpanType
calls = []
def judge(**kwargs):
    calls.append(kwargs)
    return NS(value=dict(verification_applicable=True, verification_pass=True, integrity_pass=True),
        rationale="Verification and tool integrity: printed result span_id=1111111111111111.")
module.make_execution_evidence_judge = lambda model: judge
root = NS(name="fleet_turn", parent_id=None, inputs={"task":"Compute 17 * 23"}, outputs={"answer":"391"})
receipt = NS(name="sandbox.exec", parent_id="root", span_id="1111111111111111", span_type=SpanType.TOOL,
    inputs={"code":"print(17 * 23)"}, outputs={"stdout":"391"},
    attributes={"outcome":"completed"}, status=NS(status_code=NS(name="OK")))
trace = NS(search_spans=lambda: [root, receipt])
scorer = Scorer.model_validate(json.load(open(sys.argv[1])))
feedback = scorer(trace=trace)
assert len(calls) == 1 and len(feedback) == 2
assert {f.name for f in feedback} == {"sandbox_verification_discipline", "tool_execution_integrity"}
assert all(f.value is True and f.metadata["fleet.diagnostic.registration_version"] == "1" for f in feedback)
"""
    subprocess.run(
        [sys.executable, "-c", program, str(definition)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true",
            "FLEET_JUDGE_MODEL": "openai:/offline-judge",
        },
    )


def snapshot():
    return {
        "scorers": {
            name: {"version": 3, "sample_rate": 1.0, "filter_string": None, "definition": {"source": "old"}}
            for name in reconcile.OLD_NAMES
        },
        "experiment_id": "1",
        "tracking_uri": "offline",
        "replacement": {"source": "new"},
    }


def test_only_native_required_column_set_order_is_canonicalized():
    left = {
        "definition": {"builtin_scorer_pydantic_data": {"required_columns": ["inputs", "outputs"]}},
        "ordered": ["first", "second"],
    }
    right = copy.deepcopy(left)
    right["definition"]["builtin_scorer_pydantic_data"]["required_columns"].reverse()
    assert reconcile.digest(left) == reconcile.digest(right)
    assert left["definition"]["builtin_scorer_pydantic_data"]["required_columns"] == ["inputs", "outputs"]
    right["ordered"].reverse()
    assert reconcile.digest(left) != reconcile.digest(right)


@pytest.mark.parametrize(
    "change", ["version", "sample_rate", "filter_string", "definition", "tracking_uri", "replacement"]
)
def test_reconciliation_refuses_changed_review_before_any_write(change):
    review = snapshot()
    current = copy.deepcopy(review)
    if change in ("tracking_uri", "replacement"):
        current[change] = "changed"
    else:
        current["scorers"][reconcile.OLD_NAMES[0]][change] = "changed"
    with pytest.raises(ValueError, match="changed after review"):
        reconcile.require_review(review, current)


def test_reconciliation_requires_exact_v3_sampling_and_no_existing_combined_registration():
    review = snapshot()
    reconcile.require_review(review, review)
    for name, field, value in [
        (reconcile.OLD_NAMES[0], "version", 4),
        (reconcile.OLD_NAMES[1], "sample_rate", 0.5),
        (reconcile.OLD_NAMES[0], "filter_string", "tags.route = 'flex'"),
    ]:
        invalid = copy.deepcopy(review)
        invalid["scorers"][name][field] = value
        with pytest.raises(ValueError, match="Expected"):
            reconcile.require_review(invalid, invalid)
    review["scorers"][reconcile.NEW_NAME] = {}
    with pytest.raises(ValueError, match="already exists"):
        reconcile.require_review(review, review)


def test_apply_conflict_does_not_register_or_create_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: {})
    monkeypatch.setattr(
        reconcile, "execution_evidence_diagnostics", SimpleNamespace(register=lambda **_kw: pytest.fail("write"))
    )
    audit = tmp_path / "audit.json"
    with pytest.raises(ValueError, match="changed after review"):
        reconcile.apply_review(snapshot(), audit)
    assert not audit.exists()


def test_rollback_refuses_changed_registry(monkeypatch, tmp_path):
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"phase": "complete", "after": snapshot()}))
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: {})
    with pytest.raises(ValueError, match="Rollback identity conflict"):
        reconcile.rollback(audit)


@pytest.mark.parametrize("validation_fails", [False, True])
def test_reconciliation_orders_validation_readback_activation_and_stops_only_targets(
    monkeypatch, tmp_path, validation_fails
):
    review = snapshot()
    review["scorers"]["unrelated"] = {
        "version": 9,
        "definition": {"source": "unrelated"},
        "sample_rate": 0.25,
        "filter_string": "route = 'direct'",
    }
    state = copy.deepcopy(review)
    operations = []

    class Registered:
        def __init__(self, name):
            self.name = name

        @property
        def scorer_version(self):
            return state["scorers"][self.name]["version"]

        @property
        def sample_rate(self):
            return state["scorers"][self.name]["sample_rate"]

        @property
        def filter_string(self):
            return state["scorers"][self.name]["filter_string"]

        def model_dump(self):
            return state["scorers"][self.name]["definition"]

        def start(self, **_kw):
            operations.append("start:" + self.name)
            state["scorers"][self.name]["sample_rate"] = 1.0

        def stop(self, **_kw):
            operations.append("stop:" + self.name)
            state["scorers"][self.name]["sample_rate"] = 0.0

    def register(**_kw):
        operations.append("register")
        state["scorers"][reconcile.NEW_NAME] = {
            "version": 1,
            "sample_rate": None,
            "filter_string": None,
            "definition": review["replacement"],
        }
        return Registered(reconcile.NEW_NAME)

    def validate(_scorer, **_kwargs):
        operations.append("validate")
        if validation_fails:
            raise ValueError("negative control failed")
        return {"evaluation_run_id": "eval-1"}

    monkeypatch.setenv("FLEET_JUDGE_MODEL", "openai:/offline")
    monkeypatch.setenv("MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS", "true")
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: copy.deepcopy(state))
    monkeypatch.setattr(reconcile, "prove_portability", lambda _definition: operations.append("portability"))
    monkeypatch.setattr(reconcile, "validate_saved_trace", validate)
    monkeypatch.setattr(reconcile, "execution_evidence_diagnostics", SimpleNamespace(register=register))
    monkeypatch.setattr(reconcile.registry, "get_scorer", lambda *, name, **_kw: Registered(name))
    audit = tmp_path / "audit.json"
    if validation_fails:
        with pytest.raises(ValueError, match="negative control failed"):
            reconcile.apply_review(review, audit)
        assert not any(op.startswith(("start:", "stop:")) for op in operations)
        assert json.loads(audit.read_text())["phase"] == "registered"
    else:
        reconcile.apply_review(review, audit)
        assert operations == [
            "portability",
            "register",
            "validate",
            "start:" + reconcile.NEW_NAME,
            *("stop:" + name for name in reconcile.OLD_NAMES),
        ]
        reconcile.rollback(audit)
        assert json.loads(audit.read_text())["phase"] == "rolled_back"
    assert state["scorers"]["unrelated"] == review["scorers"]["unrelated"]


def test_owned_mlflow_explicitly_enables_custom_scorers_and_reused_server_is_untouched(tmp_path):
    from fleet_rlm.launcher import _mlflow_process

    process = _mlflow_process(5000, str(tmp_path))
    assert process.env == {"MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true"}


@pytest.mark.parametrize("conflict", [None, "sampling", "entrypoint", "provider", "review"])
def test_resume_checks_existing_inactive_version_without_registering_again(monkeypatch, tmp_path, conflict):
    original = snapshot()
    review = copy.deepcopy(original)
    review.update(code_digest="fixed-code", rubric="execution-evidence-v2")
    review["scorers"][reconcile.NEW_NAME] = {
        "version": 1,
        "sample_rate": None,
        "filter_string": None,
        "definition": original["replacement"],
    }
    if conflict == "sampling":
        review["scorers"][reconcile.OLD_NAMES[0]]["sample_rate"] = 0.5
    elif conflict == "entrypoint":
        review["replacement"] = {"source": "unexpected"}
    elif conflict == "provider":
        review["judge_model"] = "another-judge"
    current = {} if conflict == "review" else copy.deepcopy(review)
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"phase": "registered", "review": original, "native_registration_version": 1}))
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: current)
    monkeypatch.setattr(reconcile, "prove_portability", lambda _definition: None)
    monkeypatch.setattr(reconcile.registry, "get_scorer", lambda **_kwargs: object())
    monkeypatch.setattr(
        reconcile,
        "execution_evidence_diagnostics",
        SimpleNamespace(register=lambda **_kw: pytest.fail("duplicate registration")),
    )

    def validate(_scorer, *, on_progress, saved_feedback=None):
        assert saved_feedback is None
        on_progress({"evaluation_run_id": "resume-run"})
        return {"evaluation_run_id": "resume-run"}

    activated = []
    monkeypatch.setattr(reconcile, "validate_saved_trace", validate)
    monkeypatch.setattr(reconcile, "_activate_review", lambda reviewed, *_args: activated.append(reviewed))
    if conflict:
        with pytest.raises(ValueError):
            reconcile.resume_registered_review(audit, review)
        assert not activated
    else:
        reconcile.resume_registered_review(audit, review)
        saved = json.loads(audit.read_text())
        assert saved["review"] == original and saved["resumption_review"] == review
        assert saved["validation_progress"]["evaluation_run_id"] == "resume-run"
        assert reconcile.NEW_NAME not in activated[0]["scorers"]


def test_validation_persists_run_and_completed_feedback_before_a_negative_control_failure(monkeypatch):
    from contextlib import nullcontext

    from mlflow.entities import Feedback

    trace = SimpleNamespace(info=SimpleNamespace(trace_metadata={}), data=SimpleNamespace(spans=[]))
    monkeypatch.setattr(reconcile.mlflow, "get_trace", lambda _trace_id: trace)
    monkeypatch.setattr(
        reconcile.mlflow,
        "start_run",
        lambda **_kw: nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="run-before-call"))),
    )
    progress = []

    def score(**_kwargs):
        assert progress[0]["evaluation_run_id"] == "run-before-call"
        if len(progress) > 1:
            raise ValueError("negative control failed")
        return [Feedback(name=name, value=True, rationale="receipt citation") for name in reconcile.OLD_NAMES]

    with pytest.raises(ValueError, match="negative control failed"):
        reconcile.validate_saved_trace(score, on_progress=progress.append)
    assert len(progress[1]["saved_feedback"]) == 2


def test_negative_control_adds_a_false_claim_without_altering_the_original():
    from mlflow.entities import Span
    from mlflow.tracing.constant import SpanAttributeKey

    def span(name, span_id, parent):
        return Span.from_dict(
            {
                "trace_id": "tr-" + "a" * 32,
                "span_id": span_id,
                "parent_span_id": parent,
                "name": name,
                "start_time_unix_nano": "1",
                "end_time_unix_nano": "2",
                "status": {"code": "STATUS_CODE_OK"},
                "attributes": {
                    SpanAttributeKey.REQUEST_ID: "tr-" + "a" * 32,
                    SpanAttributeKey.OUTPUTS: json.dumps({"answer": "5"}),
                },
            }
        )

    root = span("fleet_turn", "1" * 16, "")
    execution = span("sandbox.exec", "2" * 16, "1" * 16)
    trace = SimpleNamespace(data=SimpleNamespace(spans=[root, execution]))
    control = reconcile._unsupported_execution_claim(trace)
    assert len(control.data.spans) == 1
    assert "executed Python" in control.data.spans[0].outputs["answer"]
    assert root.outputs["answer"] == "5" and len(trace.data.spans) == 2


def test_retirement_refuses_review_drift_before_judge_or_write(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    review = snapshot()
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review))
    changed = copy.deepcopy(review)
    changed["tracking_uri"] = "changed"
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: changed)
    with pytest.raises(ValueError, match="review changed"):
        reconcile.retire_legacy(path, tmp_path / "audit.json")
    assert not (tmp_path / "audit.json").exists()


def test_retirement_validates_then_stops_only_legacy_and_recovers(monkeypatch, tmp_path):
    from contextlib import nullcontext

    from fleet_rlm.optimize import scorers

    monkeypatch.chdir(tmp_path)

    state = snapshot()
    state["judge_model"] = "openai:/offline"
    state["scorers"][reconcile.NEW_NAME] = {"version": 1, "sample_rate": None}
    operations = []
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: copy.deepcopy(state))
    monkeypatch.setattr(reconcile.mlflow, "get_trace", lambda trace_id: trace_id)
    monkeypatch.setattr(
        reconcile.mlflow,
        "start_run",
        lambda **_kw: nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="evaluation"))),
    )
    monkeypatch.setattr(scorers, "make_execution_evidence_judge", lambda _model: object())
    monkeypatch.setattr(reconcile, "_unsupported_execution_claim", lambda _trace: "negative")

    def diagnose(trace, *_args, **_kwargs):
        operations.append(trace)
        return [
            SimpleNamespace(name=name, value=trace != "negative", to_dictionary=lambda: {"recorded": True})
            for name in reconcile.OLD_NAMES
        ]

    monkeypatch.setattr(scorers, "diagnose_execution_evidence", diagnose)

    def get_scorer(*, name, **_kwargs):
        def stop(**_kw):
            assert "negative" in operations
            operations.append(name)
            state["scorers"][name]["sample_rate"] = 0.0
            if state["scorers"][name].get("filter_string") == "":
                state["scorers"][name]["filter_string"] = None

        return SimpleNamespace(
            stop=stop,
            sample_rate=state["scorers"][name]["sample_rate"],
            filter_string=state["scorers"][name].get("filter_string"),
        )

    monkeypatch.setattr(reconcile.registry, "get_scorer", get_scorer)
    for name in reconcile.OLD_NAMES:
        state["scorers"][name]["filter_string"] = ""
    initial = copy.deepcopy(state)
    path, audit = tmp_path / "review.json", tmp_path / "audit.json"
    path.write_text(json.dumps(state))
    reconcile.retire_legacy(path, audit)
    assert operations[-2:] == list(reconcile.OLD_NAMES)
    assert all(state["scorers"][name]["filter_string"] is None for name in reconcile.OLD_NAMES)
    assert state["scorers"][reconcile.NEW_NAME]["sample_rate"] is None
    saved = json.loads(audit.read_text())
    assert saved["phase"] == "retired"
    before = list(operations)
    path.write_text(json.dumps(state))
    reconcile.retire_legacy(path, audit)
    assert operations == before

    state["scorers"][reconcile.OLD_NAMES[0]]["sample_rate"] = 0.0
    state["scorers"][reconcile.OLD_NAMES[0]]["filter_string"] = None
    state["scorers"][reconcile.OLD_NAMES[1]]["sample_rate"] = 1.0
    state["scorers"][reconcile.OLD_NAMES[1]]["filter_string"] = ""
    partial_review, partial_audit = tmp_path / "partial_review.json", tmp_path / "partial_audit.json"
    partial_review.write_text(json.dumps(state))
    partial_audit.write_text(
        json.dumps(
            {
                "operation": "retire_legacy",
                "review": initial,
                "stopped": [],
                "pending_stop": reconcile.OLD_NAMES[0],
                "validations": saved["validations"],
            }
        )
    )
    reconcile.retire_legacy(partial_review, partial_audit)
    assert operations == [*before, reconcile.OLD_NAMES[1]]
    recovered = json.loads(partial_audit.read_text())
    assert recovered["phase"] == "retired" and recovered["stopped"] == list(reconcile.OLD_NAMES)
    assert "pending_stop" not in recovered


def test_operator_paths_reject_working_directory_escape(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    review = tmp_path / "review.json"
    review.write_text("{}")
    with pytest.raises(ValueError, match="working directory"):
        reconcile.retire_legacy(Path("../review.json"), tmp_path / "audit.json")
    with pytest.raises(ValueError, match="working directory"):
        reconcile.filter_online_schedules(review, Path("../audit.json"))
    assert not (tmp_path / "audit.json").exists()


def test_filter_online_schedules_updates_only_active_targets_and_recovers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    state = snapshot()
    for name, version in reconcile.ONLINE_SCHEDULE_VERSIONS.items():
        state["scorers"][name] = {
            "version": version,
            "sample_rate": 1.0,
            "filter_string": None,
            "definition": {"source": name},
        }
    operations = []
    monkeypatch.setattr(reconcile, "registry_snapshot", lambda: copy.deepcopy(state))

    def get_scorer(*, name, **_kwargs):
        def update(*, sampling_config, **_kw):
            operations.append((name, sampling_config.sample_rate, sampling_config.filter_string))
            state["scorers"][name]["filter_string"] = sampling_config.filter_string

        return SimpleNamespace(
            update=update,
            scorer_version=state["scorers"][name]["version"],
            sample_rate=state["scorers"][name]["sample_rate"],
            filter_string=state["scorers"][name]["filter_string"],
        )

    monkeypatch.setattr(reconcile.registry, "get_scorer", get_scorer)
    initial = copy.deepcopy(state)
    review_path, audit_path = tmp_path / "filter_review.json", tmp_path / "filter_audit.json"
    review_path.write_text(json.dumps(state))
    reconcile.filter_online_schedules(review_path, audit_path)
    assert [name for name, _, _ in operations] == list(reconcile.ONLINE_SCHEDULE_VERSIONS)
    assert all(
        state["scorers"][name]["filter_string"] == reconcile.ONLINE_TRACE_FILTER
        for name in reconcile.ONLINE_SCHEDULE_VERSIONS
    )
    saved = json.loads(audit_path.read_text())
    assert saved["phase"] == "filtered" and saved["filtered"] == list(reconcile.ONLINE_SCHEDULE_VERSIONS)

    # Recovery after a partial update of the first schedule
    names = list(reconcile.ONLINE_SCHEDULE_VERSIONS)
    for name in names[1:]:
        state["scorers"][name]["filter_string"] = None
    partial_review, partial_audit = tmp_path / "partial_filter_review.json", tmp_path / "partial_filter_audit.json"
    partial_review.write_text(json.dumps(state))
    partial_audit.write_text(
        json.dumps(
            {
                "operation": "filter_online",
                "review": initial,
                "filtered": [],
                "pending_filter": names[0],
            }
        )
    )
    operations.clear()
    reconcile.filter_online_schedules(partial_review, partial_audit)
    assert [name for name, _, _ in operations] == names[1:]
    recovered = json.loads(partial_audit.read_text())
    assert recovered["phase"] == "filtered" and recovered["filtered"] == names
    assert "pending_filter" not in recovered
