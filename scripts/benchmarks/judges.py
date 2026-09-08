"""Shared MLflow GenAI judge definitions for Fleet RLM evaluation scripts.

One registration path keeps benchmark evaluation, production monitoring, and
judge alignment operating on the same scorer
registry instead of drifting into per-script judge variants.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Probe-verified cheap judge (2026-08 calibration, 4/4 fixture): strong boolean
# correctness and evidence_coverage verdicts well below Claude per token.
DEFAULT_JUDGE_MODEL = "databricks:/databricks-qwen35-122b-a10b"
DEFAULT_REFLECTION_MODEL = "databricks:/system.ai.claude-opus-4-8"
DEFAULT_EMBEDDING_MODEL = "databricks:/databricks-gte-large-en"
JUDGE_INFERENCE_PARAMS = {"temperature": 0, "reasoning_effort": "low"}

CORRECTNESS_DESCRIPTION = (
    "Check whether the response reaches the expected conclusion and preserves the expected material facts "
    "without contradiction."
)
EVIDENCE_COVERAGE_DESCRIPTION = (
    "Check whether the response materially uses every required evidence item, preserves the required "
    "uncertainty, and avoids the forbidden claims for the evaluation case."
)
CORRECTNESS_INSTRUCTIONS = (
    "Compare {{ outputs }} with {{ expectations }}. Set result true iff the response matches expected_response "
    "without a material contradiction. Accept equivalent wording and use no outside knowledge."
)
EVIDENCE_COVERAGE_INSTRUCTIONS = """
Check {{ outputs }} against {{ expectations }}. Set result true iff every required_evidence item materially
supports the conclusion, required_uncertainty is preserved, and no forbidden_claims are asserted. Accept
equivalent wording and use no outside knowledge.
""".strip()

JUDGE_NAMES: tuple[str, ...] = ("correctness", "evidence_coverage")


def build_judge(
    name: str,
    model: str,
    *,
    inference_params: dict[str, Any] | None = None,
    generate_rationale_first: bool = False,
    name_suffix: str = "",
) -> Any:
    """
    Build one Fleet evaluation judge for the given MLflow model URI.

    Parameters:
        name (str): Registered judge name, one of ``JUDGE_NAMES``.
        model (str): MLflow-supported judge model URI.
        inference_params (dict[str, Any] | None): Judge inference parameters;
            defaults to ``JUDGE_INFERENCE_PARAMS``. Serving endpoints that
            reject the AI-Gateway-only knobs may pass a narrower mapping.
        generate_rationale_first (bool): Whether MLflow should condition the
            verdict on a generated rationale before emitting its value.
        name_suffix (str): Optional in-memory suffix used to keep experimental
            scorer names distinct from canonical registry names.

    Returns:
        Any: A ``make_judge`` scorer matching the Fleet registry contract.

    Raises:
        ValueError: If ``name`` is not a Fleet judge.
    """
    from mlflow.genai.judges import make_judge

    params = inference_params if inference_params is not None else JUDGE_INFERENCE_PARAMS
    scorer_name = f"{name}{name_suffix}"
    if name == "correctness":
        return make_judge(
            name=scorer_name,
            model=model,
            description=CORRECTNESS_DESCRIPTION,
            feedback_value_type=bool,
            inference_params=params,
            instructions=CORRECTNESS_INSTRUCTIONS,
            generate_rationale_first=generate_rationale_first,
        )
    if name == "evidence_coverage":
        return make_judge(
            name=scorer_name,
            model=model,
            description=EVIDENCE_COVERAGE_DESCRIPTION,
            feedback_value_type=bool,
            inference_params=params,
            instructions=EVIDENCE_COVERAGE_INSTRUCTIONS,
            generate_rationale_first=generate_rationale_first,
        )
    raise ValueError(f"unknown Fleet judge: {name!r}")


def build_judges(
    model: str,
    *,
    inference_params: dict[str, Any] | None = None,
    generate_rationale_first: bool = False,
    name_suffix: str = "",
) -> list[Any]:
    """
    Build all Fleet evaluation judges for the given MLflow model URI.

    Parameters:
        model (str): MLflow-supported judge model URI.
        inference_params (dict[str, Any] | None): Shared judge inference
            parameters; defaults to ``JUDGE_INFERENCE_PARAMS`` per judge.
        generate_rationale_first (bool): Whether the judge emits rationale
            before its verdict.
        name_suffix (str): Optional suffix for distinct in-memory scorer names.

    Returns:
        list[Any]: One scorer per entry in ``JUDGE_NAMES``.
    """
    return [
        build_judge(
            name,
            model,
            inference_params=inference_params,
            generate_rationale_first=generate_rationale_first,
            name_suffix=name_suffix,
        )
        for name in JUDGE_NAMES
    ]


def _rationale_first_setting(scorer: Any) -> bool:
    """Read MLflow's rationale setting without comparing its full serialization."""
    setting = getattr(scorer, "_generate_rationale_first", None)
    if isinstance(setting, bool):
        return setting
    setting = getattr(scorer, "generate_rationale_first", None)
    if isinstance(setting, bool):
        return setting
    model_dump = getattr(scorer, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        nested = dumped.get("instructions_judge_pydantic_data") if isinstance(dumped, dict) else None
        value = nested.get("generate_rationale_first") if isinstance(nested, dict) else None
        if isinstance(value, bool):
            return value
    return False


def _serialized_policy_fields(scorer: Any) -> Mapping[str, Any]:
    """Read MLflow's behavior payload without retaining runtime metadata."""
    model_dump = getattr(scorer, "model_dump", None)
    if not callable(model_dump):
        return {}
    try:
        dumped = model_dump()
    except Exception:
        return {}
    nested = dumped.get("instructions_judge_pydantic_data") if isinstance(dumped, dict) else None
    return nested if isinstance(nested, Mapping) else {}


def _policy_field(scorer: Any, fields: Mapping[str, Any], name: str) -> Any:
    """Prefer a public/fake field and fall back to MLflow's policy payload."""
    value = getattr(scorer, name, None)
    return fields.get(name) if value is None else value


def _normalize_policy_value(value: Any) -> Any:
    """Make one stable policy value safe for receipt comparison."""
    if isinstance(value, Mapping):
        return {str(key): _normalize_policy_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize_policy_value(item) for item in value]
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if value is None or isinstance(value, (bool, float, int, str)):
        return value
    return str(value)


def normalized_judge_policy(scorer: Any) -> dict[str, Any]:
    """Return the registry policy fields that define one Fleet judge.

    MLflow serializes runtime/version metadata alongside the policy. Registry
    drift should compare only the stable behavior fields, including the 3.16
    rationale-first setting.
    """
    fields = _serialized_policy_fields(scorer)
    inference_params = _normalize_policy_value(_policy_field(scorer, fields, "inference_params"))
    feedback_value_type = _normalize_policy_value(_policy_field(scorer, fields, "feedback_value_type"))
    return {
        "model": _normalize_policy_value(_policy_field(scorer, fields, "model")),
        "description": _normalize_policy_value(_policy_field(scorer, fields, "description")),
        "instructions": _normalize_policy_value(_policy_field(scorer, fields, "instructions")),
        "feedback_value_type": feedback_value_type,
        "inference_params": inference_params,
        "generate_rationale_first": _rationale_first_setting(scorer),
    }


def ensure_registered(
    name: str,
    model: str,
    *,
    experiment_id: str,
    generate_rationale_first: bool = False,
) -> bool:
    """
    Register one judge when the experiment registry differs from current policy.

    Parameters:
        name (str): Registered judge name, one of ``JUDGE_NAMES``.
        model (str): MLflow-supported judge model URI.
        experiment_id (str): Target MLflow experiment identifier.
        generate_rationale_first (bool): Explicitly promoted rationale policy;
            defaults to the existing baseline behavior.

    Returns:
        bool: `True` when a (re)registration was written, `False` when the
            registered scorer already matches policy.
    """
    from mlflow.genai.scorers import list_scorers

    candidate = build_judge(name, model, generate_rationale_first=generate_rationale_first)
    registered = {scorer.name: scorer for scorer in list_scorers(experiment_id=experiment_id)}.get(name)
    drifted = normalized_judge_policy(registered) != normalized_judge_policy(candidate)
    if not drifted:
        return False
    candidate.register(experiment_id=experiment_id)
    return True
