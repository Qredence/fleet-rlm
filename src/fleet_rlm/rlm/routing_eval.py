"""Bounded RLM routing evaluation from public execution evidence.

This module deliberately measures routing from owned public facts: Tool
observations, recursive summaries, sandbox creation counts, latency, and
answer output. It never requires private model prompts or chain-of-thought.
"""

from __future__ import annotations

import hashlib
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import dspy

from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm
from fleet_rlm.rlm.events import ObservationDetail, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveCallSummary, RecursiveRLMExecutor, RecursiveRLMOptions
from fleet_rlm.rlm.signature import root_signature_for_recursion

RoutingClass = Literal[
    "python_native",
    "semantic_single",
    "semantic_batched",
    "recursive_child",
    "recursive_depth_fallback",
]

_EXPECTED_ORDER: tuple[RoutingClass, ...] = (
    "python_native",
    "semantic_single",
    "semantic_batched",
    "recursive_child",
    "recursive_depth_fallback",
)

_ROUTE_DECISION_TREE = """Decision tree:
1. Use Python/REPL code for deterministic search, parsing, aggregation, and verification.
2. Use llm_query for one bounded semantic judgment Python cannot decide.
3. Use llm_query_batched for two or more independent bounded judgments.
4. Use rlm_query only for a self-contained subproblem that needs iterative Python exploration.
5. A recursive child request beyond RLM_NATIVE_CHILD_DEPTH uses
   the bounded Sub-LM fallback; it never allocates another child Sandbox.
"""


@dataclass(frozen=True, slots=True)
class RoutingScenario:
    """One owned routing benchmark case, with no hidden model text expectations."""

    name: str
    prompt: str
    expected_route: RoutingClass
    expected_answer: str


@dataclass(frozen=True, slots=True)
class RoutingFacts:
    """Observable aggregate facts from one scenario execution."""

    tool_counts: Mapping[str, int] = field(default_factory=dict)
    native_child_count: int = 0
    max_native_child_depth: int = 0
    depth_fallback_count: int = 0
    child_iterations: int = 0
    recursive_prompt_chars: int = 0
    latency_ms: int = 0
    sandbox_count: int = 0
    total_tool_calls: int = 0


@dataclass(frozen=True, slots=True)
class RoutingScore:
    """Separate answer correctness from routing efficiency."""

    scenario: str
    expected_route: RoutingClass
    observed_route: RoutingClass
    run_index: int
    answer_sha256: str
    answer_correct: bool
    routing_match: bool
    routing_efficiency: float
    facts: RoutingFacts
    rationale: str = ""


CURATED_ROUTING_SCENARIOS: tuple[RoutingScenario, ...] = (
    RoutingScenario(
        "python-exact-deterministic",
        "Use Python to compute 18434 + 92786 and return only the exact integer answer.",
        "python_native",
        "111220",
    ),
    RoutingScenario(
        "semantic-single-judgment",
        "Use one bounded semantic judgment to decide whether 'photosynthesis' is a biological process. "
        "Return yes or no.",
        "semantic_single",
        "yes",
    ),
    RoutingScenario(
        "semantic-independent-batch",
        "Classify these independent labels: ballast (object), stroll (action), cobalt (color). Use "
        "batched independent semantic judgment, then return the three labels as object/action/color.",
        "semantic_batched",
        "object/action/color",
    ),
    RoutingScenario(
        "recursive-iterative-subproblem",
        "Delegate the self-contained iterative subproblem that computes the sum of squares "
        "of integers 1 through 8 with Python and "
        "returns only its exact integer result, then return that child answer.",
        "recursive_child",
        "204",
    ),
    RoutingScenario(
        "recursive-depth-fallback",
        "From within a native child, ask for one more bounded classification of the label "
        "'phosphorus' as an element and "
        "return the fallback answer.",
        "recursive_depth_fallback",
        "element",
    ),
)


def routing_decision_tree() -> str:
    """Return the documented evaluation decision tree."""
    return _ROUTE_DECISION_TREE


def classify_routing_facts(facts: RoutingFacts) -> RoutingClass:
    """Classify one execution from observable facts without CoT access."""
    counts = {str(key): int(value) for key, value in facts.tool_counts.items()}
    if facts.depth_fallback_count > 0:
        return "recursive_depth_fallback"
    if counts.get("rlm_query", 0) > 0 or facts.native_child_count > 0 or facts.max_native_child_depth > 0:
        return "recursive_child"
    if counts.get("llm_query_batched", 0) > 0:
        return "semantic_batched"
    if counts.get("llm_query", 0) > 0:
        return "semantic_single"
    return "python_native"


