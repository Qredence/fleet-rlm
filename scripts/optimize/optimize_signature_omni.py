"""Optimize the Fleet RLM signature with GEPA ``optimize_anything`` (omni composition).

Optimizes the ``FleetRLMSignature`` instruction text against the UC-managed
evaluation dataset (Wave 2) scored by the shared Fleet judges (Wave 4
alignment included when ``--scorer-source registry``). ``dspy==3.3.0`` pins
``gepa[dspy]==0.1.1``, so the native omni engine registry is unavailable: this
script composes ``optimize_anything`` runs instead — parallel explore variants
with distinct seeds, best-of selection by validation aggregate, then a fresh
continue run (the omni shape from the GEPA 0.1.2+ engine API).

Runs execute candidate Turns in-process (trusted-host only) and require
``FLEET_LIVE=1``. The optimized candidate is written to disk for human review;
nothing is auto-applied to the Fleet runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.benchmarks.judges import DEFAULT_JUDGE_MODEL, JUDGE_NAMES, build_judges
from scripts.benchmarks.manage_prompts import DEFAULT_PROMPT_NAME, register_prompt_text
from scripts.benchmarks.rlm_eval_dataset import _dataset_name_default, dataset_examples

RECEIPT_SCHEMA = "fleet.signature-optimization/v1"
DEFAULT_MLFLOW_URL = "databricks"


def _experiment_name_default() -> str:
    return os.environ.get("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-rlm")


DEFAULT_DATASET_NAME = "fleet-rlm-quality-v2"
MAX_CANDIDATE_CHARS = 20_000
DEFAULT_OBJECTIVE = (
    "Maximize correctness and evidence coverage of Fleet RLM answers on the "
    "evaluation dataset while keeping the REPL-first execution discipline."
)
DEFAULT_BACKGROUND = (
    "Candidates are instruction texts for dspy.RLM (a recursive REPL code agent, "
    "never a retrieval module). Live Turns run root/sub models over the Databricks "
    "AI Gateway inside a Daytona sandbox; the optimizer uses the WORKER tier for "
    "evaluation Turns and the FRONTIER tier for reflection."
)
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_AI_GATEWAY_PATH = "/ai-gateway/openai/v1"

_PROGRESS_STREAM = "off"


def _progress_event(event: str, **fields: Any) -> None:
    """Emit one bounded ndjson progress line when the ndjson stream is enabled."""
    if _PROGRESS_STREAM != "ndjson":
        return
    try:
        record = {"ts": time.time(), "event": event, **fields}
        print(json.dumps(record, default=str, separators=(",", ":")), flush=True)
    except Exception:
        return


class OptimizationError(RuntimeError):
    """An optimization precondition, candidate contract, or budget failed."""


def _require_live() -> None:
    """
    Enforce the explicit live opt-in for credentialed optimization runs.

    Raises:
        OptimizationError: If ``FLEET_LIVE`` is not enabled.
    """
    if os.environ.get("FLEET_LIVE", "").lower() not in _LIVE_VALUES:
        raise OptimizationError("FLEET_LIVE=1 is required for signature optimization")


def _credentials(args: argparse.Namespace) -> tuple[str, str]:
    """
    Resolve the Databricks workspace URL and token for tier LM construction.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        tuple[str, str]: ``(workspace_url, api_key)``.

    Raises:
        OptimizationError: If required environment values are missing.
    """
    gateway_base = args.gateway_base_url or os.environ.get("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "")
    if not gateway_base:
        raise OptimizationError("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL (or --gateway-base-url) is required")
    workspace_url = args.workspace_url or gateway_base.removesuffix(_AI_GATEWAY_PATH)
    api_key = os.environ.get("DATABRICKS_TOKEN", "")
    if not api_key:
        raise OptimizationError("DATABRICKS_TOKEN is required")
    return workspace_url, api_key


def build_candidate_signature(candidate: str) -> type:
    """
    Build a fresh dspy Signature subclass whose instructions are the candidate text.

    Parameters:
        candidate (str): Proposed instruction text replacing the
            ``FleetRLMSignature`` docstring.

    Returns:
        type: A ``FleetRLMSignature`` subclass with the candidate docstring.

    Raises:
        OptimizationError: If the candidate is empty or exceeds the character bound.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        raise OptimizationError("candidate instructions must be non-empty text")
    if len(candidate) > MAX_CANDIDATE_CHARS:
        raise OptimizationError(f"candidate exceeds the {MAX_CANDIDATE_CHARS}-character bound ({len(candidate)} chars)")
    from fleet_rlm.rlm.signature import FleetRLMSignature

    return type("CandidateFleetRLMSignature", (FleetRLMSignature,), {"__doc__": candidate})


