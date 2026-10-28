"""Structured gold checks, host execution evidence, and the metric that scores them."""

from __future__ import annotations

import base64
import hashlib
import json

import dspy
import pytest

from fleet_rlm.execution_evidence import validate_structured, verification_entries
from fleet_rlm.optimize.objectives import artifact_correctness, delegation_correctness
from fleet_rlm.optimize.scorers import fleet_metric, fleet_scorer, route_correctness


def artifact(raw: bytes) -> dict:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}


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


def test_route_scorer_fails_when_a_gold_route_has_no_observed_route():
    assert route_correctness(outputs={"route": "flex"}, expectations={"route": "flex"}).value is True
    assert route_correctness(outputs={}, expectations={"route": "flex"}).value is False


def test_encoded_artifacts_are_redacted_before_export():
    from fleet_rlm.observability import trace_value

    receipt = artifact(b'api_key="very-secret-value"')
    cleaned = trace_value(receipt, nodes=[0])
    assert "base64" not in cleaned
    assert "very-secret-value" not in json.dumps(cleaned)
    assert "redacted" in cleaned["text"]


def test_python_verification_runs_only_via_daytona_transport(monkeypatch):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "verify")
    commands = []
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args, **_kw: b"print(42)")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())

    def remote(_self, _sandbox, code, *, timeout, retry):
        assert retry is False
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
    from fleet_rlm.optimize.dataset import dataset_digest

    example = dspy.Example(task="compute", structured_expectations={}).with_inputs("task")
    original = dataset_digest({"train": [example]})
    example.structured_expectations = {"artifacts": [{"path": "artifacts/a.txt", "kind": "text", "expected": "42"}]}
    assert dataset_digest({"train": [example]}) != original
    assert example.inputs().toDict() == {"task": "compute"}
