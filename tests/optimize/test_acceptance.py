from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import dspy
import pytest
from mlflow.genai.scorers import Scorer

from fleet_rlm.config import FleetSettings
from fleet_rlm.optimize.acceptance import Campaign, SpendLedger, exclusive_file, save_json, validate_controls
from fleet_rlm.optimize.contracts import contract, published_suite
from fleet_rlm.optimize.evidence import (
    artifact_correctness,
    delegation_correctness,
    validate_structured,
    verification_entries,
)
from fleet_rlm.optimize.scorers import fleet_metric, fleet_scorer


def artifact(raw: bytes) -> dict:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}


def ledger(tmp_path, **changes) -> SpendLedger:
    path = tmp_path / "ledger.json"
    save_json(
        path,
        {
            "schema_version": 1,
            "prior_usd": 20,
            "rollout_usd": 0.1,
            "reflection_usd": 0.1,
            "judge_usd": 0.01,
            "fixed_remaining_usd": 2,
            "rate_basis": "test conservative estimates",
            **changes,
        },
    )
    return SpendLedger(path)


def test_unknown_search_outcome_escapes_optimizer_retry_and_keeps_cleanup(tmp_path):
    from fleet_rlm.optimize.acceptance import AcceptanceOutcomeUnknown
    from fleet_rlm.optimize.artifacts import IsolatedFleetProgram
    from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError

    campaign = Campaign(tmp_path / "campaign.json", {"version": "test"})
    calls = []
    cleanup = []

    class Engine:
        optimization_observer = staticmethod(campaign.resource)
        sandbox_mgr = SimpleNamespace(
            session_id="search-1", shutdown=lambda: cleanup.append("shutdown"), _cleanup_receipts=[]
        )

        def __call__(self, **_inputs):
            calls.append("execute")
            raise ExecutionOutcomeUnknownError("transport interrupted")

    program = IsolatedFleetProgram(dspy.Module(), lambda *_args: Engine())
    with pytest.raises(AcceptanceOutcomeUnknown, match="without replay"):
        for _attempt in range(2):
            try:
                program(task="compute")
            except Exception:
                continue
    assert calls == ["execute"]
    assert cleanup == ["shutdown"]
    saved = json.loads(campaign.path.read_text())
    assert saved["outcome_unknown"] is True
    assert saved["resources"][-1]["event"] == "rollout_completed"


@pytest.mark.parametrize(
    "kind,raw,expected,keys",
    [
        ("text", b"North 200", "North 200", []),
        ("json", b'{"north":200,"south":240}', {"north": 200, "south": 240}, []),
        (
            "csv",
            b"region,total\nsouth,240\nnorth,200\n",
            [{"region": "north", "total": 200}, {"region": "south", "total": 240}],
            ["region"],
        ),
    ],
)
def test_artifact_checks_preserve_value_associations(kind, raw, expected, keys):
    gold = {
        "schema_version": 1,
        "artifacts": [{"path": "artifacts/result", "kind": kind, "expected": expected, "key_columns": keys}],
    }
    evidence = {"artifacts": {"complete": True, "files": {"artifacts/result": artifact(raw)}}}
    assert artifact_correctness(gold, evidence)[0] == 1
    evidence["artifacts"]["files"]["artifacts/result"] = artifact(raw.replace(b"200", b"240").replace(b"240", b"999"))
    assert artifact_correctness(gold, evidence)[0] == 0


@pytest.mark.parametrize("path", ["../bad", "files/result", "artifacts/../files/a", "artifacts//a", "/artifacts/a"])
def test_structured_gold_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        validate_structured({"artifacts": [{"path": path, "kind": "text", "expected": "a"}]})


def test_python_requires_execution_receipt_matching_observed_bytes():
    gold = {"artifacts": [{"path": "artifacts/report.py", "kind": "python", "execute": True, "stdout": "42\n"}]}
    row = artifact(b"print(42)")
    evidence = {
        "artifacts": {"complete": True, "files": {"artifacts/report.py": row}},
        "python": {
            "artifacts/report.py": {"complete": True, "sha256": row["sha256"], "exit_code": 0, "stdout": "42\n"}
        },
    }
    assert artifact_correctness(gold, evidence)[0] == 1
    evidence["python"]["artifacts/report.py"]["sha256"] = "different"
    assert artifact_correctness(gold, evidence)[0] == 0
    assert verification_entries(gold) == [{"path": "artifacts/report.py", "timeout_seconds": 30}]
    assert "expected" not in verification_entries(gold)[0]


