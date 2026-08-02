"""Shared MLflow GenAI judge definitions for Fleet RLM evaluation scripts.

One registration path keeps benchmark evaluation, production monitoring,
judge alignment, and signature optimization operating on the same scorer
registry instead of drifting into per-script judge variants.
"""

from __future__ import annotations

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


def build_judge(name: str, model: str, *, inference_params: dict[str, Any] | None = None) -> Any:
    """
    Build one Fleet evaluation judge for the given MLflow model URI.

    Parameters:
        name (str): Registered judge name, one of ``JUDGE_NAMES``.
        model (str): MLflow-supported judge model URI.
        inference_params (dict[str, Any] | None): Judge inference parameters;
            defaults to ``JUDGE_INFERENCE_PARAMS``. Serving endpoints that
            reject the AI-Gateway-only knobs may pass a narrower mapping.

    Returns:
        Any: A ``make_judge`` scorer matching the Fleet registry contract.

    Raises:
        ValueError: If ``name`` is not a Fleet judge.
    """
    from mlflow.genai.judges import make_judge

    params = inference_params if inference_params is not None else JUDGE_INFERENCE_PARAMS
    if name == "correctness":
        return make_judge(
            name="correctness",
            model=model,
            description=CORRECTNESS_DESCRIPTION,
            feedback_value_type=bool,
            inference_params=params,
            instructions=CORRECTNESS_INSTRUCTIONS,
        )
    if name == "evidence_coverage":
        return make_judge(
            name="evidence_coverage",
            model=model,
            description=EVIDENCE_COVERAGE_DESCRIPTION,
            feedback_value_type=bool,
            inference_params=params,
            instructions=EVIDENCE_COVERAGE_INSTRUCTIONS,
        )
    raise ValueError(f"unknown Fleet judge: {name!r}")


def build_judges(model: str, *, inference_params: dict[str, Any] | None = None) -> list[Any]:
    """
    Build all Fleet evaluation judges for the given MLflow model URI.

    Parameters:
        model (str): MLflow-supported judge model URI.
        inference_params (dict[str, Any] | None): Shared judge inference
            parameters; defaults to ``JUDGE_INFERENCE_PARAMS`` per judge.

    Returns:
        list[Any]: One scorer per entry in ``JUDGE_NAMES``.
    """
    return [build_judge(name, model, inference_params=inference_params) for name in JUDGE_NAMES]


def ensure_registered(name: str, model: str, *, experiment_id: str) -> bool:
    """
    Register one judge when the experiment registry differs from current policy.

    Parameters:
        name (str): Registered judge name, one of ``JUDGE_NAMES``.
        model (str): MLflow-supported judge model URI.
        experiment_id (str): Target MLflow experiment identifier.

    Returns:
        bool: `True` when a (re)registration was written, `False` when the
            registered scorer already matches policy.
    """
    from mlflow.genai.scorers import list_scorers

    candidate = build_judge(name, model)
    registered = {scorer.name: scorer for scorer in list_scorers(experiment_id=experiment_id)}.get(name)
    drifted = any(
        (
            getattr(registered, "model", None) != candidate.model,
            getattr(registered, "description", None) != candidate.description,
            getattr(registered, "instructions", None) != candidate.instructions,
            getattr(registered, "feedback_value_type", None) != candidate.feedback_value_type,
            getattr(registered, "inference_params", None) != candidate.inference_params,
        )
    )
    if not drifted:
        return False
    candidate.register(experiment_id=experiment_id)
    return True
