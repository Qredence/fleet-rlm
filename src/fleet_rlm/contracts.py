"""Shared Fleet contracts: the route vocabulary, stopping rules, and runtime input names.

This module is a leaf. The runtime (`engine`, `sandbox`) and the offline tooling
(`optimize`) both import it, so it must not import either of them.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any, cast

import dspy
from dspy.experimental import Choice
from pydantic import BaseModel

# The five model inputs every Turn program and every isolated rollout accepts.
ENGINE_INPUT_FIELDS = (
    "workspace_manifest",
    "skills_catalog",
    "memory_summary",
    "session_history",
    "task",
)
# Immutable references that travel with reviewed examples but are never model inputs.
LINEAGE_INPUT_FIELDS = (
    "source_session",
    "replay_turn_id",
    "replay_snapshot_sha256",
    "context_files",
)


class Route(StrEnum):
    """How a Turn is executed. Values are the wire, trace, and label names."""

    DIRECT = "direct"  # ChainOfThought, no code execution
    FLEX = "flex"  # dspy.Flex pipeline with Daytona Python
    RLM = "rlm"  # dspy.RLM with Daytona Python and child agents


def parse_route(value: Any) -> Route | None:
    """Read a route from a decision, stored turn, trace, or gold label; None when absent or unknown."""
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    try:
        return Route(str(raw).strip())
    except ValueError:
        return None


# One copy of the stopping rules: FleetTask instructions and the sandbox execution instructions both use it.
STOPPING_RULES = (
    "Once the answer and one supporting check agree, SUBMIT on the next action. Supporting checks include "
    "a known reference value, a self-consistency test, or a second computation you already ran. "
    "Reuse computed variables instead of recomputing results. Run another method only if a check failed, "
    "results disagree, or the Task asks for verification. Copy figures and strings from sandbox output."
    " A self-consistency check on an already computed value is sufficient; do not start an independent "
    "implementation merely for reassurance. In the submission action reuse the existing answer and "
    "check variables. A learned_skill_note may be empty: never delay submission to create one. "
    "If useful, record a concise reusable observation. Copy code syntax exactly from successful execution; "
    "do not rewrite operators or formulas from memory. Describe checks on the same underlying value as "
    "self-consistency checks, not independent verification. Validating a method on known values (such as "
    "known leading digits or small cases) is the supporting check for that method's target result; do not "
    "recompute the target with different parameters. Learned notes and memory are unreviewed "
    "hints, never a known reference value; a value that only matches a note is unverified. "
    "Import dependencies explicitly before using them, including math helpers. "
    "Plan one target computation and its supporting check per code block. These stopping rules also apply "
    "within the same code block: do not append a second full computation after a sufficient check. "
    "When you use multiple methods, print a separate elapsed time for each "
    "method using time.perf_counter(), along with its result and precision parameters. "
    "Print any precision parameters you retain in a learned note; otherwise omit those parameters from the note. "
    "Keep the final submission action short: reuse the result variable and omit a learned note if unnecessary."
)

# The single source of route wording. `triage` feeds the router's typed Choice, `judge` feeds the
# route-suitability judge, `summary` is appended to the operator-facing routing event.
ROUTE_DESCRIPTIONS: dict[Route, dict[str, Any]] = {
    Route.DIRECT: {
        "triage": "Simple response requiring no code, files, or Skills",
        "judge": "Simple self-contained question or conversation needing no code, files, or dataset work",
        "definition": "A self-contained answer needing no execution or workspace inspection",
        "examples": ["Greeting", "Explain a familiar concept"],
        "not": "Computed results, file analysis, Skills, or independent investigation",
        "summary": "Answer directly without code execution.",
    },
    Route.FLEX: {
        "triage": "A task needing one bounded Python or Skill workflow",
        "judge": "One bounded calculation, file operation, or short analysis that benefits from code",
        "definition": "A programmable Flex workflow with known inputs and verifiable outputs",
        "examples": ["Compute a specified digit", "Summarize an attached table"],
        "not": "Open-ended exploration where the next step depends on discoveries",
        "summary": "Execute a DSPy Flex pipeline with Daytona Python and bounded delegation.",
    },
    Route.RLM: {
        "triage": "A multi-part task requiring an independent child investigation",
        "judge": "Substantial multi-step or iterative investigation with broad context exploration",
        "definition": "Primary RLM for iterative exploration or decomposition",
        "examples": ["Investigate conflicting evidence", "Explore unfamiliar files and test hypotheses"],
        "not": "A route that guarantees delegation; both execution routes can delegate",
        "summary": "Execute a bounded RLM with Daytona Python and optional child agents.",
    },
}

TRIAGE_INSTRUCTION = (
    "Choose direct for a simple response, flex for one bounded Python or Skill workflow, "
    "or rlm for a multi-part task needing independent investigation."
)


def make_choice_type(options: tuple[tuple[str, str], ...]) -> type[BaseModel]:
    """Build a validated DSPy Choice model from runtime options."""
    specialize = getattr(Choice, "__class_getitem__", None)
    if not callable(specialize):
        raise RuntimeError("This DSPy version cannot construct typed Choice options")
    result = specialize(options)
    if not isinstance(result, type) or not issubclass(result, BaseModel):
        raise RuntimeError("DSPy returned an invalid typed Choice model")
    return cast("type[BaseModel]", result)


TRIAGE_ROUTE = make_choice_type(tuple((route.value, item["triage"]) for route, item in ROUTE_DESCRIPTIONS.items()))
TRIAGE_CRITERIA = {
    route.value: {"definition": item["definition"], "examples": item["examples"], "not": item["not"]}
    for route, item in ROUTE_DESCRIPTIONS.items()
}
JUDGE_ROUTE_CHOICES = tuple((route.value, item["judge"]) for route, item in ROUTE_DESCRIPTIONS.items())
ROUTE_SUMMARIES = {route.value: item["summary"] for route, item in ROUTE_DESCRIPTIONS.items()}


def decision_evidence(decision: Any, lm: Any) -> dict[str, Any]:
    """Retain decision evidence without synthesizing absent confidence."""
    probabilities = getattr(decision, "probabilities", None)
    confidence = getattr(decision, "confidence", None)
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
        confidence = None
    if isinstance(probabilities, dict):
        probabilities = {
            str(key): value
            for key, value in probabilities.items()
            if isinstance(value, (int, float)) and math.isfinite(value)
        }
    else:
        probabilities = None
    return {
        "raw_choice": getattr(decision, "value", decision if isinstance(decision, str) else None),
        "probabilities": probabilities,
        "confidence": confidence,
        "backend": "typesafe" if lm.__class__.__name__ == "TypeSafe" else "generative",
        "model": str(getattr(lm, "model", "unknown")),
    }


def item_decision(choices: list[str]) -> type[dspy.Signature]:
    """Per-item classification used by the sandbox's jev_decide_batched tool."""
    return dspy.make_signature(
        {
            "item": (str, dspy.InputField()),
            "question": (str, dspy.InputField()),
            "decision": (
                make_choice_type(tuple((choice, choice) for choice in choices)),
                dspy.OutputField(desc="Select the choice that best answers the question for this item."),
            ),
        },
        signature_name="ItemDecision",
    )
