"""CLI orchestration contracts for the safe GEPA-only baseline.

Production optimization remains fail-closed.  The only executable path in this
module is an opt-in development smoke run: official ``gepa`` optimization
reflects on a synthetic, deterministic evaluator and produces a non-promotable
candidate artifact.  The sole budget contract is the official bounded
metric-call budget; its documented bounded overshoot is accepted by mission
decision.  No USD reflection-cost cap exists anywhere in Fleet.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fleet_rlm.optimization.dataset import OptimizationDatasetError, load_export, split_records
from fleet_rlm.optimization.evidence import EvidenceStore
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


class OptimizationPreflightError(RuntimeError):
    """A safe optimization run cannot begin."""


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
        "blocker": "production strict Daytona evaluator policy is not yet authorized",
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
        "production GEPA execution is blocked: it requires a stable production gateway, "
        "a production strict Daytona proof, trusted judges, and sealed evidence"
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
    "run_development_smoke",
]