class InProcessTurnExecutor:
    """Run candidate evaluation Turns through the in-process interpreter backend.

    Trusted hosts only: candidate REPL code executes locally. Shared models and
    options are built once; each example gets a fresh candidate RLM, fresh
    interpreter, and fresh session context.
    """

    def __init__(self, *, models: Any, options: Any) -> None:
        self._models = models
        self._options = options

    @classmethod
    def from_role_settings(cls, args: argparse.Namespace) -> InProcessTurnExecutor:
        """Build the executor from the Databricks AI Gateway tier configuration."""
        from fleet_rlm.rlm.dspy_contract import RLMOptions
        from fleet_rlm.rlm.lm_factory import LMTier, build_lm_for_tier
        from fleet_rlm.rlm.model_bundle import RLMModelBundle

        workspace_url, api_key = _credentials(args)
        root = build_lm_for_tier(
            LMTier.WORKER,
            workspace_url=workspace_url,
            api_key=api_key,
            max_tokens=args.worker_max_tokens,
        )
        sub = build_lm_for_tier(
            LMTier.FAST,
            workspace_url=workspace_url,
            api_key=api_key,
            max_tokens=args.worker_max_tokens,
        )
        options = RLMOptions(
            max_iters=args.max_iters,
            max_llm_calls=args.max_llm_calls,
            max_output_chars=args.max_output_chars,
        )
        return cls(models=RLMModelBundle(root, sub), options=options)

    def run(self, candidate: str, query: str) -> dict[str, Any]:
        """
        Execute one candidate Turn for a dataset query.

        Parameters:
            candidate (str): Candidate instruction text under evaluation.
            query (str): Dataset example query.

        Returns:
            dict[str, Any]: Answer text plus bounded execution metadata
            (iterations, termination mode) for optimizer side information.
        """
        import dspy

        from fleet_rlm.chat.session_context import SessionContextManifest
        from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
        from fleet_rlm.rlm.dspy_contract import build_native_rlm, prediction_result
        from fleet_rlm.rlm.inputs import build_rlm_input_kwargs

        signature = build_candidate_signature(candidate)
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        try:
            rlm = build_native_rlm(
                signature=signature,
                options=self._options,
                sub_lm=self._models.sub_lm,
                verbose=False,
            )
            context = SessionContextManifest(session_id=uuid4(), checkpoint_version=0, message_count=0, recent=())
            kwargs = build_rlm_input_kwargs(request=query, session_context=context)
            started = time.perf_counter()
            with dspy.context(lm=self._models.root_lm, adapter=dspy.JSONAdapter(), track_usage=True):
                prediction = rlm(interpreter, **kwargs)
            result = prediction_result(
                prediction,
                signature,
                schema_id="fleet.signature-optimization",
                schema_version="1",
                max_output_chars=self._options.max_output_chars,
            )
            trajectory = getattr(prediction, "trajectory", ())
            mode = (
                "native_extraction_fallback"
                if getattr(prediction, "final_reasoning", None) == "Extract forced final output"
                else "typed_submit"
            )
            return {
                "answer": result.display_text,
                "iterations": len(trajectory) if isinstance(trajectory, list) else 0,
                "termination_mode": mode,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        finally:
            interpreter.shutdown()


def _judge_value(feedback: Any) -> bool:
    value = getattr(feedback, "value", feedback)
    return bool(value)


def _rationale(feedback: Any) -> str:
    rationale = getattr(feedback, "rationale", None)
    return rationale if isinstance(rationale, str) else ""


def score_example(
    judges: Mapping[str, Any], *, query: str, answer: str, expectations: Mapping[str, Any]
) -> tuple[float, dict[str, Any]]:
    """
    Score one candidate answer with the shared Fleet judges.

    Parameters:
        judges (Mapping[str, Any]): Judge callables keyed by registered name.
        query (str): Dataset example query.
        answer (str): Candidate Turn answer text.
        expectations (Mapping[str, Any]): Dataset example expectations.

    Returns:
        tuple[float, dict[str, Any]]: Mean boolean judge score in [0, 1] plus
        GEPA side information (per-judge results and bounded rationales).
    """
    per_judge: dict[str, bool] = {}
    rationales: list[str] = []
    for name, judge in judges.items():
        feedback = judge(inputs={"query": query}, outputs=answer, expectations=dict(expectations))
        per_judge[name] = _judge_value(feedback)
        rationale = _rationale(feedback)
        if rationale:
            rationales.append(f"{name}: {rationale[:500]}")
    score = sum(per_judge.values()) / max(1, len(per_judge))
    side_info: dict[str, Any] = {
        "Feedback": " | ".join(rationales) if rationales else "no judge rationale",
        **per_judge,
    }
    return score, side_info


def make_evaluator(executor: Any, judges: Mapping[str, Any], *, cost_penalty_per_iteration: float = 0.0) -> Any:
    """
    Build the GEPA per-example evaluator over candidate instruction texts.

    Parameters:
        executor (Any): Turn executor with ``run(candidate, query)``.
        judges (Mapping[str, Any]): Judge callables keyed by registered name.
        cost_penalty_per_iteration (float): Penalty applied per REPL iteration
            so cheap Turns dominate quality ties (0 disables the Pareto shaping).

    Returns:
        Any: ``evaluator(candidate, example) -> (score, side_info)`` that is
        fail-soft: executor failures score 0 with a bounded failure category as
        actionable side information.
    """

    def evaluator(candidate: Any, example: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
        candidate_text = candidate if isinstance(candidate, str) else json.dumps(candidate)
        try:
            outcome = executor.run(candidate_text, str(example["query"]))
        except Exception as exc:
            from fleet_rlm.observability.failure_diagnostics import trace_failure_category

            return 0.0, {"Feedback": "executor_failed", "failure_category": trace_failure_category(exc)}
        try:
            score, side_info = score_example(
                judges,
                query=str(example["query"]),
                answer=str(outcome["answer"]),
                expectations=example.get("expectations") or {},
            )
        except Exception as exc:
            from fleet_rlm.observability.failure_diagnostics import trace_failure_category

            return 0.0, {"Feedback": "judge_failed", "failure_category": trace_failure_category(exc)}
        penalty = outcome["iterations"] * cost_penalty_per_iteration
        side_info["base_score"] = score
        side_info["iteration_penalty"] = penalty
        side_info["iterations"] = outcome["iterations"]
        side_info["termination_mode"] = outcome["termination_mode"]
        return score - penalty, side_info

    return evaluator


def reflection_lm(args: argparse.Namespace) -> Any:
    """
    Build the FRONTIER-tier reflection LM as a plain GEPA LanguageModel callable.

    Parameters:
        args (argparse.Namespace): Connection and model options.

    Returns:
        Any: ``(prompt: str | list[dict]) -> str`` backed by a stock ``dspy.LM``.
    """
    from fleet_rlm.rlm.lm_factory import LMTier, build_lm_for_tier

    workspace_url, api_key = _credentials(args)
    frontier = build_lm_for_tier(
        LMTier.FRONTIER,
        workspace_url=workspace_url,
        api_key=api_key,
        max_tokens=args.reflection_max_tokens,
    )

    def lm(prompt: Any) -> str:
        response = frontier(prompt=prompt) if isinstance(prompt, str) else frontier(messages=prompt)
        if isinstance(response, str):
            return response
        try:
            return str(response[0])
        except (TypeError, IndexError, KeyError):
            return str(response)

    return lm


def _aggregate_score(result: Any) -> float:
    best_score = getattr(result, "best_score", None)
    if isinstance(best_score, (int, float)):
        return float(best_score)
    aggregate = getattr(result, "val_aggregate_subscores", None)
    if isinstance(aggregate, Mapping) and aggregate:
        values = [float(value) for value in aggregate.values() if isinstance(value, (int, float))]
        if values:
            return sum(values) / len(values)
    return float("-inf")


def _envelope_result_dict(payload: Any) -> dict[str, Any]:
    """Pick the final ``{"type": "result"}`` object from a claude JSON envelope."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        for item in reversed(payload):
            if isinstance(item, dict) and item.get("type") == "result":
                return item
        for item in reversed(payload):
            if isinstance(item, dict):
                return item
    return {}


def _apply_claude_json_envelope_shim() -> None:
    """Accept claude CLI >=2.1 JSON-array envelopes in the upstream gepa engines.

    Upstream ``gepa@0310bb7`` parses ``claude --output-format json`` stdout as
    exactly one JSON object; claude CLI 2.1.220 emits a JSON array of envelope
    entries instead and breaks ``_parse_proposer_result`` /
    ``_extract_claude_cost`` with ``AttributeError: 'list' object has no
    attribute 'get'``. In-place compatibility shim until the fix lands
    upstream.
    """
    from gepa.oa.engines import autoresearch as _autoresearch
    from gepa.oa.engines import meta_harness as _meta_harness

    original_parse = _meta_harness._parse_proposer_result

    def _parse_proposer_result(stdout: str) -> tuple[float, dict[str, Any]]:
        stripped = (stdout or "").strip()
        if stripped.startswith("["):
            try:
                payload = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                payload = []
            result = _envelope_result_dict(payload)
            try:
                cost = float(result.get("total_cost_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                cost = 0.0
            return cost, result
        return original_parse(stdout)

    original_extract = _autoresearch._extract_claude_cost

    def _extract_claude_cost(stdout: str) -> float:
        stripped = (stdout or "").strip()
        if stripped.startswith("["):
            try:
                payload = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return 0.0
            result = _envelope_result_dict(payload)
            try:
                return float(result.get("total_cost_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0
        return original_extract(stdout)

    _meta_harness._parse_proposer_result = _parse_proposer_result
    _autoresearch._extract_claude_cost = _extract_claude_cost


def native_omni_available() -> bool:
    """Return whether the native omni engine registry (``gepa.oa``) is importable."""
    try:
        import gepa.oa  # noqa: F401
    except ImportError:
        return False
    return True


def run_native_omni(
    seed_candidate: str,
    *,
    evaluator: Any,
    train: list[dict[str, Any]],
    val: list[dict[str, Any]],
    objective: str,
    background: str,
    reflection: Any,
    engines: Sequence[str],
    explore_evals: int,
    continue_evals: int,
    agent_model: str,
    agent_effort: str,
) -> dict[str, Any]:
    """
    Run the true omni pipeline: optimize_best_of explore across engines, fresh continue.

    Explore runs every requested engine on an equal ``explore_evals`` slice of
    the budget in parallel and keeps the best candidate; a fresh ``gepa``
    instance then continues from it. Agent-engine failures degrade to smaller
    engine sets (the omni contract measures engines, not engine availability).

    Parameters:
        seed_candidate (str): Starting instruction text.
        evaluator (Any): GEPA per-example evaluator.
        train (list[dict[str, Any]]): Training examples.
        val (list[dict[str, Any]]): Validation examples.
        objective (str): Optimization objective text.
        background (str): Optimization background text.
        reflection (Any): Reflection LanguageModel callable for the gepa engine.
        engines (Sequence[str]): Requested engines
            (subset/permutation of ``gepa``, ``meta_harness``, ``autoresearch``).
        explore_evals (int): Evaluation budget per explore engine.
        continue_evals (int): Evaluation budget for the continue phase.
        agent_model (str): Claude model id for agent proposers.
        agent_effort (str): Claude effort flag for agent proposers.

    Returns:
        dict[str, Any]: Engine/result evidence plus the continued best candidate.

    Raises:
        OptimizationError: If budgets are invalid or every engine set failed.
    """
    if explore_evals < 1 or continue_evals < 1:
        raise OptimizationError("explore/continue eval budgets must be positive")
    _apply_claude_json_envelope_shim()
    from gepa.optimize_anything import OptimizeAnythingConfig, optimize_anything, optimize_best_of

    def _config(engine: str, max_evals: int) -> Any:
        engine_config: dict[str, Any] = (
            {"reflection": {"reflection_lm": reflection}}
            if engine == "gepa"
            else {"model": agent_model, "effort": agent_effort}
        )
        return OptimizeAnythingConfig(
            engine=engine,
            max_evals=max_evals,
            sandbox=True,
            engine_config=engine_config,
            run_dir=str(
                _REPO_ROOT / ".scratch" / "optimization" / "engine-runs" / f"native-{engine}-{int(time.time())}"
            ),
        )

    attempts = [tuple(engines)]
    if len(engines) > 1:
        attempts.append(("gepa", "meta_harness"))
        attempts.append(("gepa",))
    explore = None
    engines_used: tuple[str, ...] = ()
    failures: list[str] = []
    _progress_event("engine_set_start", engines=list(engines))
    for attempt in attempts:
        try:
            explore = optimize_best_of(
                seed_candidate,
                evaluator=evaluator,
                configs=[_config(engine, explore_evals) for engine in attempt],
                dataset=train,
                valset=val or None,
                objective=objective,
                background=background,
                max_workers=len(attempt),
            )
            engines_used = attempt
            break
        except Exception as exc:
            failures.append(f"{list(attempt)}: {type(exc).__name__}")
            _progress_event("engine_set_failed", engines=list(attempt), error_category=type(exc).__name__)
    if explore is None:
        raise OptimizationError(f"all native engine sets failed: {'; '.join(failures)}")
    _progress_event(
        "engine_set_done",
        engines=list(engines_used),
        score=getattr(explore, "best_score", None),
        evals=getattr(explore, "total_evals", None),
    )
    _progress_event("continue_start")

    continued = optimize_anything(
        explore.best_candidate,
        evaluator=evaluator,
        dataset=train,
        valset=val or None,
        objective=objective,
        background=background,
        config=_config("gepa", continue_evals),
    )
    explore_best_score = getattr(explore, "best_score", None)
    continued_best_score = getattr(continued, "best_score", None)
    regressed = (
        isinstance(explore_best_score, (int, float))
        and isinstance(continued_best_score, (int, float))
        and continued_best_score < explore_best_score
    )
    _progress_event(
        "continue_done",
        score=continued_best_score,
        evals=getattr(continued, "total_evals", None),
    )
    best_candidate = explore.best_candidate if regressed else continued.best_candidate
    _progress_event(
        "best",
        phase="explore" if regressed else "continue",
        score=max(
            (score for score in (explore_best_score, continued_best_score) if isinstance(score, (int, float))),
            default=None,
        ),
        best_candidate_sha256=hashlib.sha256(best_candidate.encode("utf-8")).hexdigest(),
    )
    metadata = getattr(explore, "metadata", {}) or {}
    return {
        "engine_mode": "native-omni",
        "engines_used": list(engines_used),
        "engine_failures": failures,
        "explore_best_score": explore_best_score,
        "explore_total_evals": getattr(explore, "total_evals", None),
        "explore_metadata": {str(key): str(value)[:200] for key, value in metadata.items()},
        "continued_best_score": continued_best_score,
        "continued_total_evals": getattr(continued, "total_evals", None),
        "best_phase": "explore" if regressed else "continue",
        "objective_pareto_size": None,
        "best_candidate": best_candidate,
    }


def run_omni(
    seed_candidate: str,
    *,
    evaluator: Any,
    train: list[dict[str, Any]],
    val: list[dict[str, Any]],
    objective: str,
    background: str,
    reflection: Any,
    explore_variants: int,
    explore_metric_calls: int,
    continue_metric_calls: int,
) -> dict[str, Any]:
    """
    Run the omni composition: parallel explore, best-of, fresh continue.

    Parameters:
        seed_candidate (str): Starting instruction text.
        evaluator (Any): GEPA per-example evaluator.
        train (list[dict[str, Any]]): Training examples.
        val (list[dict[str, Any]]): Validation examples for best-of selection.
        objective (str): Optimization objective text.
        background (str): Optimization background text.
        reflection (Any): Reflection LanguageModel callable.
        explore_variants (int): Number of parallel explore runs (distinct seeds).
        explore_metric_calls (int): Metric-call budget per explore variant.
        continue_metric_calls (int): Metric-call budget for the continue run.

    Returns:
        dict[str, Any]: Explore results, selected best, and the continued result.

    Raises:
        OptimizationError: If budgets are invalid or every explore run failed.
    """
    if explore_variants < 1 or explore_metric_calls < 1 or continue_metric_calls < 1:
        raise OptimizationError("explore_variants and metric-call budgets must be positive")
    from gepa.optimize_anything import EngineConfig, GEPAConfig, ReflectionConfig, optimize_anything

    def _config(metric_calls: int, seed: int) -> Any:
        run_dir = _REPO_ROOT / ".scratch" / "optimization" / "engine-runs" / f"fallback-{seed}-{int(time.time())}"
        return GEPAConfig(
            engine=EngineConfig(
                max_metric_calls=metric_calls,
                seed=seed,
                display_progress_bar=False,
                run_dir=str(run_dir),
            ),
            reflection=ReflectionConfig(reflection_lm=reflection),
        )

    explore_results: list[Any] = []
    failures: list[str] = []
    _progress_event("engine_set_start", engines=["gepa"], variants=explore_variants)
    for variant in range(explore_variants):
        _progress_event("explore_variant_start", variant=variant)
        try:
            explore_results.append(
                optimize_anything(
                    seed_candidate,
                    evaluator=evaluator,
                    dataset=train,
                    valset=val or None,
                    objective=objective,
                    background=background,
                    config=_config(explore_metric_calls, variant),
                )
            )
            _progress_event("explore_variant_done", variant=variant, score=_aggregate_score(explore_results[-1]))
        except Exception as exc:
            failures.append(f"explore[{variant}]: {type(exc).__name__}")
            _progress_event("explore_variant_failed", variant=variant, error_category=type(exc).__name__)
    if not explore_results:
        raise OptimizationError(f"all explore variants failed: {'; '.join(failures)}")
    _progress_event("continue_start")

    best = max(explore_results, key=_aggregate_score)
    continued = optimize_anything(
        best.best_candidate,
        evaluator=evaluator,
        dataset=train,
        valset=val or None,
        objective=objective,
        background=background,
        config=_config(continue_metric_calls, explore_variants),
    )
    selected_score = _aggregate_score(best)
    continued_score = _aggregate_score(continued)
    regressed = (
        selected_score != float("-inf") and continued_score != float("-inf") and continued_score < selected_score
    )
    _progress_event("continue_done", score=continued_score, evals=getattr(continued, "total_metric_calls", None))
    _progress_event(
        "best",
        phase="explore" if regressed else "continue",
        score=max(selected_score, continued_score),
    )
    front = getattr(continued, "objective_pareto_front", None)
    return {
        "engine_mode": "gepa-fallback-composition",
        "explore": [
            {"aggregate_score": _aggregate_score(result), "metric_calls": getattr(result, "total_metric_calls", None)}
            for result in explore_results
        ],
        "explore_failures": failures,
        "selected_index": explore_results.index(best),
        "selected_score": selected_score,
        "continued_score": continued_score,
        "continued_metric_calls": getattr(continued, "total_metric_calls", None),
        "best_phase": "explore" if regressed else "continue",
        "objective_pareto_size": len(front) if isinstance(front, (list, tuple)) else None,
        "best_candidate": best.best_candidate if regressed else continued.best_candidate,
    }


def _load_examples(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if args.dataset_json is not None:
        try:
            records = json.loads(args.dataset_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OptimizationError(f"could not read dataset records: {args.dataset_json}") from exc
        if not isinstance(records, list) or not all("inputs" in record for record in records):
            raise OptimizationError("dataset-json must be a list of {'inputs': {...}, 'expectations': {...}} records")
        return dataset_examples(records, val_fraction=args.val_fraction, seed=args.dataset_seed)
    from mlflow.genai import datasets

    try:
        dataset = datasets.get_dataset(name=args.dataset_name)
    except Exception as exc:
        raise OptimizationError(
            f"could not load dataset {args.dataset_name!r}: {type(exc).__name__}; "
            "use --dataset-json for offline records"
        ) from exc
    return dataset_examples(dataset.to_df().to_dict("records"), val_fraction=args.val_fraction, seed=args.dataset_seed)


def _register_best_candidate(args: argparse.Namespace, candidate: str) -> dict[str, Any]:
    """
    Register the optimized best candidate as a versioned MLflow prompt.

    Parameters:
        args (argparse.Namespace): Connection, prompt name, and alias options.
        candidate (str): Optimized instruction text to register.

    Returns:
        dict[str, Any]: Bounded prompt registry metadata for the receipt.
    """
    receipt = register_prompt_text(
        template=candidate,
        prompt_name=args.prompt_name,
        mlflow_url=args.mlflow_url,
        experiment_id=args.experiment_id,
        experiment_name=args.registry_experiment_name,
        source="optimizer",
        commit_message=args.prompt_commit_message or None,
        alias=args.prompt_alias or "",
    )
    return {
        "prompt_name": receipt["prompt_name"],
        "prompt_version": receipt["version"],
        "prompt_alias": receipt["prompt_alias"],
        "signature_sha256": receipt["signature_sha256"],
    }


def optimize(args: argparse.Namespace) -> dict[str, Any]:
    """
    Run one optimization command and return its receipt payload.

    Parameters:
        args (argparse.Namespace): Connection, dataset, judge, and budget options.

    Returns:
        dict[str, Any]: Optimization receipt content (without schema envelope).

    Raises:
        OptimizationError: If preconditions or the omni run fail.
    """
    native_available = native_omni_available()
    if args.engine == "gepa":
        effective = "composition"
        native_engines: tuple[str, ...] = ()
    elif args.engine == "auto":
        effective = "native" if native_available else "composition"
        native_engines = ("gepa", "meta_harness", "autoresearch")
    else:
        if not native_available:
            raise OptimizationError(
                f"native omni engine {args.engine!r} requires the gepa omni registry (gepa.oa), "
                "unavailable: pin the gepa omni commit via [tool.uv] override-dependencies"
            )
        effective = "native"
        native_engines = (args.engine,)
    if not 0.0 <= args.cost_penalty_per_iteration <= 1.0:
        raise OptimizationError("cost-penalty-per-iteration must be in [0, 1]")
    _require_live()
    import mlflow

    mlflow.set_tracking_uri(args.mlflow_url)
    if args.scorer_source == "registry":
        experiment = mlflow.get_experiment_by_name(args.registry_experiment_name)
        if experiment is None:
            raise OptimizationError(f"MLflow experiment not found: {args.registry_experiment_name!r}")
        from mlflow.genai.scorers import get_scorer

        judges = {name: get_scorer(name=name, experiment_id=experiment.experiment_id) for name in JUDGE_NAMES}
    else:
        judge_params: dict[str, Any] | None = None
        if args.judge_params:
            try:
                judge_params = json.loads(args.judge_params)
            except json.JSONDecodeError as exc:
                raise OptimizationError("--judge-params must be a JSON object") from exc
            if not isinstance(judge_params, dict):
                raise OptimizationError("--judge-params must be a JSON object")
        judges = {
            name: judge
            for name, judge in zip(
                JUDGE_NAMES, build_judges(args.judge_model, inference_params=judge_params), strict=True
            )
        }

    train, val = _load_examples(args)
    if not train:
        raise OptimizationError("no training examples available")
    _progress_event(
        "job_start",
        engine=args.engine,
        scorer_source=args.scorer_source,
        train=len(train),
        val=len(val),
    )
    executor = InProcessTurnExecutor.from_role_settings(args)
    evaluator = make_evaluator(executor, judges, cost_penalty_per_iteration=args.cost_penalty_per_iteration)

    from fleet_rlm.rlm.signature import FleetRLMSignature

    seed_candidate = FleetRLMSignature.__doc__ or ""
    reflection = reflection_lm(args)
    if effective == "native":
        result = run_native_omni(
            seed_candidate,
            evaluator=evaluator,
            train=train,
            val=val,
            objective=args.objective,
            background=args.background,
            reflection=reflection,
            engines=native_engines,
            explore_evals=args.explore_metric_calls,
            continue_evals=args.continue_metric_calls,
            agent_model=args.agent_model,
            agent_effort=args.agent_effort,
        )
    else:
        result = run_omni(
            seed_candidate,
            evaluator=evaluator,
            train=train,
            val=val,
            objective=args.objective,
            background=args.background,
            reflection=reflection,
            explore_variants=args.explore_variants,
            explore_metric_calls=args.explore_metric_calls,
            continue_metric_calls=args.continue_metric_calls,
        )

    best_candidate = str(result["best_candidate"])
    candidate_path = args.candidate_out or (
        _REPO_ROOT / ".scratch" / "optimization" / f"candidate-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.txt"
    )
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(best_candidate, encoding="utf-8")
    _progress_event("receipt_written", candidate_out=str(candidate_path))

    prompt_registry: dict[str, Any] | None = None
    if args.register_prompt:
        prompt_registry = _register_best_candidate(args, best_candidate)
        _progress_event("prompt_registered", **prompt_registry)

    reported = {key: value for key, value in result.items() if key != "best_candidate"}
    return {
        **reported,
        "strategy": "explore-continue",
        "requested_engine": args.engine,
        "native_available": native_available,
        "cost_penalty_per_iteration": args.cost_penalty_per_iteration,
        "dataset": {
            "name": args.dataset_name if args.dataset_json is None else str(args.dataset_json),
            "train": len(train),
            "val": len(val),
        },
        "scorer_source": args.scorer_source,
        "judge_model": args.judge_model,
        "seed_candidate_sha256": hashlib.sha256(seed_candidate.encode("utf-8")).hexdigest(),
        "best_candidate_sha256": hashlib.sha256(best_candidate.encode("utf-8")).hexdigest(),
        "best_candidate_preview": best_candidate[:500],
        "candidate_out": str(candidate_path),
        "prompt_registry": prompt_registry,
    }


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for the signature optimization run.

    Returns:
        argparse.ArgumentParser: Parser configured with connection, dataset,
        judge, and budget options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--gateway-base-url", default="")
    parser.add_argument("--workspace-url", default="")
    parser.add_argument("--scorer-source", choices=("registry", "policy"), default="registry")
    parser.add_argument("--registry-experiment-name", default=_experiment_name_default())
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument(
        "--judge-params",
        default="",
        help="JSON object of judge inference params for policy scorer source "
        "(e.g. '{\"temperature\": 0}' for serving endpoints that reject reasoning_effort)",
    )
    parser.add_argument("--agent-model", default="sonnet", help="Claude model id for agent proposers")
    parser.add_argument("--agent-effort", default="low", help="Claude effort level for agent proposers")
    parser.add_argument("--dataset-name", default=_dataset_name_default(), help=DEFAULT_DATASET_NAME)
    parser.add_argument("--dataset-json", type=Path, default=None, help="Offline records file (skips UC fetch)")
    parser.add_argument("--val-fraction", type=float, default=0.0)
    parser.add_argument("--dataset-seed", type=int, default=0)
    parser.add_argument("--explore-variants", type=int, default=3)
    parser.add_argument("--explore-metric-calls", type=int, default=25)
    parser.add_argument("--continue-metric-calls", type=int, default=100)
    parser.add_argument(
        "--engine",
        choices=("auto", "gepa", "autoresearch", "meta_harness"),
        default="auto",
        help="auto/gepa run the fallback composition; native omni engines require gepa>=0.1.2",
    )
    parser.add_argument(
        "--cost-penalty-per-iteration",
        type=float,
        default=0.005,
        help="Pareto shaping: score penalty per REPL iteration (0 disables)",
    )
    parser.add_argument("--max-iters", type=int, default=20)
    parser.add_argument("--max-llm-calls", type=int, default=50)
    parser.add_argument("--max-output-chars", type=int, default=10_000)
    parser.add_argument("--worker-max-tokens", type=int, default=8_000)
    parser.add_argument("--reflection-max-tokens", type=int, default=16_000)
    parser.add_argument("--objective", default=DEFAULT_OBJECTIVE)
    parser.add_argument("--background", default=DEFAULT_BACKGROUND)
    parser.add_argument("--candidate-out", type=Path, default=None)
    parser.add_argument(
        "--register-prompt",
        action="store_true",
        help="Register the optimized best candidate as a versioned MLflow prompt",
    )
    parser.add_argument("--prompt-name", default=DEFAULT_PROMPT_NAME, help="MLflow prompt name for --register-prompt")
    parser.add_argument("--prompt-alias", default="", help="Alias for the registered prompt version")
    parser.add_argument("--prompt-commit-message", default="", help="Commit message for the registered prompt version")
    parser.add_argument(
        "--experiment-id",
        default="",
        help="Explicit experiment id for prompt registration (defaults to --registry-experiment-name)",
    )
    parser.add_argument(
        "--progress-stream",
        choices=("off", "ndjson"),
        default="off",
        help="Emit bounded ndjson progress events (off by default)",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the optimization command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the
            process arguments when omitted.

    Returns:
        int: `0` when the optimization completes, `1` when it fails.
    """
    load_dotenv(_REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    global _PROGRESS_STREAM
    _PROGRESS_STREAM = args.progress_stream
    try:
        payload = optimize(args)
    except Exception as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "ok",
            **payload,
        }
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
