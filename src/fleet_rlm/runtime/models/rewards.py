"""Quality-constraint reward functions for DSPy 3.x Refine/BestOfN.

DSPy 3.x replaced ``dspy.Assert``/``dspy.Suggest`` with ``dspy.Refine``
and ``dspy.BestOfN``.  These reward functions score module predictions
and are used as:

    dspy.Refine(module, N=3, reward_fn=fn, threshold=0.7)

See https://dspy.ai/api/modules/Refine/ for details.
"""

from __future__ import annotations

from typing import Any

import dspy


# ---------------------------------------------------------------------------
# Reward functions
# ---------------------------------------------------------------------------


def grounded_answer_reward(_args: dict[str, Any], prediction: dspy.Prediction) -> float:
    """Reward for ``GroundedAnswerSynthesisModule`` outputs.

    Checks: non-empty answer (0.4), at least one citation (0.3),
    confidence > 0.3 (0.3).  Returns 0.0–1.0.
    """
    score = 0.0
    answer = str(getattr(prediction, "answer", "") or "").strip()
    if answer and len(answer) > 10:
        score += 0.4
    citations = getattr(prediction, "citations", []) or []
    if citations:
        score += 0.3
    confidence = getattr(prediction, "confidence", 0)
    try:
        conf_val = float(confidence)
    except (TypeError, ValueError):
        conf_val = 0.0
    if conf_val > 0.3:
        score += 0.3
    return score


def sub_rlm_answer_reward(_args: dict[str, Any], prediction: dspy.Prediction) -> float:
    """Reward for ``RecursiveSubQuerySignature`` outputs.

    Checks: non-empty answer with meaningful content.
    """
    answer = str(getattr(prediction, "answer", "") or "").strip()
    if not answer:
        return 0.0
    if len(answer) < 5:
        return 0.2
    if answer.lower() in ("n/a", "none", "unknown", "i don't know"):
        return 0.1
    return 1.0


def variable_mode_answer_reward(
    _args: dict[str, Any], prediction: dspy.Prediction
) -> float:
    """Reward for ``RLMVariableSignature`` outputs.

    Checks: non-empty answer with meaningful length.
    """
    answer = str(getattr(prediction, "answer", "") or "").strip()
    if not answer:
        return 0.0
    if len(answer) < 10:
        return 0.3
    return 1.0


def memory_action_reward(_args: dict[str, Any], prediction: dspy.Prediction) -> float:
    """Reward for ``MemoryActionIntentSignature`` outputs.

    Checks: valid action type and risk_level fields.
    """
    score = 0.0
    action = str(getattr(prediction, "action_type", "") or "").strip().lower()
    valid_actions = {"read", "write", "move", "delete", "audit", "migrate"}
    if action in valid_actions:
        score += 0.5
    risk = str(getattr(prediction, "risk_level", "") or "").strip().lower()
    if risk in ("low", "medium", "high"):
        score += 0.3
    rationale = str(getattr(prediction, "rationale", "") or "").strip()
    if rationale and len(rationale) > 10:
        score += 0.2
    return score


# ---------------------------------------------------------------------------
# Wrapper helper
# ---------------------------------------------------------------------------


def refine_module(
    module: dspy.Module,
    reward_fn: Any,
    *,
    n: int = 3,
    threshold: float = 0.7,
) -> dspy.Module:
    """Wrap a module in ``dspy.Refine`` for quality-constrained generation.

    ``dspy.Refine`` runs the module up to *n* times at ``temperature=1.0``
    and returns either the first prediction exceeding *threshold* or the
    best-scoring one.  On failure it generates feedback for improvement.
    """
    return dspy.Refine(module, N=n, reward_fn=reward_fn, threshold=threshold)
