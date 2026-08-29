"""Public-evidence RLM routing evaluation contracts."""

from __future__ import annotations

import time

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.optimization.routing import (
    CURATED_ROUTING_SCENARIOS,
    RoutingFacts,
    RoutingScenario,
    classify_routing_facts,
    facts_from_execution_details,
    facts_from_recursive_summary,
    routing_decision_tree,
    run_routing_scenario,
    score_routing_execution,
    summarize_scores,
)
from fleet_rlm.rlm.events import ToolCompleted, ToolStarted
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import (
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
)


def test_curated_scenarios_cover_the_six_owned_routes() -> None:
    assert [scenario.expected_route for scenario in CURATED_ROUTING_SCENARIOS] == [
        "python_native",
        "semantic_single",
        "semantic_batched",
        "recursive_child",
        "recursive_depth_fallback",
        "recursive_batch",
    ]
    assert all(scenario.prompt for scenario in CURATED_ROUTING_SCENARIOS)
    assert "Python/REPL code" in routing_decision_tree()
    assert "lm" in routing_decision_tree().lower()


def test_routing_classifier_maps_each_public_route_shape() -> None:
    assert classify_routing_facts(RoutingFacts()) == "python_native"
    assert classify_routing_facts(RoutingFacts(tool_counts={"llm_query": 1})) == "semantic_single"
    assert classify_routing_facts(RoutingFacts(tool_counts={"llm_query_batched": 1})) == "semantic_batched"
    assert classify_routing_facts(RoutingFacts(native_child_count=2, max_native_child_depth=1)) == "recursive_child"
    assert classify_routing_facts(RoutingFacts(recursive_batch_calls=1)) == "recursive_batch"
    assert classify_routing_facts(RoutingFacts(depth_fallback_count=1)) == "recursive_depth_fallback"


def test_scoring_separates_final_answer_quality_from_routing_efficiency() -> None:
    scenario = RoutingScenario("semantic case", "judge one bounded label", "semantic_single", "positive")

    expensive_but_correct = score_routing_execution(
        scenario,
        answer="positive",
        facts=RoutingFacts(tool_counts={"rlm_query": 1}, native_child_count=1),
    )

    assert expensive_but_correct.answer_correct is True
    assert expensive_but_correct.routing_match is False
    assert expensive_but_correct.routing_efficiency == 0.0
    assert "routing_mismatch" in expensive_but_correct.rationale

    summary = summarize_scores((expensive_but_correct,))
    assert summary["answer_correct_count"] == 1
    assert summary["routing_match_count"] == 0


def test_public_execution_details_feed_the_classifier() -> None:
    details = (
        ToolStarted("call-1", "rlm_query", {"prompt_count": 1, "prompt_chars": 42}),
        ToolCompleted(
            "call-1",
            "rlm_query",
            {"status": "completed", "recursive_depth": 1, "child_iterations": 2, "termination_mode": "typed_submit"},
        ),
    )

    facts = facts_from_execution_details(details, latency_ms=12, sandbox_count=1)

    assert classify_routing_facts(facts) == "recursive_child"
    assert facts.max_native_child_depth == 1
    assert facts.child_iterations == 2
    assert facts.recursive_prompt_chars == 42
    assert facts.sandbox_count == 1


def test_public_execution_details_capture_recursive_batch_width() -> None:
    details = (
        ToolStarted("call-1", "rlm_query_batched", {"prompt_count": 3, "prompt_chars": 42}),
        ToolCompleted(
            "call-1",
            "rlm_query_batched",
            {"status": "completed", "answer_count": 3, "peak_child_concurrency": 2},
        ),
    )

    facts = facts_from_execution_details(details, latency_ms=12, sandbox_count=3)

    assert classify_routing_facts(facts) == "recursive_batch"
    assert facts.recursive_batch_calls == 1
    assert facts.peak_child_concurrency == 2


def test_live_scoring_supports_normalized_containment_and_run_indexes() -> None:
    scenario = RoutingScenario("semantic case", "judge one label", "semantic_single", "positive")

    score = score_routing_execution(
        scenario,
        answer="The judgment is positive, with qualification.",
        facts=RoutingFacts(tool_counts={"llm_query": 1}),
        run_index=3,
        allow_contains=True,
    )

    assert score.run_index == 3
    assert score.answer_correct is True
    assert len(score.answer_sha256) == 64
    assert "positive" not in score.answer_sha256
    summary = summarize_scores((score,))
    assert summary["expected_routes"] == ["semantic_single"]
    assert summary["run_indexes"] == [3]


