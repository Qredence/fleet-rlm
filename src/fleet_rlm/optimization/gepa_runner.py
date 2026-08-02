"""CLI orchestration contracts for the safe GEPA-only baseline.

Production optimization remains fail-closed.  The only executable path in this
module is an opt-in development smoke run: GEPA reflects on a synthetic,
deterministic evaluator and produces a non-promotable candidate artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fleet_rlm.optimization.dataset import OptimizationDatasetError, load_export, split_records
from fleet_rlm.optimization.evidence import EvidenceStore
from fleet_rlm.optimization.mlflow_observability import development_gepa_trace
from fleet_rlm.rlm.lm_factory import LMTier, build_lm_for_tier
from fleet_rlm.rlm.signature import FleetRLMSignature

_DEVELOPMENT_SCHEMA = "fleet.development-gepa-smoke/v1"
_LIVE_VALUES = frozenset({"1", "true", "yes"})


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


def preflight(*, export_path: Path, split_seed: int, max_total_cost_usd: float | None) -> dict[str, Any]:
    """Validate non-spending optimizer inputs and expose the production blocker."""
    records, split, dataset_sha256 = _load_split(export_path, split_seed)
    _require_cost_cap(max_total_cost_usd)
    budget = CandidateRoundBudget().evaluator_calls(selection_records=len(split.selection))
    return {
        "schema": "fleet.safe-gepa-preflight/v1",
        "dataset_sha256": dataset_sha256,
        "records": len(records),
        "split": split.public_manifest,
        "candidate_rounds": {"exploration": 8, "continuation": 24},
        "gepa_evaluator_call_budget": budget,
        "max_total_cost_usd": max_total_cost_usd,
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


def run_development_smoke(
    *,
    export_path: Path,
    split_seed: int,
    max_total_cost_usd: float | None,
    max_evals: int,
    evidence_root: Path,
    run_id: str,
) -> dict[str, Any]:
    """Run real GEPA only against synthetic deterministic development scoring.

    Candidate text is never executed.  The only paid calls are GEPA reflection
    calls through the host-owned FRONTIER LM; ``max_token_cost`` is exactly the
    required hard cap because the evaluator makes no provider calls.
    """
    _require_live()
    _require_cost_cap(max_total_cost_usd)
    assert max_total_cost_usd is not None
    if max_evals < 1 or max_evals > 8:
        raise OptimizationPreflightError("development smoke max-evals must be between 1 and 8")
    _require_development_export(export_path)
    _records, split, dataset_sha256 = _load_split(export_path, split_seed)
    if len(split.selection) < 1 or len(split.train) < 1:
        raise OptimizationPreflightError("development smoke requires train and selection records")

    try:
        from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything
    except ImportError as exc:
        raise OptimizationPreflightError("GEPA optimization dependency is unavailable") from exc

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

    store = EvidenceStore(evidence_root, run_id)
    manifest = {
        "schema": _DEVELOPMENT_SCHEMA,
        "state": "running",
        "promotion_eligible": False,
        "production_authorized": False,
        "engine": "gepa",
        "dataset_sha256": dataset_sha256,
        "split": split.public_manifest,
        "max_evals": max_evals,
        "max_total_cost_usd": max_total_cost_usd,
        "evaluator": "fleet.synthetic-instruction-quality/v1",
        "candidate_execution": "disabled",
    }
    store.initialize(manifest)

    def evaluator(candidate: str, example: dict[str, Any]) -> tuple[float, dict[str, str]]:
        # Deterministic and deliberately non-executing: this only scores whether
        # an instruction retains Fleet's core safe-answering constraints.
        del example
        normalized = candidate.lower()
        required = ("verify", "typed", "submit", "python")
        present = sum(term in normalized for term in required)
        score = present / len(required)
        missing = ", ".join(term for term in required if term not in normalized) or "none"
        return score, {"missing_required_instruction_terms": missing, "development_only": "true"}

    train = [record.optimizer_example() for record in split.train]
    selection = [record.optimizer_example() for record in split.selection]
    config = OptimizeAnythingConfig(
        engine="gepa",
        name=run_id,
        max_evals=max_evals,
        max_token_cost=max_total_cost_usd,
        max_concurrency=1,
        output_dir=str(store.root / "gepa-output"),
        run_dir=str(store.root / "gepa-run"),
        sandbox=True,
        engine_config={
            "reflection": {
                "reflection_lm": reflection_lm,
                "reflection_minibatch_size": 1,
            },
            "engine": {"seed": split_seed, "max_workers": 1},
        },
    )
    trace_metadata = {
        "schema": _DEVELOPMENT_SCHEMA,
        "run_id": run_id,
        "dataset_sha256": dataset_sha256,
        "train_records": len(train),
        "selection_records": len(selection),
        "max_evals": max_evals,
        "max_total_cost_usd": float(max_total_cost_usd),
        "engine": "gepa",
        "environment": "development",
        "synthetic": True,
        "candidate_execution": "disabled",
        "promotion_eligible": False,
        "production_authorized": False,
    }
    with development_gepa_trace(metadata=trace_metadata) as trace:
        try:
            result = optimize_anything(
                seed_candidate=FleetRLMSignature.__doc__ or "",
                evaluator=evaluator,
                dataset=train,
                valset=selection,
                objective="Preserve concise, safe Fleet RLM instruction constraints.",
                background="Development-only synthetic scoring. Do not add capabilities or access external data.",
                config=config,
            )
        except Exception as exc:
            store.write_json(
                "development-result.json",
                {"schema": _DEVELOPMENT_SCHEMA, "state": "failed", "promotion_eligible": False},
            )
            raise OptimizationPreflightError("development GEPA smoke failed") from exc

    candidate = str(getattr(result, "best_candidate", ""))
    if not candidate.strip():
        raise OptimizationPreflightError("GEPA returned no candidate")
    receipt = {
        "schema": _DEVELOPMENT_SCHEMA,
        "state": "completed",
        "promotion_eligible": False,
        "production_authorized": False,
        "candidate_execution": "disabled",
        "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "dataset_sha256": dataset_sha256,
        "split": split.public_manifest,
        "max_evals": max_evals,
        "max_total_cost_usd": max_total_cost_usd,
        "best_score": _finite_score(getattr(result, "best_score", None)),
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


def _require_cost_cap(value: float | None) -> None:
    if value is None or value <= 0:
        raise OptimizationPreflightError("--max-total-cost-usd must be a positive explicit cap")


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