@pytest.mark.parametrize("failure", ["count", "failed", "inputs", "digest", "incomplete"])
def test_delegation_checks_host_receipts(failure):
    gold = {"children": [{"input_paths": ["files/sales.csv"], "artifacts": ["totals.json"]}]}
    evidence = {
        "children_complete": True,
        "children": [
            {
                "status": "succeeded",
                "inputs": {"files/sales.csv": "inputhash"},
                "artifacts": [{"path": "artifacts/random-id/totals.json", "sha256": "abc"}],
            }
        ],
        "artifacts": {"files": {"artifacts/random-id/totals.json": {"sha256": "abc"}}},
    }
    assert delegation_correctness(gold, evidence)[0] == 1
    if failure == "count":
        evidence["children"] *= 2
    elif failure == "failed":
        evidence["children"][0]["status"] = "failed"
    elif failure == "inputs":
        evidence["children"][0]["inputs"]["files/private.csv"] = "extra"
    elif failure == "digest":
        evidence["artifacts"]["files"]["artifacts/random-id/totals.json"]["sha256"] = "changed"
    else:
        evidence["children_complete"] = False
    assert delegation_correctness(gold, evidence)[0] == 0


def test_missing_artifact_zeroes_both_dspy_and_mlflow_quality(monkeypatch):
    from fleet_rlm.optimize import scorers

    monkeypatch.setattr(scorers, "answer_correctness", lambda *_args: (1.0, "correct"))
    gold = dspy.Example(
        task="produce report",
        answer="42",
        structured_expectations={"artifacts": [{"path": "artifacts/report.txt", "kind": "text", "expected": "42"}]},
    )
    prediction = dspy.Prediction(answer="42", lm_calls=1, sandbox_errors=0, execution_evidence={})
    result = fleet_metric(gold, prediction, judge_lm=object())
    assert result.score == 0 and result.objective_scores["artifact_correctness"] == 0
    feedback = fleet_scorer(object())(
        inputs={"task": gold.task},
        outputs=prediction.toDict(),
        expectations={"expected_answer": gold.answer, "structured_expectations": gold.structured_expectations},
    )
    assert next(row.value for row in feedback if row.name == "fleet_metric") == result.score


def test_every_published_scorer_round_trips_without_captured_lm(monkeypatch):
    # Deserialize only this repository's source in the isolated local test backend.
    monkeypatch.setenv("MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS", "true")
    from mlflow.genai.scorers import base

    # This suite imports MLflow's server for other fixtures; exercise the client path here.
    monkeypatch.setattr(base, "_is_tracking_server_process", lambda: False)
    settings = FleetSettings(_env_file=None)
    definition = contract(settings)
    assert definition["digest"] == contract(settings)["digest"]
    suite = [Scorer.model_validate(item.model_dump()) for item in published_suite(settings)]
    route = next(item for item in suite if item.name == "route_correctness")
    assert route(outputs={"route": "flex"}, expectations={"route": "flex"}).value is True
    assert route(outputs={}, expectations={"route": "flex"}).value is False
    retrieval = next(item for item in suite if item.name == "retrieval_groundedness")
    assert retrieval(trace=SimpleNamespace(search_spans=lambda: [])) is None


def test_spend_reservations_survive_reloads_and_admission_is_exclusive(tmp_path):
    budget = ledger(tmp_path)
    assert budget.forecast(10, 6)["admitted"]
    budget.admit("search", 25)
    fresh = SpendLedger(budget.path)
    fresh.admit("search", 25)
    assert fresh.total == 45
    with pytest.raises(ValueError, match="budget stop"):
        fresh.admit("more", 2)
    with exclusive_file(budget.path), pytest.raises(ValueError, match="Another process"):
        fresh.admit("parallel", 0)