def facts_from_recursive_summary(
    summary: RecursiveCallSummary,
    *,
    latency_ms: int,
    tool_counts: Mapping[str, int] | None = None,
    sandbox_count: int = 0,
) -> RoutingFacts:
    """Project the owned recursive summary into routing evidence."""
    return RoutingFacts(
        tool_counts=dict(tool_counts or {}),
        native_child_count=summary.call_count - summary.depth_fallback_count,
        max_native_child_depth=1 if summary.call_count > summary.depth_fallback_count else 0,
        depth_fallback_count=summary.depth_fallback_count,
        child_iterations=summary.child_iterations,
        recursive_prompt_chars=summary.maximum_prompt_chars,
        latency_ms=latency_ms,
        sandbox_count=sandbox_count,
        total_tool_calls=sum(int(value) for value in (tool_counts or {}).values()),
    )


def facts_from_execution_details(
    details: tuple[ObservationDetail, ...],
    *,
    latency_ms: int,
    sandbox_count: int = 0,
) -> RoutingFacts:
    """Extract one Turn's public tool observations into routing evidence."""
    counts: dict[str, int] = {}
    max_depth = 0
    prompt_chars = 0
    child_iterations = 0
    depth_fallback_count = 0
    for detail in details:
        if isinstance(detail, ToolStarted):
            counts[detail.tool_name] = counts.get(detail.tool_name, 0) + 1
            input_value = detail.input
            if isinstance(input_value, Mapping):
                raw_prompt_chars = input_value.get("prompt_chars", 0)
                prompt_chars = max(
                    prompt_chars, int(raw_prompt_chars) if isinstance(raw_prompt_chars, (int, str)) else 0
                )
        elif isinstance(detail, (ToolCompleted, ToolFailed)):
            output = getattr(detail, "output", None) if isinstance(detail, ToolCompleted) else None
            if isinstance(output, Mapping):
                depth = output.get("recursive_depth")
                if isinstance(depth, int) and not isinstance(depth, bool):
                    max_depth = max(max_depth, depth)
                child_iterations += int(output.get("child_iterations", 0) or 0)
                if output.get("termination_mode") == "depth_fallback":
                    depth_fallback_count += 1
    native_child_count = max_depth > 0
    return RoutingFacts(
        tool_counts=counts,
        native_child_count=int(native_child_count),
        max_native_child_depth=max_depth,
        depth_fallback_count=depth_fallback_count,
        child_iterations=child_iterations,
        recursive_prompt_chars=prompt_chars,
        latency_ms=latency_ms,
        sandbox_count=sandbox_count,
        total_tool_calls=sum(counts.values()),
    )


def score_routing_execution(
    scenario: RoutingScenario,
    *,
    answer: str,
    facts: RoutingFacts,
    run_index: int = 1,
    allow_contains: bool = False,
) -> RoutingScore:
    """Score answer quality separately from routing efficiency."""
    observed = classify_routing_facts(facts)
    normalized_answer = " ".join(answer.strip().lower().split())
    expected_answer = " ".join(scenario.expected_answer.strip().lower().split())
    answer_correct = expected_answer in normalized_answer if allow_contains else normalized_answer == expected_answer
    answer_sha256 = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    match = observed == scenario.expected_route
    efficiency = 1.0 if match else 0.0
    if not answer_correct:
        rationale = "answer_mismatch"
    elif not match:
        rationale = f"routing_mismatch: expected {scenario.expected_route}, observed {observed}"
    else:
        rationale = "quality_and_route_match"
    return RoutingScore(
        scenario=scenario.name,
        expected_route=scenario.expected_route,
        observed_route=observed,
        run_index=run_index,
        answer_sha256=answer_sha256,
        answer_correct=answer_correct,
        routing_match=match,
        routing_efficiency=efficiency,
        facts=facts,
        rationale=rationale,
    )


def summarize_scores(scores: tuple[RoutingScore, ...]) -> dict[str, object]:
    """Return a small repeatable benchmark summary without prompt/body retention."""
    if not scores:
        raise ValueError("routing evaluation requires at least one score")
    return {
        "scenario_count": len(scores),
        "expected_routes": [score.expected_route for score in scores],
        "run_indexes": [score.run_index for score in scores],
        "route_order": list(_EXPECTED_ORDER),
        "answer_correct_count": sum(score.answer_correct for score in scores),
        "routing_match_count": sum(score.routing_match for score in scores),
        "routing_efficiency": round(sum(score.routing_efficiency for score in scores) / len(scores), 4),
        "answers_satisfied": round(sum(score.answer_correct for score in scores) / len(scores), 4),
        "max_latency_ms": max(score.facts.latency_ms for score in scores),
        "max_recursive_prompt_chars": max(score.facts.recursive_prompt_chars for score in scores),
        "max_child_iterations": max(score.facts.child_iterations for score in scores),
        "max_sandbox_count": max(score.facts.sandbox_count for score in scores),
        "detail_tree": routing_decision_tree(),
    }


