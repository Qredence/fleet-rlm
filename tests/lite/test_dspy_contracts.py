"""Exercise the installed DSPy contracts without provider or Daytona calls."""

from __future__ import annotations

from types import SimpleNamespace

import dspy
import pytest
from dspy.primitives.facade import SandboxLM

from fleet_rlm.dspy_support import decision_evidence, make_choice_type
from fleet_rlm.engine.signatures import TRIAGE_ROUTE
from fleet_rlm.memory_and_eval import AdaptiveFleetEngine
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


def manager():
    replies = []
    counts = []
    return SimpleNamespace(
        consume_budget=lambda n=1: counts.append(n),
        check_turn=lambda: None,
        sub_lm=lambda prompt: [prompt.upper()],
        replies=replies,
        counts=counts,
    )


@pytest.mark.parametrize("batched", [False, True])
def test_injected_query_enforces_local_budget_and_charges_global_once(batched):
    m = manager()
    rlm = dspy.RLM("task -> answer", max_llm_calls=2, sub_lm=SandboxLM(m.sub_lm, m.consume_budget))
    interpreter = DaytonaInterpreter(m)
    rlm._inject_execution_context(interpreter, rlm._prepare_execution_tools())
    name = "llm_query_batched" if batched else "llm_query"
    args = [["first", "second"]] if batched else ["first"]
    m.replies.append(interpreter._handle_host_call({"id": "first", "tool": name, "args": args}))
    if not batched:
        m.replies.append(interpreter._handle_host_call({"id": "second", "tool": name, "args": ["second"]}))
    assert sum(m.counts) == 2
    m.replies.append(interpreter._handle_host_call({"id": "exhausted", "tool": name, "args": args}))
    assert "error" in m.replies[-1]
    assert sum(m.counts) == 2
    assert m.replies[0]["value"] == (["FIRST", "SECOND"] if batched else "FIRST")


@pytest.mark.parametrize("batched", [False, True])
def test_query_fallback_keeps_global_accounting(batched):
    m = manager()
    interpreter = DaytonaInterpreter(m)
    m.replies.append(
        interpreter._handle_host_call(
            {
                "id": "one",
                "tool": "llm_query_batched" if batched else "llm_query",
                "args": [["a", "b"]] if batched else ["a"],
            }
        )
    )
    assert sum(m.counts) == (2 if batched else 1)
    assert "error" not in m.replies[-1]


def test_multiple_completions_reserve_each_distinct_budget_once():
    calls = []
    global_count = []
    local_count = []
    lm = SandboxLM(
        SandboxLM(lambda *_args, **kw: calls.append(kw) or ["answer"] * kw["n"], global_count.append),
        local_count.append,
    )
    assert lm("question", n=3) == ["answer"] * 3
    assert global_count == [3] and local_count == [3] and len(calls) == 1
    with pytest.raises(TypeError):
        lm("question", api_key="forbidden")
    assert global_count == [3]


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
def test_native_child_promotion_belongs_to_successful_host_invocation(outcome):
    events = []
    m = manager()
    child_manager = SimpleNamespace(
        collect_child_artifacts=lambda: events.append("promoted"), shutdown=lambda: events.append("closed")
    )

    def call():
        child = DaytonaInterpreter(child_manager, owns_manager=True)
        try:
            if outcome == "failed":
                raise RuntimeError("predictor failed")
            return {"answer": "ok"}
        finally:
            child.shutdown()

    if outcome == "cancelled":

        def cancelled():
            raise RuntimeError("Turn cancelled")

        m.check_turn = cancelled
    interpreter = DaytonaInterpreter(m, tools={"predictor": call})
    m.replies.append(interpreter._handle_host_call({"id": "call", "tool": "predictor", "args": []}))
    if outcome in {"failed", "cancelled"}:
        assert events == ["closed"]
        assert "error" in m.replies[-1]
    else:
        assert events == ["promoted", "closed"]
        assert "value" in m.replies[-1]


def test_unowned_explicit_child_keeps_promotion_with_its_caller():
    events = []
    m = manager()
    child_manager = SimpleNamespace(shutdown=lambda: events.append("closed"))

    def call():
        DaytonaInterpreter(child_manager, owns_manager=False).shutdown()
        return "explicit caller owns cleanup"

    interpreter = DaytonaInterpreter(m, tools={"spawn": call})
    assert interpreter._invoke_tool("spawn", [], {}) == "explicit caller owns cleanup"
    assert events == []


def test_decision_evidence_and_missing_evidence():
    choice = make_choice_type((("yes", "yes"), ("no", "no")))
    result = choice(value="yes", confidence=0.8, probabilities={"yes": 0.9, "no": 0.1})
    evidence = decision_evidence(result, SimpleNamespace(model="test-model"))
    assert evidence["probabilities"] == {"yes": 0.9, "no": 0.1}
    assert evidence["confidence"] == 0.8 and evidence["raw_choice"] == "yes"
    assert decision_evidence(None, SimpleNamespace())["confidence"] is None