def test_campaign_recovers_scoring_without_repeating_execution(tmp_path):
    budget = ledger(tmp_path)
    campaign = Campaign(tmp_path / "run" / "acceptance.json", {"configuration": "one"})
    calls = []

    def program(**_inputs):
        calls.append("execute")
        return dspy.Prediction(answer="42", lm_calls=1, sandbox_errors=0)

    def fail_score(*_args):
        raise RuntimeError("judge unavailable")

    example = dspy.Example(task="answer").with_inputs("task")
    with pytest.raises(RuntimeError, match="judge unavailable"):
        campaign.observe("one", program, example, fail_score, budget)
    recovered = Campaign(campaign.path, {"configuration": "one"}, resume=True)

    def metric(*_args):
        return dspy.Prediction(score=1.0, feedback="correct", objective_scores={"quality": 1.0})

    recovered.observe("one", program, example, metric, budget)
    recovered.observe("one", program, example, metric, budget)
    assert calls == ["execute"]
    with pytest.raises(ValueError, match="changed"):
        Campaign(campaign.path, {"configuration": "two"}, resume=True)


def test_unknown_outcome_never_replays(tmp_path):
    campaign = Campaign(tmp_path / "run" / "acceptance.json", {})
    budget = ledger(tmp_path)
    calls = []

    def unknown(**_inputs):
        calls.append(1)
        raise RuntimeError("connection lost")

    example = dspy.Example(task="run").with_inputs("task")
    with pytest.raises(RuntimeError):
        campaign.observe("one", unknown, example, None, budget)
    with pytest.raises(ValueError, match="unknown"):
        campaign.observe("one", unknown, example, None, budget)
    assert len(calls) == 1


def test_encoded_artifacts_are_redacted_before_export():
    from fleet_rlm.observability import trace_value

    receipt = artifact(b'api_key="very-secret-value"')
    cleaned = trace_value(receipt, nodes=[0])
    assert "base64" not in cleaned
    assert "very-secret-value" not in json.dumps(cleaned)
    assert "redacted" in cleaned["text"]


def test_calibration_requires_real_review_and_exact_coverage():
    with pytest.raises(ValueError, match="human approval"):
        validate_controls({"review_status": "pending", "controls": []})
    with pytest.raises(ValueError, match="twelve"):
        validate_controls({"review_status": "approved", "reviewer": "human", "controls": []})


@pytest.mark.parametrize("unbound", [False, True])
def test_registry_publication_is_idempotent_and_detects_drift(tmp_path, monkeypatch, unbound):
    import mlflow
    from mlflow.exceptions import MlflowException
    from mlflow.genai import scorers
    from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST

    from fleet_rlm.optimize import contracts

    settings = FleetSettings(
        _env_file=None, mlflow_mode="databricks", mlflow_tracking_uri="databricks", compiled_program_dir=str(tmp_path)
    )
    registry = {}
    registrations = []
    bound = []

    class Published:
        name = "fleet_metric"
        scorer_version = 1
        sample_rate = 0

        def model_dump(self):
            return {"name": self.name, "rubric": "one shared rubric"}

        def register(self, *, experiment_id):
            registry[experiment_id] = self
            registrations.append(experiment_id)
            return self

    monkeypatch.setattr(contracts, "published_suite", lambda _settings: [Published()])
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda *_args: None)

    def experiment(name):
        evaluation = name == settings.mlflow_target().eval_experiment_name
        if unbound and not evaluation and not bound:
            return SimpleNamespace(experiment_id="gepa", trace_location=None)
        return SimpleNamespace(
            experiment_id="eval" if evaluation else "gepa",
            trace_location=SimpleNamespace(
                catalog_name=settings.mlflow_trace_catalog,
                schema_name=settings.mlflow_trace_schema if evaluation else settings.mlflow_optimization_trace_schema,
                table_prefix=settings.mlflow_eval_trace_table_prefix
                if evaluation
                else settings.mlflow_optimization_trace_table_prefix,
            ),
        )

    def get_scorer(*, experiment_id, **_kwargs):
        if experiment_id not in registry:
            raise MlflowException("not found", error_code=RESOURCE_DOES_NOT_EXIST)
        return registry[experiment_id]

    monkeypatch.setattr(mlflow, "get_experiment_by_name", experiment)

    def bind(name, *, trace_location):
        bound.append("gepa")
        assert trace_location.table_prefix == settings.mlflow_optimization_trace_table_prefix
        return experiment(name)

    monkeypatch.setattr(mlflow, "set_experiment", bind)
    monkeypatch.setattr(scorers, "get_scorer", get_scorer)
    contracts.govern(settings, "register")
    contracts.govern(settings, "verify")
    contracts.govern(settings, "register")
    assert registrations == ["eval", "gepa"]
    assert bound == (["gepa"] if unbound else [])
    registry["gepa"].model_dump = lambda: {"name": "fleet_metric", "rubric": "changed"}
    with pytest.raises(ValueError, match="drift"):
        contracts.govern(settings, "verify")


