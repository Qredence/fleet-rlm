"""CLI orchestration contracts for development and authoritative GEPA runs.

The development smoke path is synthetic and always non-promotable. The
authoritative path is separately preflighted, requires a validated strict
Daytona proof and trusted host scorer, and persists only bounded provenance.
Both paths use the official bounded metric-call budget; neither is deployment
authority, and production optimization remains fail-closed until every gate is
sealed together.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.optimization.dataset import OptimizationDatasetError, load_export, split_records
from fleet_rlm.optimization.evidence import (
    EvidenceStore,
    StrictDaytonaPolicyBinding,
    StrictDaytonaProofError,
    ValidatedStrictDaytonaProof,
)
from fleet_rlm.optimization.metric import TrustedGEPAFeedbackMetric
from fleet_rlm.optimization.mlflow_observability import development_gepa_trace
from fleet_rlm.rlm.program import FleetRLMSignature, LMTier, build_lm_for_tier

_DEVELOPMENT_SCHEMA = "fleet.development-gepa-smoke/v1"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_DEVELOPMENT_COMPONENT = "system_prompt"
_REQUIRED_INSTRUCTION_TERMS = ("verify", "typed", "submit", "python")
# Preserves the development smoke's objective/background on the official
# prompt-template surface; must keep both official placeholders.
_DEVELOPMENT_REFLECTION_PROMPT_TEMPLATE = (
    "I provided an assistant with the following instructions to perform a task for me:\n"
    "```\n<curr_param>\n```\n\n"
    "Objective: Preserve concise, safe Fleet RLM instruction constraints.\n"
    "Background: Development-only synthetic scoring. Do not add capabilities or access external data.\n\n"
    "The following are examples of different task inputs provided to the assistant along with the "
    "assistant's response for each of them, and some feedback on how the assistant's response "
    "could be better:\n```\n<side_info>\n```\n\n"
    "Your task is to write a new instruction for the assistant.\n\n"
    "Provide the new instructions within ``` blocks."
)
_PRODUCTION_SCHEMA = "fleet.phase6-gepa-campaign/v1"
_SHA256 = set("0123456789abcdef")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


class OptimizationPreflightError(RuntimeError):
    """A safe optimization run cannot begin."""


def run_authoritative_gepa(
    *,
    student: dspy.Module,
    trainset: Sequence[Any],
    selection_set: Sequence[Any],
    held_out_set: Sequence[Any],
    metric: TrustedGEPAFeedbackMetric,
    task_lm: dspy.LM,
    reflection_lm: dspy.LM,
    task_model_id: str,
    reflection_model_id: str,
    strict_proof: ValidatedStrictDaytonaProof,
    strict_policy: StrictDaytonaPolicyBinding,
    dataset_sha256: str,
    scorer_sha256: str,
    capability_coverage_sha256: str,
    capability_coverage_verified: bool,
    seed: int,
    max_metric_calls: int,
    evidence_root: Path,
    run_id: str,
    held_out_evaluator: Any,
    fresh_process_reload: Any,
) -> dict[str, Any]:
    """Run DSPy's production GEPA contract behind the validated strict proof.

    The caller supplies a host-owned student/evaluator composition.  Candidate
    execution and judging never move into this module's receipt writer.  The
    function intentionally requires train, selection, and held-out inputs,
    uses exactly one explicit ``max_metric_calls`` budget, tracks DSPy
    ``detailed_results``, and requires a caller-provided fresh-process reload
    check before returning a campaign receipt.
    """
    _require_live()
    if not isinstance(student, dspy.Module):
        raise OptimizationPreflightError("production GEPA requires a DSPy Module student")
    if not isinstance(metric, TrustedGEPAFeedbackMetric):
        raise OptimizationPreflightError("production GEPA requires the trusted host metric")
    if not isinstance(strict_proof, ValidatedStrictDaytonaProof) or strict_proof.receipt.schema != (
        "fleet.strict-daytona-proof/v2"
    ):
        raise OptimizationPreflightError("production GEPA requires a validated block-all Daytona proof")
    if not isinstance(strict_policy, StrictDaytonaPolicyBinding):
        raise OptimizationPreflightError("production GEPA requires an explicit evaluator policy binding")
    try:
        strict_proof.require_matches(
            policy_id=strict_policy.policy_id,
            snapshot=strict_policy.snapshot,
            gateway_domains=strict_policy.gateway_domains,
            auto_stop_interval_seconds=strict_policy.auto_stop_interval_seconds,
            auto_delete_interval_seconds=strict_policy.auto_delete_interval_seconds,
            network_block_all=strict_policy.network_block_all,
        )
    except StrictDaytonaProofError as exc:
        raise OptimizationPreflightError("strict Daytona proof does not match evaluator policy") from exc
    for name, value in (
        ("task_model_id", task_model_id),
        ("reflection_model_id", reflection_model_id),
    ):
        if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
            raise OptimizationPreflightError(f"{name} is not a safe model identity")
    if task_model_id == reflection_model_id:
        raise OptimizationPreflightError("task and reflection models must be distinct")
    if not _is_sha256(dataset_sha256) or not _is_sha256(scorer_sha256):
        raise OptimizationPreflightError("dataset and scorer identities must be SHA-256 digests")
    if metric.scorer_sha256 != scorer_sha256:
        raise OptimizationPreflightError("campaign scorer identity does not match the trusted metric")
    if not _is_sha256(capability_coverage_sha256):
        raise OptimizationPreflightError("capability coverage identity must be a SHA-256 digest")
    if capability_coverage_verified is not True:
        raise OptimizationPreflightError("production GEPA requires explicit strict-evaluator capability coverage")
    if type(seed) is not int or seed < 0:
        raise OptimizationPreflightError("production GEPA seed must be a nonnegative integer")
    if (
        not isinstance(trainset, Sequence)
        or not isinstance(selection_set, Sequence)
        or not isinstance(held_out_set, Sequence)
    ):
        raise OptimizationPreflightError("production GEPA requires concrete train, selection, and held-out splits")
    if min(len(trainset), len(selection_set), len(held_out_set)) < 5:
        raise OptimizationPreflightError("production GEPA requires at least five records per split")
    expected_budget = CandidateRoundBudget().evaluator_calls(selection_records=len(selection_set))["total"]
    if type(max_metric_calls) is not int or max_metric_calls != expected_budget:
        raise OptimizationPreflightError(
            f"production GEPA max_metric_calls must equal the bounded 8+24-round budget ({expected_budget})"
        )
    if not callable(held_out_evaluator) or not callable(fresh_process_reload):
        raise OptimizationPreflightError("held-out evaluation and fresh-process reload callbacks are required")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise OptimizationPreflightError("production GEPA run_id is not a safe identifier")

    strict_policy_payload = {
        "policy_id": strict_policy.policy_id,
        "snapshot": strict_policy.snapshot,
        "gateway_domains": list(strict_policy.gateway_domains),
        "auto_stop_interval_seconds": strict_policy.auto_stop_interval_seconds,
        "auto_delete_interval_seconds": strict_policy.auto_delete_interval_seconds,
        "network_block_all": strict_policy.network_block_all,
    }

    store = EvidenceStore(evidence_root, run_id)
    manifest = {
        "schema": _PRODUCTION_SCHEMA,
        "state": "running",
        "promotion_eligible": False,
        "production_authorized": True,
        "dataset_sha256": dataset_sha256,
        "scorer_sha256": scorer_sha256,
        "capability_coverage_sha256": capability_coverage_sha256,
        "capability_coverage_verified": True,
        "strict_proof_id": strict_proof.proof_id,
        "strict_policy": strict_policy_payload,
        "task_model_id": task_model_id,
        "reflection_model_id": reflection_model_id,
        "seed": seed,
        "max_metric_calls": max_metric_calls,
        "track_stats": True,
        "detailed_results": True,
        "split_counts": {
            "train": len(trainset),
            "selection": len(selection_set),
            "held_out": len(held_out_set),
        },
    }
    store.initialize(manifest)
    try:
        optimizer = dspy.GEPA(
            metric=metric,
            auto=None,
            max_metric_calls=max_metric_calls,
            reflection_lm=reflection_lm,
            track_stats=True,
            track_best_outputs=True,
            use_merge=False,
            seed=seed,
            # GEPA logs can contain candidate outputs and reflection context.
            # Keep that transient state out of the persisted, non-content
            # evidence directory; only the bounded summary below is sealed.
            log_dir=None,
        )
        with dspy.context(lm=task_lm, track_usage=True):
            optimized = optimizer.compile(
                student,
                trainset=list(trainset),
                valset=list(selection_set),
            )
        detailed = _bounded_detailed_results(getattr(optimized, "detailed_results", None))
        instruction_sha256 = _instruction_sha256(optimized)
        reloaded_sha256 = fresh_process_reload(instruction_sha256)
        if not isinstance(reloaded_sha256, str) or reloaded_sha256 != instruction_sha256:
            raise OptimizationPreflightError("fresh-process instruction reload changed the candidate identity")
        held_out = _bounded_held_out_result(held_out_evaluator(optimized, tuple(held_out_set)))
        unsigned = {
            "schema": _PRODUCTION_SCHEMA,
            "state": "completed",
            "promotion_eligible": False,
            "production_authorized": True,
            "dataset_sha256": dataset_sha256,
            "scorer_sha256": scorer_sha256,
            "capability_coverage_sha256": capability_coverage_sha256,
            "capability_coverage_verified": True,
            "strict_proof_id": strict_proof.proof_id,
            "strict_policy": strict_policy_payload,
            "task_model_id": task_model_id,
            "reflection_model_id": reflection_model_id,
            "seed": seed,
            "max_metric_calls": max_metric_calls,
            "budget_rounds": {"exploration": 8, "continuation": 24},
            "track_stats": True,
            "detailed_results": detailed,
            "instruction_sha256": instruction_sha256,
            "fresh_process_reload_sha256": reloaded_sha256,
            "held_out": held_out,
        }
        receipt = {**unsigned, "campaign_sha256": _canonical_digest(unsigned)}
        store.write_json("production-result.json", receipt)
        return {**receipt, "evidence_dir": str(store.root)}
    except OptimizationPreflightError:
        store.write_json(
            "production-result.json",
            {"schema": _PRODUCTION_SCHEMA, "state": "failed", "promotion_eligible": False},
        )
        raise
    except Exception as exc:
        store.write_json(
            "production-result.json",
            {"schema": _PRODUCTION_SCHEMA, "state": "failed", "promotion_eligible": False},
        )
        raise OptimizationPreflightError("authoritative GEPA campaign failed") from exc


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in _SHA256 for char in value)


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bounded_detailed_results(value: Any) -> dict[str, Any]:
    """Persist GEPA statistics without candidate text, traces, or user content."""
    to_dict = getattr(value, "to_dict", None)
    if not callable(to_dict):
        raise OptimizationPreflightError("DSPy GEPA did not return detailed_results")
    raw = to_dict()
    if not isinstance(raw, Mapping):
        raise OptimizationPreflightError("DSPy GEPA detailed_results are malformed")
    scores = raw.get("val_aggregate_scores")
    if (
        not isinstance(scores, list)
        or not scores
        or len(scores) > 4096
        or any(
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 1
            for score in scores
        )
    ):
        raise OptimizationPreflightError("DSPy GEPA detailed scores are malformed")
    candidates = raw.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 4096:
        raise OptimizationPreflightError("DSPy GEPA candidate statistics are malformed")
    total_metric_calls = _bounded_optional_int(raw.get("total_metric_calls"), "total_metric_calls", 1_000_000)
    num_full_val_evals = _bounded_optional_int(raw.get("num_full_val_evals"), "num_full_val_evals", 100_000)
    best_idx = _bounded_optional_int(raw.get("best_idx"), "best_idx", len(scores) - 1)
    return {
        "candidate_count": len(candidates),
        "val_aggregate_scores": [float(score) for score in scores],
        "total_metric_calls": total_metric_calls,
        "num_full_val_evals": num_full_val_evals,
        "best_idx": best_idx,
        "details_sha256": _canonical_digest(
            {
                "val_aggregate_scores": [float(score) for score in scores],
                "total_metric_calls": total_metric_calls,
                "num_full_val_evals": num_full_val_evals,
            }
        ),
    }


def _bounded_optional_int(value: Any, field: str, upper_bound: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= upper_bound:
        raise OptimizationPreflightError(f"DSPy GEPA {field} is malformed")
    return value


def _instruction_sha256(program: Any) -> str:
    predictors = getattr(program, "named_predictors", None)
    if not callable(predictors):
        raise OptimizationPreflightError("optimized program cannot expose named instructions")
    instructions: dict[str, str] = {}
    for name, predictor in predictors():
        text = getattr(getattr(predictor, "signature", None), "instructions", None)
        if not isinstance(name, str) or not isinstance(text, str) or not text.strip():
            raise OptimizationPreflightError("optimized program contains an invalid instruction")
        instructions[name] = text
    if not instructions:
        raise OptimizationPreflightError("optimized program contains no instructions")
    return _canonical_digest(instructions)


def _bounded_held_out_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OptimizationPreflightError("held-out evaluator must return a mapping")
    required = {"complete", "quality", "p95_seconds", "cost_usd"}
    if set(value) != required or value["complete"] is not True:
        raise OptimizationPreflightError("held-out evaluation is incomplete")
    result = {"complete": True}
    for field in ("quality", "p95_seconds", "cost_usd"):
        number = value[field]
        if type(number) not in (int, float) or isinstance(number, bool) or not math.isfinite(float(number)):
            raise OptimizationPreflightError("held-out metrics must be finite numbers")
        if number < 0 or (field == "quality" and number > 1):
            raise OptimizationPreflightError("held-out metric is outside its bounded range")
        result[field] = float(number)
    return result


@dataclass(frozen=True, slots=True)
class CandidateRoundBudget:
    """Candidate-round intent translated to GEPA evaluator-call ceilings."""

    exploration_rounds: int = 8
    continuation_rounds: int = 24

    def evaluator_calls(self, *, selection_records: int) -> dict[str, int]:
        """Size GEPA's evaluator cap for full selection scoring per candidate."""
        if selection_records < 1:
            raise OptimizationPreflightError("selection split must contain at least one record")
        return {
            "exploration": self.exploration_rounds * selection_records,
            "continuation": self.continuation_rounds * selection_records,
            "total": (self.exploration_rounds + self.continuation_rounds) * selection_records,
        }