class _RoutingScenarioSignature(dspy.Signature):
    """Run one bounded routing scenario without any private model trace capture."""

    prompt: str = dspy.InputField()
    answer: str = dspy.OutputField()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def run_routing_scenario(
    scenario: RoutingScenario,
    *,
    root_lm: Any,
    sub_lm: Any,
    root_interpreter_factory: Callable[[], Any] | Callable[[], Awaitable[Any]],
    child_runtime_factory: Callable[[int], Any],
    recursion_enabled: bool = True,
    repeats: int = 1,
    deadline_seconds: float = 120.0,
) -> tuple[RoutingScore, ...]:
    """Run one scenario in an isolated harness, using caller-owned runtimes.

    No Session or Turn persistence is touched. The caller supplies the same
    native interpreter/runtime contracts used by product execution; evidence
    remains bounded public Tool/child facts. This is the opt-in provider lane:
    normal deterministic tests use fake LMs/interpreters while live settings
    may pass provider-backed implementations.
    """
    if repeats < 1 or repeats > 32:
        raise ValueError("routing evaluation repeats must be between 1 and 32")
    scenarios: list[RoutingScore] = []
    for _ in range(repeats):
        captured = []
        started = time.perf_counter()
        deadline = time.monotonic() + deadline_seconds
        created_children = 0

        def tracked_child_factory(call_index: int) -> Any:
            nonlocal created_children
            created_children += 1
            return child_runtime_factory(call_index)

        recursive = RecursiveRLMExecutor(
            models=RLMModelBundle(root_lm=root_lm, sub_lm=sub_lm),
            options=RecursiveRLMOptions(enabled=recursion_enabled),
            child_runtime_factory=tracked_child_factory,
            deadline=deadline,
            observer=captured.append,
        )
        signature = root_signature_for_recursion(_RoutingScenarioSignature, recursion_enabled=recursion_enabled)
        tools = (recursive.tool,) if recursion_enabled else None
        rlm = build_native_rlm(signature=signature, options=RLMOptions(), tools=tools, verbose=False)
        root_interpreter = await _maybe_await(root_interpreter_factory())
        try:
            bind_observer = getattr(root_interpreter, "bind_observer", None)
            if callable(bind_observer):
                bind_observer(captured.append, max_chars=2_000)
            with dspy.context(lm=root_lm, adapter=dspy.JSONAdapter(), track_usage=True):
                prediction = await rlm.acall(root_interpreter, prompt=scenario.prompt)
        finally:
            shutdown = getattr(root_interpreter, "shutdown", None)
            if callable(shutdown):
                shutdown()
        details = tuple(cast(ObservationDetail, detail) for detail in captured if isinstance(detail, ObservationDetail))
        details_facts = facts_from_execution_details(details, latency_ms=int((time.perf_counter() - started) * 1000))
        counts = dict(details_facts.tool_counts)
        if recursive.summary().call_count:
            counts["rlm_query"] = counts.get("rlm_query", 0) + recursive.summary().call_count
        facts = RoutingFacts(
            tool_counts=counts,
            native_child_count=recursive.summary().call_count - recursive.summary().depth_fallback_count,
            max_native_child_depth=1
            if recursive.summary().call_count > recursive.summary().depth_fallback_count
            else 0,
            depth_fallback_count=recursive.summary().depth_fallback_count,
            child_iterations=recursive.summary().child_iterations,
            recursive_prompt_chars=recursive.summary().maximum_prompt_chars,
            latency_ms=details_facts.latency_ms,
            sandbox_count=created_children,
        )
        scenarios.append(
            score_routing_execution(
                scenario,
                answer=str(getattr(prediction, "answer", "")),
                facts=facts,
                run_index=len(scenarios) + 1,
                allow_contains=True,
            )
        )
    return tuple(scenarios)


__all__ = [
    "CURATED_ROUTING_SCENARIOS",
    "RoutingClass",
    "RoutingFacts",
    "RoutingScenario",
    "RoutingScore",
    "classify_routing_facts",
    "facts_from_execution_details",
    "facts_from_recursive_summary",
    "routing_decision_tree",
    "run_routing_scenario",
    "score_routing_execution",
    "summarize_scores",
]
