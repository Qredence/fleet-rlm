"""Shared Fleet contracts: the route vocabulary, stopping rules, and runtime input names.

This module is a leaf. The runtime (`engine`, `sandbox`) and the offline tooling
(`optimize`) both import it, so it must not import either of them.
"""

from __future__ import annotations

import functools
import json
import math
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
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
    "requested_skills",
)


class Route(StrEnum):
    """How a Turn is executed. Values are the wire, trace, and label names."""

    DIRECT = "direct"  # ChainOfThought, no code execution
    FLEX = "flex"  # dspy.Flex pipeline with Daytona Python
    RLM = "rlm"  # dspy.RLM with Daytona Python and child agents


@dataclass(frozen=True)
class RequiredSkill:
    """Captured deployment bytes; not learned state or a claim of reviewed execution."""

    name: str
    source: str
    sha256: str
    skill_md: str
    files: tuple[tuple[str, bytes], ...] = field(repr=False)


class SkillAdmissionError(ValueError):
    """Caller-safe required-Skill refusal, containing no filesystem/provider details."""


@dataclass(frozen=True)
class OptimizationInputs:
    """What only an isolated optimization rollout supplies; absent on every ordinary Turn."""

    context: Any = None
    verifications: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    required_skills: tuple[RequiredSkill, ...] = ()


def turn_is_over(exc: BaseException) -> bool:
    """Deadline, cancellation and shared budget exhaustion end the entire Turn."""
    return isinstance(exc, TimeoutError) or str(exc).startswith(("Turn cancelled", "Shared RLM budget exhausted"))


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Decode the JSON data lines from a Fleet SSE response, ignoring malformed lines."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    return events


def catalog_entries(catalog: str) -> list[tuple[str, str]] | None:
    """(name, description) pairs from a rendered Stage-1 catalog; None when it is not valid XML."""
    try:
        root = ElementTree.fromstring(catalog)
    except ElementTree.ParseError:
        return None
    return [(item.get("name") or "", (item.text or "").strip()) for item in root.iter("skill") if item.get("name")]


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
    "Measure through materializing lazy results, converting the result to the required representation, "
    "and extracting the answer; timing only a lazy constant or handle assignment is insufficient. "
    "Label local computation time separately from sandbox or Turn wall time. "
    "Retain only exact observed representations in a learned note; do not rescale figures (for example to M). "
    "Print any retained precision parameters during the computation; otherwise omit them from the note. "
    "Never add an execution solely to create or complete an optional learned note. "
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
        "triage": "A task needing code, files, Skills, or iterative investigation",
        "judge": "Code, file or Skill work, including bounded calculations and iterative investigation",
        "definition": "Bounded RLM for code, file or Skill work, exploration or decomposition",
        "examples": ["Compute a specified digit", "Summarize an attached table", "Investigate conflicting evidence"],
        "not": "A route that guarantees delegation; both execution routes can delegate",
        "summary": "Execute a bounded RLM with Daytona Python and optional child agents.",
    },
}

TRIAGE_INSTRUCTION = (
    "Choose direct for a self-contained response requiring no code, files, or Skills; "
    "choose rlm for code, file or Skill work, including bounded calculations and iterative investigation."
)


def make_choice_type(options: tuple[tuple[str, str], ...]) -> type[BaseModel]:
    """Build a validated DSPy Choice model from runtime options."""
    result = cast("Any", Choice)[options]
    if not isinstance(result, type) or not issubclass(result, BaseModel):
        raise RuntimeError("DSPy returned an invalid typed Choice model")
    return result


TRIAGE_ROUTE = make_choice_type(
    tuple((route.value, ROUTE_DESCRIPTIONS[route]["triage"]) for route in (Route.DIRECT, Route.RLM))
)
TRIAGE_CRITERIA = {
    route.value: {"definition": item["definition"], "examples": item["examples"], "not": item["not"]}
    for route, item in ROUTE_DESCRIPTIONS.items()
    if route in (Route.DIRECT, Route.RLM)
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


@functools.lru_cache(maxsize=64)
def _item_decision_cached(choices: tuple[str, ...]) -> type[dspy.Signature]:
    """Build the classification signature for one choice vocabulary."""
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


def item_decision(choices: list[str]) -> type[dspy.Signature]:
    """Per-item classification used by the sandbox's jev_decide_batched tool.

    The returned class is shared across callers with the same vocabulary, so treat
    it as immutable: build a new signature instead of editing this one.
    """
    return _item_decision_cached(tuple(choices))


# A figure: an optionally signed number, with `$` and thousands separators allowed; not part of an identifier.
_FIGURE = re.compile(r"(?<![\w.])\$?-?\d[\d,]*(?:\.\d+)?")


def figures(text: str, *, include_small: bool = False) -> dict[str, float]:
    """Normalized number literals in `text`.

    Small integers carry no grounding evidence and are ignored by default; `include_small` keeps every
    literal, which is what a gold label needs.
    """
    found: dict[str, float] = {}
    for match in _FIGURE.finditer(text):
        literal = match.group().replace("$", "").replace(",", "")
        try:
            value = float(literal)
        except ValueError:
            continue
        if include_small or "." in literal or abs(value) >= 10:
            found[literal] = value
    return found


def figure_grounded(literal: str, value: float, observed: set[float]) -> bool:
    """A typed figure is grounded when some observed value rounds to it at the typed precision."""
    places = len(literal.split(".", 1)[1]) if "." in literal else 0
    return any(round(seen, places) == round(value, places) for seen in observed)