def test_native_child_to_sub_lm_fallback_has_no_second_sandbox() -> None:
    """The deterministic harness lane proves the fixed boundary with no provider."""
    adapter = dspy.JSONAdapter()
    created: list[DaytonaCodeInterpreter] = []

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        created.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"routing-child-{call_index}",
            "routing-test-volume",
            f"recursive/routing/test/{call_index}",
            interpreter.shutdown,
        )

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM(
                [
                    {"reasoning": "delegate from child", "code": "inner = rlm_query(prompt='inner slice')"},
                    {"reasoning": "complete child", "code": "SUBMIT(answer=inner)"},
                ],
                adapter=adapter,
            ),
            dspy.utils.DummyLM([{"answer": "fallback element"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 30,
    )

    assert executor.tool(prompt="outer recursive classification") == "fallback element"
    facts = facts_from_recursive_summary(
        executor.summary(),
        latency_ms=1,
        tool_counts={"rlm_query": 2},
        sandbox_count=len(created),
    )

    assert len(created) == 1
    assert facts.depth_fallback_count == 1
    assert facts.native_child_count == 1
    assert facts.max_native_child_depth == 1
    assert classify_routing_facts(facts) == "recursive_depth_fallback"
    assert created[0]._shutdown


@pytest.mark.asyncio
async def test_harness_runs_an_isolated_fake_lm_python_scenario() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "compute in Python", "code": "value = 18434 + 92786\nSUBMIT(answer=str(value))"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    child_calls = 0

    def child_factory(call_index: int):
        nonlocal child_calls
        child_calls += 1
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        return ChildRuntimeLease(
            interpreter,
            f"routing-child-{call_index}",
            "routing-test-volume",
            f"recursive/routing/scenario/{call_index}",
            interpreter.shutdown,
        )

    scenario = next(item for item in CURATED_ROUTING_SCENARIOS if item.expected_route == "python_native")
    scores = await run_routing_scenario(
        scenario,
        root_lm=root,
        sub_lm=sub,
        root_interpreter_factory=lambda: DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        child_runtime_factory=child_factory,
    )

    assert len(scores) == 1
    assert scores[0].answer_correct is True
    assert scores[0].routing_match is True
    assert classify_routing_facts(scores[0].facts) == "python_native"
    assert child_calls == 0
    assert scores[0].facts.sandbox_count == 0


@pytest.mark.asyncio
async def test_harness_routes_one_native_semantic_call_to_configured_sub_lm() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {
                "reasoning": "one bounded semantic judgment",
                "code": "judgment = llm_query('Is photosynthesis a biological process?')\nSUBMIT(answer=judgment)",
            }
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "yes"}], adapter=adapter)
    scenario = next(item for item in CURATED_ROUTING_SCENARIOS if item.expected_route == "semantic_single")

    def unexpected_child(_call_index: int) -> None:
        raise AssertionError("semantic routing must not allocate a child runtime")

    scores = await run_routing_scenario(
        scenario,
        root_lm=root,
        sub_lm=sub,
        root_interpreter_factory=lambda: DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        child_runtime_factory=unexpected_child,
    )

    assert scores[0].answer_correct is True
    assert scores[0].routing_match is True
    assert scores[0].facts.tool_counts["llm_query"] == 1
    assert len(sub.history) == 1


@pytest.mark.asyncio
async def test_harness_routes_native_semantic_batch_to_configured_sub_lm() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {
                "reasoning": "independent bounded semantic judgments",
                "code": (
                    "labels = llm_query_batched(['ballast', 'stroll', 'cobalt'])\nSUBMIT(answer='/'.join(labels))"
                ),
            }
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM(
        {
            "ballast": {"answer": "object"},
            "stroll": {"answer": "action"},
            "cobalt": {"answer": "color"},
        },
        adapter=adapter,
    )
    scenario = next(item for item in CURATED_ROUTING_SCENARIOS if item.expected_route == "semantic_batched")

    def unexpected_child(_call_index: int) -> None:
        raise AssertionError("semantic routing must not allocate a child runtime")

    scores = await run_routing_scenario(
        scenario,
        root_lm=root,
        sub_lm=sub,
        root_interpreter_factory=lambda: DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        child_runtime_factory=unexpected_child,
    )

    assert scores[0].answer_correct is True
    assert scores[0].routing_match is True
    assert scores[0].facts.tool_counts["llm_query_batched"] == 1
    assert len(sub.history) == 3


def test_tracking_child_factory_forwards_optional_factory_cleanup() -> None:
    """The routing harness forwards factory-owned cleanup hooks it does not use."""

    from fleet_rlm.optimization.routing import _TrackingChildRuntimeFactory

    class _FakeLease:
        pass

    class _Factory:
        def __init__(self) -> None:
            self.created: list[int] = []
            self.waited = 0
            self.raised = 0

        def __call__(self, call_index: int) -> _FakeLease:
            self.created.append(call_index)
            return _FakeLease()

        def wait_owned(self) -> None:
            self.waited += 1

        def raise_if_cleanup_failed(self) -> None:
            self.raised += 1

    factory = _Factory()
    tracked = _TrackingChildRuntimeFactory(factory)
    assert isinstance(tracked(3), _FakeLease)
    tracked.wait_owned()
    tracked.raise_if_cleanup_failed()
    assert tracked.created == 1
    assert factory.created == [3]
    assert factory.waited == 1
    assert factory.raised == 1

    plain_tracked = _TrackingChildRuntimeFactory(lambda _index: _FakeLease())
    plain_tracked.wait_owned()
    plain_tracked.raise_if_cleanup_failed()