def preflight(*, export_path: Path, split_seed: int) -> dict[str, Any]:
    """Validate non-spending optimizer inputs and expose the production blocker."""
    records, split, dataset_sha256 = _load_split(export_path, split_seed)
    budget = CandidateRoundBudget().evaluator_calls(selection_records=len(split.selection))
    return {
        "schema": "fleet.safe-gepa-preflight/v1",
        "dataset_sha256": dataset_sha256,
        "records": len(records),
        "split": split.public_manifest,
        "candidate_rounds": {"exploration": 8, "continuation": 24},
        "gepa_evaluator_call_budget": budget,
        "engine": "gepa",
        "release_blocked": True,
        "blocker": "production GEPA prerequisites are not yet sealed",
    }


def initialize_preflight_evidence(*, evidence_root: Path, run_id: str, receipt: dict[str, Any]) -> Path:
    """Persist a write-once preflight receipt without enabling a live run."""
    store = EvidenceStore(evidence_root, run_id)
    store.initialize({"schema": "fleet.safe-gepa-manifest/v1", "state": "preflight", **receipt})
    store.write_json("preflight.json", receipt)
    return store.root


class _DevelopmentInstructionAdapter:
    """Deterministic development adapter for official GEPA.

    Candidate instruction text is scored in place and never executed as code.
    Evaluation makes no provider calls; GEPA reflection calls through the
    host-owned FRONTIER LM remain the only paid calls.  ``propose_new_texts``
    stays ``None`` so the official default reflective proposer (driven by the
    reflection LM) generates new candidates.
    """

    # Official GEPAAdapter surface: None keeps the default reflective proposer.
    propose_new_texts: Any = None

    def evaluate(
        self,
        batch: Sequence[Mapping[str, Any]],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> Any:
        # Lazy import: only the optimize path may load gepa; Fleet base runtime
        # and tool-registration surfaces never import it at import time.
        from gepa import EvaluationBatch

        candidate_text = candidate[_DEVELOPMENT_COMPONENT]
        outputs: list[dict[str, str]] = []
        scores: list[float] = []
        trajectories: list[dict[str, Any]] | None = [] if capture_traces else None
        for data in batch:
            score, feedback = _score_development_instruction(candidate_text)
            outputs.append({"full_assistant_response": candidate_text})
            scores.append(score)
            if trajectories is not None:
                trajectories.append(
                    {
                        "data": dict(data),
                        "full_assistant_response": candidate_text,
                        "feedback": feedback,
                    }
                )
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: Any,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        if len(components_to_update) != 1 or components_to_update[0] != _DEVELOPMENT_COMPONENT:
            raise OptimizationPreflightError("development GEPA smoke optimizes exactly one instruction component")
        candidate_text = candidate.get(_DEVELOPMENT_COMPONENT)
        if not isinstance(candidate_text, str) or not candidate_text.strip():
            raise OptimizationPreflightError("development GEPA smoke candidate is missing its instruction component")
        trajectories = eval_batch.trajectories
        if not trajectories:
            raise OptimizationPreflightError("development GEPA smoke returned no reflective trajectories")
        items = [
            {
                "Inputs": str(trajectory["data"].get("query", "")),
                "Generated Outputs": str(trajectory["full_assistant_response"]),
                "Feedback": str(trajectory["feedback"]),
            }
            for trajectory in trajectories
        ]
        return {_DEVELOPMENT_COMPONENT: items}


def run_development_smoke(
    *,
    export_path: Path,
    split_seed: int,
    max_metric_calls: int,
    evidence_root: Path,
    run_id: str,
) -> dict[str, Any]:
    """Run real GEPA only against synthetic deterministic development scoring.

    Candidate text is never executed.  The only paid calls are GEPA reflection
    calls through the host-owned FRONTIER LM.  ``max_metric_calls`` is passed
    through the official ``gepa.optimize`` contract as the bounded metric-call
    budget; Fleet accepts the official documented bounded overshoot and records
    the requested cap together with the observed official counter.
    """
    _require_live()
    if max_metric_calls < 1 or max_metric_calls > 8:
        raise OptimizationPreflightError("development smoke max_metric_calls must be between 1 and 8")
    _require_development_export(export_path)
    _records, split, dataset_sha256 = _load_split(export_path, split_seed)
    if len(split.selection) < 1 or len(split.train) < 1:
        raise OptimizationPreflightError("development smoke requires train and selection records")

    try:
        import gepa
    except ImportError as exc:
        raise OptimizationPreflightError(
            "GEPA optimization requires the optimize extra: install fleet-rlm[optimize]"
        ) from exc

    workspace_url = os.environ.get("DATABRICKS_HOST", "").strip()
    api_key = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not workspace_url or not api_key:
        raise OptimizationPreflightError("development GEPA smoke requires DATABRICKS_HOST and DATABRICKS_TOKEN")
    reflection_lm = build_lm_for_tier(
        LMTier.FRONTIER,
        workspace_url=workspace_url,
        api_key=api_key,
        max_tokens=1024,
        cache=False,
    )

    def _gepa_reflection_lm(prompt: str | list[dict[str, Any]]) -> str:
        """Adapt the host-owned DSPy LM to the official reflection callable."""
        raw_outputs = reflection_lm(_prompt_text(prompt))
        if not raw_outputs:
            raise OptimizationPreflightError("reflection model returned no outputs")
        first = raw_outputs[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
        raise OptimizationPreflightError("reflection model returned an unsupported payload")

    store = EvidenceStore(evidence_root, run_id)
    manifest = {
        "schema": _DEVELOPMENT_SCHEMA,
        "state": "running",
        "promotion_eligible": False,
        "production_authorized": False,
        "engine": "gepa",
        "budget_contract": "official-bounded-metric-calls/v1",
        "dataset_sha256": dataset_sha256,
        "split": split.public_manifest,
        "max_metric_calls": max_metric_calls,
        "evaluator": "fleet.synthetic-instruction-quality/v1",
        "candidate_execution": "disabled",
    }
    store.initialize(manifest)

    train = [record.optimizer_example() for record in split.train]
    selection = [record.optimizer_example() for record in split.selection]
    trace_metadata = {
        "schema": _DEVELOPMENT_SCHEMA,
        "run_id": run_id,
        "dataset_sha256": dataset_sha256,
        "train_records": len(train),
        "selection_records": len(selection),
        "max_metric_calls": max_metric_calls,
        "engine": "gepa",
        "environment": "development",
        "synthetic": True,
        "candidate_execution": "disabled",
        "promotion_eligible": False,
        "production_authorized": False,
    }
    with development_gepa_trace(metadata=trace_metadata) as trace:
        try:
            result = gepa.optimize(
                seed_candidate={_DEVELOPMENT_COMPONENT: FleetRLMSignature.__doc__ or ""},
                trainset=train,
                valset=selection,
                adapter=_DevelopmentInstructionAdapter(),
                reflection_lm=_gepa_reflection_lm,
                reflection_prompt_template=_DEVELOPMENT_REFLECTION_PROMPT_TEMPLATE,
                reflection_minibatch_size=1,
                max_metric_calls=max_metric_calls,
                run_dir=str(store.root / "gepa-run"),
                seed=split_seed,
                track_best_outputs=False,
                display_progress_bar=False,
            )
        except Exception as exc:
            store.write_json(
                "development-result.json",
                {"schema": _DEVELOPMENT_SCHEMA, "state": "failed", "promotion_eligible": False},
            )
            raise OptimizationPreflightError("development GEPA smoke failed") from exc

    candidate_mapping = result.best_candidate
    candidate = (
        str(candidate_mapping.get(_DEVELOPMENT_COMPONENT, ""))
        if isinstance(candidate_mapping, dict)
        else str(candidate_mapping)
    )
    if not candidate.strip():
        raise OptimizationPreflightError("GEPA returned no candidate")
    observed_metric_calls = result.total_metric_calls
    if not isinstance(observed_metric_calls, int) or observed_metric_calls < 1:
        raise OptimizationPreflightError("GEPA returned no official metric-call counter")
    receipt = {
        "schema": _DEVELOPMENT_SCHEMA,
        "state": "completed",
        "promotion_eligible": False,
        "production_authorized": False,
        "candidate_execution": "disabled",
        "budget_contract": "official-bounded-metric-calls/v1",
        "metric_call_budget": {
            "requested_max_metric_calls": max_metric_calls,
            "observed_max_metric_calls": observed_metric_calls,
            "overshoot_contract": "official documented bounded overshoot accepted",
        },
        "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "dataset_sha256": dataset_sha256,
        "split": split.public_manifest,
        "max_metric_calls": max_metric_calls,
        "best_score": _finite_score(result.val_aggregate_scores[result.best_idx]),
        "mlflow_trace_id": trace.trace_id,
    }
    store.write_json("development-result.json", receipt)
    return {**receipt, "evidence_dir": str(store.root)}


def require_live_execution_capability() -> None:
    """Fail closed for production candidate execution."""
    raise OptimizationPreflightError(
        "production GEPA execution is blocked: it requires a live strict Daytona proof "
        "for the host-polled broker boundary, trusted judges, and sealed evidence"
    )


def _require_live() -> None:
    """Require explicit operator consent before credentialed GEPA reflection."""
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise OptimizationPreflightError("FLEET_LIVE=1 is required for development GEPA smoke")


def _load_split(export_path: Path, split_seed: int) -> tuple[list[Any], Any, str]:
    try:
        document = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OptimizationPreflightError("could not read curated optimization export") from exc
    if not isinstance(document, dict):
        raise OptimizationPreflightError("curated optimization export must be an object")
    try:
        records = load_export(document)
        split = split_records(records, seed=split_seed)
    except OptimizationDatasetError as exc:
        raise OptimizationPreflightError(str(exc)) from exc
    return records, split, hashlib.sha256(export_path.read_bytes()).hexdigest()


def _require_development_export(path: Path) -> None:
    resolved = path.resolve()
    if ".fleet_rlm" in resolved.parts or "production" in resolved.parts:
        raise OptimizationPreflightError("development GEPA smoke accepts only a development synthetic export")


def _prompt_text(prompt: str | list[dict[str, Any]]) -> str:
    """Normalize the official reflection-callable prompt contract to plain text."""
    if isinstance(prompt, str):
        return prompt
    parts: list[str] = []
    for message in prompt:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    return "\n\n".join(part for part in parts if part)


def _score_development_instruction(candidate: str) -> tuple[float, str]:
    """Deterministically score whether candidate text keeps required terms."""
    normalized = candidate.lower()
    present = sum(term in normalized for term in _REQUIRED_INSTRUCTION_TERMS)
    score = present / len(_REQUIRED_INSTRUCTION_TERMS)
    missing = ", ".join(term for term in _REQUIRED_INSTRUCTION_TERMS if term not in normalized) or "none"
    feedback = f"missing_required_instruction_terms: {missing}; development_only: true"
    return score, feedback


def _finite_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise OptimizationPreflightError("GEPA returned an invalid best score") from exc
    if not 0 <= score <= 1:
        raise OptimizationPreflightError("GEPA returned an out-of-range best score")
    return score


__all__ = [
    "CandidateRoundBudget",
    "OptimizationPreflightError",
    "initialize_preflight_evidence",
    "preflight",
    "require_live_execution_capability",
    "run_authoritative_gepa",
    "run_development_smoke",
]
