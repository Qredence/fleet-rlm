"""Review, then explicitly replace only experiment 1's two broken v3 schedules.

Check mode only reads the registry and creates a local review snapshot. Apply performs
registry writes and judge calls; it never executes a Turn or edits historical feedback.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mlflow
from mlflow.entities import Feedback, Span
from mlflow.genai.scorers import ScorerSamplingConfig, registry
from mlflow.tracing.constant import SpanAttributeKey

from fleet_rlm.observability import trace_object
from fleet_rlm.optimize.scorers import (
    EXECUTION_DIAGNOSTIC_RUBRIC,
    execution_diagnostic_identity,
    execution_evidence_diagnostics,
)

EXPERIMENT = "1"
TARGET_TRACE = "tr-6935d04dc15442cfb7a7dd11597cecee"
OLD_NAMES = ("sandbox_verification_discipline", "tool_execution_integrity")
NEW_NAME = "execution_evidence_diagnostics"


def digest(value: Any) -> str:
    """Hash a canonical review definition."""

    def canonical(item: Any) -> Any:
        if isinstance(item, list):
            return [canonical(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {key: canonical(child) for key, child in item.items()}
        # MLflow serializes this set as a list whose order varies by process hash seed.
        # No other array, source text, version or schedule is normalized.
        builtin = result.get("builtin_scorer_pydantic_data")
        if isinstance(builtin, dict) and isinstance(builtin.get("required_columns"), list):
            builtin["required_columns"] = sorted(builtin["required_columns"])
        return result

    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def registry_snapshot() -> dict[str, Any]:
    """Retain every definition/version and the current schedule, without changing them."""
    scorers = {}
    for item in registry.list_scorers(experiment_id=EXPERIMENT):
        scorers[item.name] = {
            "version": item.scorer_version,
            "sample_rate": item.sample_rate,
            "filter_string": item.filter_string,
            "definition": item.model_dump(),
            "versions": [
                {"version": version, "definition": scorer.model_dump()}
                for scorer, version in sorted(
                    registry.list_scorer_versions(name=item.name, experiment_id=EXPERIMENT), key=lambda pair: pair[1]
                )
            ],
        }
    return {
        "tracking_uri": mlflow.get_tracking_uri(),
        "experiment_id": EXPERIMENT,
        "scorers": scorers,
        "replacement": execution_evidence_diagnostics.model_dump(),
        "rubric": EXECUTION_DIAGNOSTIC_RUBRIC,
        "code_digest": execution_diagnostic_identity(None, "")["fleet.diagnostic.code_digest"],
        "judge_model": os.environ.get("FLEET_JUDGE_MODEL"),
        "judge_endpoint_digest": digest(os.environ.get("OPENAI_API_BASE", "")),
    }


def require_review(review: dict[str, Any], current: dict[str, Any]) -> None:
    """Refuse stale reviews, new versions, changed filters, or unexpected registrations."""
    if digest(review) != digest(current):
        raise ValueError("Registry, destination, or local implementation changed after review")
    if NEW_NAME in current["scorers"]:
        raise ValueError("Replacement already exists; recover the audit instead of registering again")
    for name in OLD_NAMES:
        item = current["scorers"].get(name, {})
        if item.get("version") != 3 or item.get("sample_rate") != 1.0 or item.get("filter_string") not in (None, ""):
            raise ValueError(f"Expected {name} v3 at 100% with no filter")


def prove_portability(definition: dict[str, Any]) -> None:
    """Use MLflow's real serializer in a new interpreter; no model call or registry write."""
    program = (
        "import json,sys; from mlflow.genai.scorers import Scorer; "
        "s=Scorer.model_validate(json.load(sys.stdin)); assert s(trace=None) is None"
    )
    subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(definition),
        text=True,
        check=True,
        timeout=60,
        env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1", "MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true"},
        capture_output=True,
    )