def test_python_verification_runs_only_via_daytona_transport(monkeypatch):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "verify")
    commands = []
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args, **_kw: b"print(42)")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())

    def remote(_self, _sandbox, code, *, timeout):
        commands.append((code, timeout))
        return json.dumps({"complete": True, "exit_code": 0, "stdout": "42\n"})

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote)
    manager.reset_turn_budget()
    try:
        result = manager.verify_python_artifact("artifacts/result.py", 30)
    finally:
        manager.end_turn()
    assert result["complete"] and result["stdout"] == "42\n"
    assert "subprocess.Popen" in commands[0][0] and "'/workspace/artifacts/result.py'" not in commands[0][0]
    assert commands[0][1] <= 31


def test_structured_labels_change_dataset_digest_without_entering_inputs():
    from fleet_rlm.optimize.gepa import dataset_digest

    example = dspy.Example(task="compute", structured_expectations={}).with_inputs("task")
    original = dataset_digest({"train": [example]})
    example.structured_expectations = {"artifacts": [{"path": "artifacts/a.txt", "kind": "text", "expected": "42"}]}
    assert dataset_digest({"train": [example]}) != original
    assert example.inputs().toDict() == {"task": "compute"}


@pytest.mark.parametrize("session_id", ["s1", "s" * 64])
def test_paired_campaign_saves_winner_and_resume_never_reexecutes(tmp_path, monkeypatch, session_id):
    from contextlib import nullcontext

    import mlflow

    from fleet_rlm.optimize import dataset, evaluation, gepa
    from fleet_rlm.optimize.artifacts import ArtifactManifest, IsolatedFleetProgram, promotion_gate

    settings = FleetSettings(_env_file=None, compiled_program_dir=str(tmp_path))
    searches, executions = [], []

    class Program(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict("task -> answer")
            self.compiled = SimpleNamespace(digest="")

    template = Program()
    splits = {
        key: [dspy.Example(task=key, critical=key == "holdout").with_inputs("task")]
        for key in ("train", "dev", "holdout")
    }

    def factory(_settings, _store, _examples=(), observer=None):
        def create(_example, program):
            class Runtime:
                sandbox_mgr = SimpleNamespace(shutdown=lambda: None, session_id="isolated", _cleanup_receipts=[])
                optimization_observer = None

                def __call__(self, **inputs):
                    assert searches  # Neither validation nor holdout may run before search.
                    executions.append(inputs["task"])
                    return dspy.Prediction(
                        answer="42",
                        lm_calls=1,
                        sandbox_errors=0,
                        winner="WINNER" in program.predict.signature.instructions,
                    )

            runtime = Runtime()
            runtime.optimization_observer = observer
            return runtime

        return create

    class Optimizer:
        def __init__(self, **_kwargs):
            pass

        def compile(self, _baseline, *, trainset, valset):
            assert trainset is splits["train"] and valset is splits["dev"]
            searches.append(1)
            candidate = Program()
            candidate.predict.signature = candidate.predict.signature.with_instructions("WINNER")
            return SimpleNamespace(program=candidate, detailed_results=SimpleNamespace(to_dict=lambda: {"calls": 2}))

    def metric(_gold, prediction):
        return dspy.Prediction(
            score=0.95 if prediction.winner else 0.91,
            feedback="correct",
            objective_scores={"quality": 1.0, "usage_available": 1.0},
        )

    monkeypatch.setattr(gepa, "build_isolated_engine_factory", factory)
    monkeypatch.setattr(gepa, "approval_evidence", lambda *_args: {"incumbent_digest": "", "code_digest": "same"})
    monkeypatch.setattr(gepa, "changed_components", lambda *_args: ["predict"])
    monkeypatch.setattr(gepa.dspy, "GEPA", Optimizer)
    monkeypatch.setattr(dataset, "publish_reviewed_dataset", lambda *_args: {"name": "frozen", "digest": "dataset"})
    monkeypatch.setattr(mlflow, "get_experiment_by_name", lambda *_args: SimpleNamespace(experiment_id="123"))
    monkeypatch.setattr(
        mlflow, "start_run", lambda **_kw: nullcontext(SimpleNamespace(info=SimpleNamespace(run_id="run")))
    )
    monkeypatch.setattr(
        mlflow,
        "start_span",
        lambda **_kw: nullcontext(SimpleNamespace(set_inputs=lambda _value: None, set_outputs=lambda _value: None)),
    )
    monkeypatch.setattr(mlflow, "get_last_active_trace_id", lambda: "trace")
    monkeypatch.setattr(mlflow, "flush_trace_async_logging", lambda: None)
    monkeypatch.setattr(
        evaluation,
        "read_evaluation_trace",
        lambda *_args, **_kw: SimpleNamespace(data=SimpleNamespace(spans=[object()])),
    )
    for name in ("log_artifact", "log_dict"):
        monkeypatch.setattr(mlflow, name, lambda *_args, **_kw: None)
    calibration = tmp_path / "calibration.json"
    save_json(calibration, {"passed": True})
    args = SimpleNamespace(resume=None, max_metric_calls=2, calibration=calibration)
    budget = ledger(tmp_path)
    baseline = IsolatedFleetProgram(template, None)
    gepa._acceptance_compile(
        args, settings, None, session_id, settings.mlflow_target(), template, baseline, splits, metric, None, budget
    )
    manifests = list((tmp_path / "candidates").glob("*.manifest.json"))
    assert len(manifests) == 1
    manifest = ArtifactManifest(**json.loads(manifests[0].read_bytes()))
    assert promotion_gate(manifest.evaluation).passed
    assert len(executions) == 12 and searches == [1]
    assert not (tmp_path / f"{session_id}.json").exists()
    args.resume = manifest.version
    gepa._acceptance_compile(
        args,
        settings,
        None,
        session_id,
        settings.mlflow_target(),
        template,
        baseline,
        splits,
        metric,
        None,
        SpendLedger(budget.path),
    )
    assert len(executions) == 12 and searches == [1]


def test_reviewed_dataset_recovers_empty_upload_and_refuses_gold_drift(monkeypatch):
    import mlflow
    import pandas as pd

    from fleet_rlm.optimize.dataset import publish_reviewed_dataset

    class Dataset:
        dataset_id = "dataset-1"
        version = 1

        def __init__(self):
            self.rows = []

        def to_df(self):
            return pd.DataFrame(self.rows)

        def merge_records(self, rows):
            self.rows = rows

    dataset = Dataset()
    monkeypatch.setattr(mlflow.genai.datasets, "get_dataset", lambda **_kw: dataset)
    settings = FleetSettings(_env_file=None)
    splits = {
        split: [
            dspy.Example(
                task=split,
                source_session="s1",
                replay_turn_id=split,
                replay_snapshot_sha256="a" * 64,
                answer="42",
                expected_values=["42"],
                route="flex",
                critical=split == "holdout",
                source_group=split,
            )
        ]
        for split in ("train", "dev", "holdout")
    }
    first = publish_reviewed_dataset(settings, splits, "123")
    assert first == publish_reviewed_dataset(settings, splits, "123")
    assert first["records"] == 3 and first["version"] == 1
    assert ".fleet_evaluation.fleet_rlm_acceptance_" in first["name"]
    assert "expected_values" not in dataset.rows[0]["inputs"]
    dataset.rows[0]["expectations"]["expected_answer"] = "tampered"
    with pytest.raises(ValueError, match="immutable content"):
        publish_reviewed_dataset(settings, splits, "123")
