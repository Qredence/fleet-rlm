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
JUDGE_INFERENCE_PARAMS = {"temperature": 0, "reasoning_effort": "low", "max_tokens": 1024}

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


import re

DEFAULT_GUIDELINES = "The response must be concise, stay within the requested scope, and avoid unsupported claims."

CUSTOM_SCORER_NAMES: tuple[str, ...] = ("response_present", "tool_evidence_used")

BUILTIN_SCORER_NAMES: tuple[str, ...] = ("guidelines", "retrieval_groundedness")

SCORER_NAMES: tuple[str, ...] = CUSTOM_SCORER_NAMES + BUILTIN_SCORER_NAMES

RESPONSE_PRESENT_DESCRIPTION = "Whether the response is a non-empty answer."

TOOL_EVIDENCE_DESCRIPTION = (
    "Whether the trace performed tool calls whose output text covers every required_evidence item."
)


def response_present_impl(*, outputs: Any = None) -> bool:
    """Return whether the response is a non-empty answer string."""
    return bool(str(outputs or "").strip())


def _span_text(span: Any) -> str:
    """Read tool outputs only; request metadata does not demonstrate evidence."""
    value = getattr(span, "outputs", None)
    return str(value) if value is not None else ""


def tool_evidence_used_impl(*, trace: Any = None, expectations: Any = None) -> bool:
    """
    Return whether the trace's tool spans covered the required evidence.

    Parameters:
        trace (Any): MLflow Trace for the evaluated row, or ``None`` when the
            evaluation dataset carries no trace column.
        expectations (Any): Expectations mapping; ``required_evidence`` must be
            a list of evidence identifiers when present.

    Returns:
        bool: `True` only when tool spans exist and every required evidence
            identifier appears in their output text. `False` when the trace is
            absent or evidence cannot be confirmed.
    """
    if not isinstance(expectations, Mapping):
        return False
    required = expectations.get("required_evidence")
    if not isinstance(required, list) or not required:
        return False
    if any(not isinstance(item, str) or not item.strip() for item in required):
        return False
    data = getattr(trace, "data", None)
    spans = list(getattr(data, "spans", None) or []) if data is not None else []
    tool_text = " ".join(
        _span_text(span) for span in spans if str(getattr(span, "span_type", "") or "").upper() == "TOOL"
    ).lower()
    if not tool_text:
        return False
    return all(re.search(r"(?<!\w)" + re.escape(item.strip().lower()) + r"(?!\w)", tool_text) for item in required)


def build_scorer(name: str, *, judge_model: str | None = None, guidelines: str | None = None) -> Any:
    """
    Build one Fleet or MLflow built-in scorer by name.

    Parameters:
        name (str): Scorer name, one of ``SCORER_NAMES``.
        judge_model (str | None): Judge model URI required by built-in LLM scorers.
        guidelines (str | None): Guideline text for the ``guidelines`` scorer.

    Returns:
        Any: A callable scorer usable in ``mlflow.genai.evaluate``.

    Raises:
        ValueError: If ``name`` is unknown or a built-in scorer lacks ``judge_model``.
    """
    if name == "response_present":
        from mlflow.genai.scorers import scorer

        return scorer(name="response_present", description=RESPONSE_PRESENT_DESCRIPTION)(response_present_impl)
    if name == "tool_evidence_used":
        from mlflow.genai.scorers import scorer

        return scorer(name="tool_evidence_used", description=TOOL_EVIDENCE_DESCRIPTION)(tool_evidence_used_impl)
    if name == "guidelines":
        if not judge_model:
            raise ValueError("the guidelines scorer requires a --judge-model URI")
        from mlflow.genai.scorers import Guidelines

        return Guidelines(name="guidelines", guidelines=guidelines or DEFAULT_GUIDELINES, model=judge_model)
    if name == "retrieval_groundedness":
        if not judge_model:
            raise ValueError("the retrieval_groundedness scorer requires a --judge-model URI")
        from mlflow.genai.scorers import RetrievalGroundedness

        return RetrievalGroundedness(name="retrieval_groundedness", model=judge_model)
    raise ValueError(f"unknown Fleet scorer: {name!r}")


def build_scorers(
    names: list[str],
    *,
    judge_model: str | None = None,
    guidelines: str | None = None,
) -> list[Any]:
    """
    Build multiple scorers by name, preserving order.

    Parameters:
        names (list[str]): Scorer names from ``SCORER_NAMES``.
        judge_model (str | None): Judge model URI required by built-in LLM scorers.
        guidelines (str | None): Guideline text for the ``guidelines`` scorer.

    Returns:
        list[Any]: One callable scorer per requested name.
    """
    return [build_scorer(name, judge_model=judge_model, guidelines=guidelines) for name in names]


__all__ = [
    "BUILTIN_SCORER_NAMES",
    "CORRECTNESS_DESCRIPTION",
    "CORRECTNESS_INSTRUCTIONS",
    "CUSTOM_SCORER_NAMES",
    "DEFAULT_GUIDELINES",
    "DEFAULT_JUDGE_MODEL",
    "EVIDENCE_COVERAGE_DESCRIPTION",
    "EVIDENCE_COVERAGE_INSTRUCTIONS",
    "JUDGE_INFERENCE_PARAMS",
    "JUDGE_NAMES",
    "RESPONSE_PRESENT_DESCRIPTION",
    "SCORER_NAMES",
    "TOOL_EVIDENCE_DESCRIPTION",
    "build_judge",
    "build_judges",
    "build_scorer",
    "build_scorers",
    "ensure_registered",
    "normalized_judge_policy",
    "response_present_impl",
    "tool_evidence_used_impl",
]