def validate_saved_trace(
    scorer: Any,
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    saved_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Judge captured receipts and a receipt-free negative control; never rerun the Turn."""
    trace = mlflow.get_trace(TARGET_TRACE)
    if trace is None:
        raise ValueError("Saved trace is unavailable")
    with mlflow.start_run(experiment_id=EXPERIMENT, run_name="execution-diagnostics-reconciliation") as run:
        if on_progress is not None:
            on_progress({"evaluation_run_id": run.info.run_id, "trace_id": TARGET_TRACE})
        # Calling the scorer returns feedback locally. It does not overwrite prior assessments.
        feedback = (
            [Feedback.from_dictionary(item) for item in saved_feedback] if saved_feedback else scorer(trace=trace)
        )
        if {item.name for item in feedback or []} != set(OLD_NAMES):
            raise ValueError("Saved-trace validation did not return both diagnostics")
        if on_progress is not None:
            on_progress({"saved_feedback": [item.to_dictionary() for item in feedback]})
        control = _unsupported_execution_claim(trace)
        negative = scorer(trace=control)
        if on_progress is not None:
            on_progress(
                {
                    "negative_control": [item.to_dictionary() for item in negative or []],
                    "negative_control_kind": "unsupported_execution_claim_without_receipts",
                }
            )
        if len(negative or []) != 2 or any(item.value is not False for item in negative):
            raise ValueError("Receipt-free computation control did not fail both diagnostics")
        return {
            "trace_id": TARGET_TRACE,
            "target_agent_model_id": trace.info.trace_metadata.get("mlflow.modelId", "unversioned"),
            "evaluation_run_id": run.info.run_id,
            "saved_feedback_reused": bool(saved_feedback),
            "saved_feedback": [item.to_dictionary() for item in feedback],
            "negative_control": [item.to_dictionary() for item in negative],
        }


def _unsupported_execution_claim(trace: Any) -> Any:
    """Create an explicit false tool claim without altering the stored trace or inferring claims from a bare digit."""
    control = copy.deepcopy(trace)
    retained = []
    for span in control.data.spans:
        if span.name == "sandbox.exec":
            continue
        if span.name == "fleet_turn" and not span.parent_id:
            data = span.to_dict()
            outputs = trace_object(span.outputs)
            outputs["answer"] = (
                "I executed Python in sandbox.exec and independently verified this result; every execution succeeded. "
                "The answer is " + str(outputs.get("answer", ""))
            )
            data["attributes"][SpanAttributeKey.OUTPUTS] = json.dumps(outputs)
            span = Span.from_dict(data)
        retained.append(span)
    control.data.spans = retained
    return control


def apply_review(review: dict[str, Any], audit_path: Path) -> None:
    """Validate readback before enabling the replacement and stopping the old schedules."""
    require_review(review, registry_snapshot())
    if not os.environ.get("FLEET_JUDGE_MODEL"):
        raise ValueError("Configure the same FLEET_JUDGE_MODEL on client and owned worker first")
    if os.environ.get("MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS", "").lower() != "true":
        raise ValueError("Explicit custom-scorer support is required on client and owned server")
    prove_portability(review["replacement"])
    audit = {"review": review, "phase": "review_verified"}

    def save() -> None:
        audit_path.write_text(json.dumps(audit, indent=2) + "\n")

    # Reserve an audit before any write. A failed run must be recovered, never blindly repeated.
    with audit_path.open("x") as stream:
        json.dump(audit, stream, indent=2)
    registered = execution_evidence_diagnostics.register(experiment_id=EXPERIMENT)
    audit.update(phase="registered", native_registration_version=registered.scorer_version)
    save()
    readback = registry.get_scorer(name=NEW_NAME, experiment_id=EXPERIMENT)
    if readback.scorer_version != 1 or digest(readback.model_dump()) != digest(review["replacement"]):
        raise ValueError("Replacement version or definition readback mismatch; old schedules remain active")

    def progress(details: dict[str, Any]) -> None:
        audit.setdefault("validation_progress", {}).update(details)
        save()

    audit["validation"] = validate_saved_trace(readback, on_progress=progress)
    audit["phase"] = "validated"
    save()
    _activate_review(review, audit, audit_path)


def _activate_review(review: dict[str, Any], audit: dict[str, Any], audit_path: Path) -> None:
    def save() -> None:
        audit_path.write_text(json.dumps(audit, indent=2) + "\n")

    registered = registry.get_scorer(name=NEW_NAME, experiment_id=EXPERIMENT)
    # Recheck old schedules immediately before activation; no unrelated registration is mutated.
    current = registry_snapshot()
    del current["scorers"][NEW_NAME]
    require_review(review, current)
    registered.start(experiment_id=EXPERIMENT, sampling_config=ScorerSamplingConfig(sample_rate=1.0))
    active = registry.get_scorer(name=NEW_NAME, experiment_id=EXPERIMENT)
    if active.scorer_version != 1 or active.sample_rate != 1.0 or active.filter_string not in (None, ""):
        raise ValueError("Replacement schedule readback mismatch; old schedules remain active")
    audit["phase"] = "replacement_active"
    save()
    for name in OLD_NAMES:
        old = registry.get_scorer(name=name, experiment_id=EXPERIMENT)
        reviewed = review["scorers"][name]
        if (
            old.scorer_version != 3
            or digest(old.model_dump()) != digest(reviewed["definition"])
            or old.sample_rate != reviewed["sample_rate"]
            or old.filter_string != reviewed["filter_string"]
        ):
            raise ValueError("Old scorer changed before stop; recover the audit")
        old.stop(experiment_id=EXPERIMENT)
        if registry.get_scorer(name=name, experiment_id=EXPERIMENT).sample_rate != 0:
            raise ValueError("Old schedule stop could not be verified; recover the audit")
        audit["phase"] = f"stopped:{name}"
        save()
    audit["phase"] = "complete"
    audit["after"] = registry_snapshot()
    save()


def resume_registered_review(audit_path: Path, review: dict[str, Any]) -> None:
    """Resume an inactive registration after a reviewed scorer-code fix, without registering again."""
    audit = json.loads(audit_path.read_text())
    if audit.get("phase") != "registered":
        raise ValueError("Only an inactive registered phase can use this recovery")
    current = registry_snapshot()
    if digest(current) != digest(review):
        raise ValueError("Registry, destination, or local implementation changed after recovery review")
    original = audit["review"]
    for key in ("tracking_uri", "experiment_id", "judge_model", "judge_endpoint_digest", "replacement"):
        if current.get(key) != original.get(key):
            raise ValueError("Recovery changed the destination, provider, or registered entrypoint")
    expected_names = set(original["scorers"]) | {NEW_NAME}
    if set(current["scorers"]) != expected_names or any(
        digest(current["scorers"].get(name)) != digest(item) for name, item in original["scorers"].items()
    ):
        raise ValueError("Existing registrations changed before recovery")
    item = current["scorers"][NEW_NAME]
    if item["version"] != 1 or item["sample_rate"] not in (None, 0) or item["filter_string"] not in (None, ""):
        raise ValueError("Recovery requires the unchanged inactive v1 replacement")
    if digest(item["definition"]) != digest(original["replacement"]):
        raise ValueError("Replacement definition changed before recovery")
    prove_portability(review["replacement"])
    audit["resumption_review"] = review

    def progress(details: dict[str, Any]) -> None:
        audit.setdefault("validation_progress", {}).update(details)
        audit_path.write_text(json.dumps(audit, indent=2) + "\n")

    progress({"code_digest": review.get("code_digest"), "rubric": review.get("rubric")})
    readback = registry.get_scorer(name=NEW_NAME, experiment_id=EXPERIMENT)
    prior = audit.get("validation_progress", {}).get("saved_feedback")
    identity = {
        "fleet.diagnostic.code_digest": review.get("code_digest"),
        "fleet.diagnostic.rubric_version": review.get("rubric"),
        "fleet.diagnostic.judge_model": review.get("judge_model"),
    }
    if prior and not all(
        all(item.get("metadata", {}).get(key) == value for key, value in identity.items()) for item in prior
    ):
        prior = None
    audit["validation"] = validate_saved_trace(readback, on_progress=progress, saved_feedback=prior)
    audit["phase"] = "validated"
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")
    expected = {**review, "scorers": {name: item for name, item in review["scorers"].items() if name != NEW_NAME}}
    _activate_review(expected, audit, audit_path)


def rollback(audit_path: Path) -> None:
    """Restore old sampling only when definitions, versions and all schedules still match."""
    audit = json.loads(audit_path.read_text())
    if audit.get("phase") != "complete" or digest(registry_snapshot()) != digest(audit.get("after")):
        raise ValueError("Rollback identity conflict; inspect the audit and current registry")
    for name in OLD_NAMES:
        old = registry.get_scorer(name=name, experiment_id=EXPERIMENT)
        old.start(experiment_id=EXPERIMENT, sampling_config=ScorerSamplingConfig(sample_rate=1.0))
        if registry.get_scorer(name=name, experiment_id=EXPERIMENT).sample_rate != 1.0:
            raise ValueError("Rollback schedule readback failed")
    registry.get_scorer(name=NEW_NAME, experiment_id=EXPERIMENT).stop(experiment_id=EXPERIMENT)
    if registry.get_scorer(name=NEW_NAME, experiment_id=EXPERIMENT).sample_rate != 0:
        raise ValueError("Replacement stop could not be verified")
    audit["phase"] = "rolled_back"
    audit["rollback_after"] = registry_snapshot()
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")


def main() -> None:
    """Default to a read-only registry snapshot; apply needs an already reviewed snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="Authorize registry writes and saved-evidence judge calls")
    action.add_argument("--resume", action="store_true", help="Resume inactive v1 using a fresh recovery snapshot")
    action.add_argument("--rollback", action="store_true", help="Restore only identity-checked prior schedules")
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    if args.resume:
        if args.audit is None or args.snapshot is None:
            parser.error("--resume requires the registered --audit and a fresh reviewed --snapshot")
        resume_registered_review(args.audit, json.loads(args.snapshot.read_text()))
    elif args.rollback:
        if args.audit is None:
            parser.error("--rollback requires the completed --audit file")
        rollback(args.audit)
    elif args.snapshot is None:
        parser.error("--snapshot is required for check or apply")
    elif args.apply:
        if args.audit is None:
            parser.error("--apply requires a new --audit file")
        apply_review(json.loads(args.snapshot.read_text()), args.audit)
    else:
        with args.snapshot.open("x") as stream:
            json.dump(registry_snapshot(), stream, indent=2)
            stream.write("\n")


if __name__ == "__main__":
    main()