def test_batch_decisions_keep_alignment_and_evidence(monkeypatch):
    import fleet_rlm.sandbox.interpreter as module

    choice = make_choice_type((("a", "a"), ("b", "b")))
    predictions = [
        SimpleNamespace(decision=choice(value=v, confidence=0.7, probabilities={"a": 0.7, "b": 0.3}))
        for v in ["a", "b"]
    ]
    monkeypatch.setattr(
        module.dspy, "Predict", lambda _signature: SimpleNamespace(batch=lambda *_args, **_kwargs: predictions)
    )
    m = manager()
    m.jev_lm = None
    results = DaytonaInterpreter(m)._run_jev_batch(["one", "two"], "choose", ["a", "b"])
    assert [(r["item"], r["decision"]) for r in results] == [("one", "a"), ("two", "b")]
    assert all(r["confidence"] == 0.7 and r["probabilities"] == {"a": 0.7, "b": 0.3} for r in results)


@pytest.mark.parametrize("backend", ["generative", "typesafe"])
@pytest.mark.parametrize(
    "context,mode,expected,reason",
    [
        (None, "hybrid", "direct", None),
        ("selected", "hybrid", "rlm", "selected_context"),
        (None, "rlm", "rlm", "session_mode"),
    ],
)
def test_routing_evidence_distinguishes_backend_and_override(backend, context, mode, expected, reason):
    events = []
    m = DaytonaSandboxManager("key", "org", "volume", "test")
    m.event_callback = lambda payload: events.append((payload["event"], payload))
    root = dspy.LM("openai/test-root", engine="lm15", cache=False)
    lm = type("TypeSafe", (), {"model": "test-jev"})() if backend == "typesafe" else None
    engine = AdaptiveFleetEngine(m, root, root, jev_lm=lm)
    engine.triage = lambda **_kwargs: SimpleNamespace(
        route=TRIAGE_ROUTE(
            value="direct",
            confidence=0.8,
            probabilities={"direct": 0.8, "flex": 0.1, "rlm": 0.1},
        )
    )
    engine.direct_cot = lambda **_kwargs: SimpleNamespace(answer="ok")
    engine.primary_rlm = lambda **_kwargs: SimpleNamespace(answer="ok")
    try:
        result = engine(workspace_manifest="", memory_summary="", task="greeting", context=context, execution_mode=mode)
        evidence = next(p["evidence"] for name, p in events if name == "triage_decision")
        assert result.route == expected and evidence["effective_route"] == expected
        assert evidence["override_reason"] == reason
        assert evidence["backend"] == backend
        assert evidence["confidence"] == (0.8 if mode == "hybrid" else None)
    finally:
        m.end_turn()


def test_routing_regression_cases_cover_each_effective_route():
    import json
    from collections import Counter
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / "fixtures/triage_cases.json").read_text())
    assert Counter(case["route"] for case in cases) == {"direct": 6, "flex": 6, "rlm": 6}
    assert len({case["id"] for case in cases}) == 18
    assert all(case["review_status"] == "developer_reviewed" and case["rationale"] for case in cases)


@pytest.mark.parametrize("finish", ["recovery", "extraction", "failure"])
def test_installed_rlm_lifecycle_promotes_only_a_valid_completed_invocation(finish):
    from dspy.primitives.code_interpreter import CodeExecutionError, FinalOutput
    from dspy.primitives.facade import SHIM_SETUP

    events = []
    child_manager = SimpleNamespace(
        collect_child_artifacts=lambda: events.append("promoted"),
        shutdown=lambda: events.append("closed"),
    )

    class ControlledInterpreter(DaytonaInterpreter):
        def start(self):
            return None

        def execute(self, code, variables=None):
            del variables
            if code == SHIM_SETUP:
                return None
            if code == "SUBMIT":
                return FinalOutput({"answer": 391})
            raise CodeExecutionError("deliberate repair probe")

    def factory():
        return ControlledInterpreter(child_manager, owns_manager=True)

    rlm = dspy.RLM("task: str -> answer: int", interpreter_factory=factory, max_iters=2 if finish == "recovery" else 1)
    actions = iter(["fail", "SUBMIT"])
    rlm.generate_action = lambda **_kwargs: dspy.Prediction(reasoning="", code=next(actions))

    def extract(**_kwargs):
        if finish == "failure":
            raise RuntimeError("extraction failed")
        return dspy.Prediction(answer=391)

    rlm.extract = extract
    parent = DaytonaInterpreter(manager(), tools={"nested": lambda: rlm(task="controlled invocation")})
    if finish == "failure":
        with pytest.raises(RuntimeError, match="extraction failed"):
            parent._invoke_tool("nested", [], {})
        assert events == ["closed"]
    else:
        result = parent._invoke_tool("nested", [], {})
        assert result.answer == 391
        assert events == ["promoted", "closed"]
        assert "deliberate repair probe" in result.trajectory[0]["output"]
        assert (result.final_reasoning == "Extract forced final output") == (finish == "extraction")
