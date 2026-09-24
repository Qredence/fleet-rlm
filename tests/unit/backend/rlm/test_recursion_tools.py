"""Recursive tool contracts and live canary capture boundaries.

* The live child-result capture helpers provide credential-free canary assertions.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.runtime import ChildRuntimeLease
from fleet_rlm.rlm.budget import BudgetDimension, BudgetLimits, TurnBudget
from fleet_rlm.rlm.events import ChildProgress, Status, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import (
    ChildRequest,
    ChildRuntimeCleanupError,
    ChildRuntimeNotStartedError,
    RecursiveRLMOptions,
    _child_progress_outcome,
)
from fleet_rlm.rlm.recursion import RecursiveRLMExecutor as ProductionRecursiveRLMExecutor
from fleet_rlm.sessions.run_state import RunAuthority
from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.tools import SkillToolHost
from tests.live.backend.test_daytona_recursive_batch import _ChildEvidence, _install_batch_answer_capture
from tests.support.recursion_scheduler import RecursiveRLMExecutor


# --- from test_recursion_tools.py -------------------------------------
def _executor(
    root_actions: list[dict[str, str]],
    *,
    sub_actions: list[dict[str, str]] | None = None,
    options: RecursiveRLMOptions | None = None,
    factory_calls: list[DaytonaCodeInterpreter] | None = None,
    observer=None,
    is_authorized: Callable[[], bool] | None = None,
    input_materializer: Callable[[ChildRequest], dict[str, bytes]] | None = None,
    result_writer: Callable[[int, str, bytes], str] | None = None,
    child_runtime_factory: Callable[..., ChildRuntimeLease] | None = None,
    parent_run_id: str | None = None,
    loaded_skills=None,
) -> RecursiveRLMExecutor:
    """
    Construct a recursive executor backed by dummy root and sub-models for tests.

    Parameters:
        root_actions (list[dict[str, str]]): Responses supplied by the root model.
        sub_actions (list[dict[str, str]] | None): Responses supplied by the sub-model.
        options (RecursiveRLMOptions | None): Executor configuration.
        factory_calls (list[DaytonaCodeInterpreter] | None): Collection to receive created child interpreters.
        observer: Optional event observer.
        is_authorized (Callable[[], bool] | None): Optional callback used to authorize recursive calls.

    Returns:
        RecursiveRLMExecutor: A configured executor with an in-process child-runtime factory.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM(sub_actions or [{"answer": "fallback"}], adapter=adapter)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        """Create a child runtime lease backed by an in-process interpreter.

        Parameters:
                call_index (int): Index used to identify the child runtime.

        Returns:
                ChildRuntimeLease: A lease for the newly created child runtime.
        """
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        if factory_calls is not None:
            factory_calls.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )

    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=child_runtime_factory or factory,
        deadline=time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
        input_materializer=input_materializer,
        result_writer=result_writer,
        parent_run_id=parent_run_id,
        loaded_skills=loaded_skills,
    )


def test_child_inherits_only_loaded_skill_guidance_and_private_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_calls

    catalog = build_bundled_skill_catalog()
    host = SkillToolHost(catalog)
    first = catalog.require(stable_skill_id("long-context"))
    second = catalog.require(stable_skill_id("workspace-files"))
    host.mark_preloaded(first)
    staged: dict[int, dict[str, bytes]] = {}
    construction: list[dict[str, object]] = []
    child_prompts: list[dict[str, object]] = []
    child_span_inputs: list[dict[str, object]] = []
    start_turn_span = recursive_calls.start_turn_span

    def capture_child_span(name: str, **kwargs: object):
        if name == "RLM.child.invoke":
            inputs = kwargs.get("inputs")
            assert isinstance(inputs, dict)
            child_span_inputs.append(inputs)
        return start_turn_span(name, **kwargs)

    class Child:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            payload = json.loads(prompt)
            child_prompts.append(payload)
            return dspy.Prediction(answer=payload["task"], evidence=[], gaps=[], result_files=[], trajectory=[])

    def build(**kwargs: object) -> Child:
        construction.append(kwargs)
        return Child()

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        assert profile == "semantic-child"
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        return ChildRuntimeLease(
            interpreter,
            f"skill-child-{call_index}",
            "",
            "",
            interpreter.shutdown,
            _stage_files=lambda files: staged.setdefault(call_index, dict(files)),
        )

    monkeypatch.setattr(recursive_calls, "build_native_rlm", build)
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)
    monkeypatch.setattr(recursive_calls, "start_turn_span", capture_child_span)
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        options=RecursiveRLMOptions(max_calls=2),
        child_runtime_factory=factory,
        loaded_skills=host.loaded_definitions,
        input_materializer=lambda request: {"data/report.csv": b"selected-source"} if request.inputs else {},
    )

    first_result = executor.tool(task="first", inputs=["data/report.csv"])
    assert first_result["status"] == "completed"
    assert construction[0]["skill_instructions"] == (first.instructions,)
    assert construction[0]["tools"] == []
    expected_first_files = {
        "data/report.csv": b"selected-source",
        **{
            f"skills/{first.card.name}/{resource.path}": resource.content.encode("utf-8")
            for resource in first.resources.values()
        },
    }
    assert staged[1] == expected_first_files
    expected_manifest = [
        {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(expected_first_files.items())
    ]
    assert child_prompts[0]["source_manifest"] == expected_manifest
    encoded_manifest = json.dumps(expected_manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert first_result["source_manifest_sha256"] == hashlib.sha256(encoded_manifest.encode("utf-8")).hexdigest()

    assert host.load_skill(str(second.card.id), second.card.version)["ok"] is True
    assert executor.tool(task="second", inputs=[])["status"] == "completed"
    assert set(construction[1]["skill_instructions"]) == {first.instructions, second.instructions}
    assert "publish_workspace_artifact" in second.instructions
    assert construction[1]["tools"] == []
    assert [span["skill_versions"] for span in child_span_inputs] == [
        [f"{first.card.id}:{first.card.version}"],
        [
            f"{first.card.id}:{first.card.version}",
            f"{second.card.id}:{second.card.version}",
        ],
    ]
    assert set(staged[2]) == {
        f"skills/{skill.card.name}/{resource.path}"
        for skill in (first, second)
        for resource in skill.resources.values()
    }
    assert staged[1] != staged[2]
    executor.wait_owned()


def test_native_child_depth_is_a_fixed_invariant_not_an_options_surface() -> None:
    import dataclasses
    import inspect

    # The public composition surface accepts no recursion depth setting;
    # depth is proven through the child's recursive completion evidence and
    # the absence of any grandchild allocation (see the depth-cap lane).
    parameters = set(inspect.signature(RecursiveRLMOptions).parameters)
    assert "max_depth" not in parameters
    assert not any("depth" in field.name for field in dataclasses.fields(RecursiveRLMOptions()))
    with pytest.raises(TypeError, match="max_depth"):
        RecursiveRLMOptions(max_depth=2)  # type: ignore[call-arg]

    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []
    executor = _executor(
        [
            {"reasoning": "semantic judgment", "code": "inner = llm_query('inner slice')"},
            {"reasoning": "submit child", "code": "SUBMIT(answer=inner, evidence=[], gaps=[], result_files=[])"},
        ],
        sub_actions=[{"answer": "fallback-answer"}],
        factory_calls=created,
        observer=events.append,
    )

    outcome = executor.tool(task="outer slice", inputs=[])
    assert outcome["status"] == "completed"
    assert "fallback-answer" in outcome["answer"]
    # Native semantic work never allocates another Fleet child.
    assert len(created) == 1
    completed = [event for event in events if isinstance(event, ToolCompleted)]
    depths = [event.output["recursive_depth"] for event in completed]
    assert depths == [1]
    assert executor.summary().call_count == 1


def test_recursive_tool_runs_fresh_native_child_and_redacts_observation() -> None:
    events = []
    created: list[DaytonaCodeInterpreter] = []
    parent_run_id = str(uuid4())
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok', evidence=[], gaps=[], result_files=[])"}],
        factory_calls=created,
        observer=events.append,
        parent_run_id=parent_run_id,
    )

    result = executor.tool(task="classify selected row", inputs=[])

    assert result["answer"] == "child-ok"
    assert len(created) == 1
    assert created[0]._shutdown
    assert executor.summary().call_count == 1
    assert executor.summary().child_iterations == 1
    started = next(event for event in events if isinstance(event, ToolStarted))
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert started.input == {"input_count": 0}
    assert completed.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event, Status)]
    assert [event.status for event in statuses] == ["child_started", "child_completed"]
    child_progress = [event for event in events if isinstance(event, ChildProgress)]
    assert child_progress[-1].child_id == "child-1"
    assert child_progress[-1].parent_run_id == parent_run_id
    assert child_progress[-1].outcome == "child-ok"
    assert child_progress[-1].code_excerpt == "SUBMIT(answer='child-ok', evidence=[], gaps=[], result_files=[])"
    assert child_progress[-1].output_excerpt == "FINAL submitted"
    assert statuses[0].message == "call_index=1 recursive_depth=1"
    assert statuses[1].message is not None
    assert "duration_ms=" in statuses[1].message
    assert "cleanup_status=completed" in statuses[1].message
    assert "child-ok" not in repr(statuses)


def test_child_progress_outcome_is_bounded_and_has_an_explicit_fallback() -> None:
    long_answer = "Useful finding " * 40

    excerpt = _child_progress_outcome(long_answer, None)

    assert excerpt.startswith("Useful finding Useful finding")
    assert len(excerpt) == 240
    assert excerpt.endswith("...")
    assert _child_progress_outcome("  \n ", None) == "Child answer unavailable"
    assert _child_progress_outcome(None, "capacity") == "capacity"


def test_child_capacity_refusal_is_not_reported_as_a_started_timeout() -> None:
    events: list[object] = []

    def refuse(_call_index: int, *, profile: str) -> ChildRuntimeLease:
        del profile
        raise ChildRuntimeNotStartedError("child capacity is unavailable")

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        observer=events.append,
        child_runtime_factory=refuse,
    )

    outcome = executor.tool(task="inspect selected records", inputs=[])

    assert outcome["status"] == "not_started"
    assert outcome["error_category"] == "capacity"
    assert executor.summary().recursive_children_started == 0
    progress = [event for event in events if isinstance(event, ChildProgress)]
    assert len(progress) == 1
    assert progress[0].state == "not_started"
    assert progress[0].cleanup_state == "not_required"
    statuses = [event for event in events if isinstance(event, Status)]
    assert [event.status for event in statuses] == ["child_not_started"]


def test_capacity_refusal_releases_shared_started_child_budget() -> None:
    deadline = time.monotonic() + 30
    budget = TurnBudget(deadline=deadline, limits=BudgetLimits(recursive_children=1, tool_calls=2))
    lm = dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter())

    def refuse(_call_index: int, *, profile: str) -> ChildRuntimeLease:
        assert profile == "semantic-child"
        raise ChildRuntimeNotStartedError("capacity unavailable")

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(lm, lm, budget=budget),
        options=RecursiveRLMOptions(max_calls=2),
        child_runtime_factory=refuse,
        deadline=deadline,
    )
    assert executor.tool(task="first", inputs=[])["status"] == "not_started"
    assert executor.tool(task="second", inputs=[])["status"] == "not_started"
    assert budget.snapshot()[BudgetDimension.RECURSIVE_CHILDREN.value] == 0
    assert budget.snapshot()[BudgetDimension.TOOL_CALLS.value] == 2
    executor.wait_owned()


@pytest.mark.parametrize("writer_fails", [False, True])
def test_child_result_files_are_persisted_before_cleanup_and_never_fabricated(
    monkeypatch: pytest.MonkeyPatch,
    writer_fails: bool,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_calls

    child_closed = False
    persisted: dict[str, bytes] = {}
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    class Child:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            assert json.loads(prompt)["task"] == "write a structured summary"
            return dspy.Prediction(
                answer="summary ready",
                evidence=["records.jsonl:line 12"],
                gaps=[],
                result_files=["summary.json"],
                trajectory=[],
            )

    def read_result_files(paths: list[str] | tuple[str, ...]) -> dict[str, bytes]:
        assert not child_closed
        return {path: b'{"count":12}' for path in paths}

    def close() -> None:
        nonlocal child_closed
        child_closed = True
        interpreter.shutdown()

    lease = ChildRuntimeLease(
        interpreter,
        "result-child",
        "",
        "",
        close,
        _stage_files=lambda _files: None,
        _read_result_files=read_result_files,
    )

    def persist(call_index: int, path: str, content: bytes) -> str:
        assert not child_closed
        assert call_index == 1
        assert path == "summary.json"
        if writer_fails:
            raise OSError("parent Run result persistence failed")
        persisted[path] = content
        return f"run/children/{call_index}/{path}"

    observed: list[object] = []
    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        child_runtime_factory=lambda _index, *, profile: lease,  # noqa: ARG005
        result_writer=persist,
        observer=observed.append,
    )

    outcome = executor.tool(task="write a structured summary", inputs=[])

    assert child_closed
    progress = [item for item in observed if isinstance(item, ChildProgress)]
    assert progress[-1].result_file_count == (0 if writer_fails else 1)
    if writer_fails:
        assert outcome["status"] == "failed"
        assert outcome["result_files"] == []
        assert persisted == {}
    else:
        assert outcome["status"] == "completed"
        assert outcome["result_files"] == ["run/children/1/summary.json"]
        assert persisted == {"summary.json": b'{"count":12}'}


@pytest.mark.asyncio
async def test_recursive_tool_awaits_native_child_from_an_active_event_loop() -> None:
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok', evidence=[], gaps=[], result_files=[])"}],
    )

    assert executor.tool(task="classify selected row", inputs=[])["answer"] == "child-ok"


def test_child_uses_native_semantic_calls_without_new_interpreter() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [
            {"reasoning": "semantic judgment", "code": "inner = llm_query('inner slice')"},
            {"reasoning": "submit child", "code": "SUBMIT(answer=inner, evidence=[], gaps=[], result_files=[])"},
        ],
        sub_actions=[{"answer": "fallback-answer"}],
        factory_calls=created,
    )

    outcome = executor.tool(task="outer slice", inputs=[])
    assert outcome["status"] == "completed"
    assert "fallback-answer" in outcome["answer"]
    assert len(created) == 1
    assert executor.summary().call_count == 1


@pytest.mark.parametrize(
    ("prompt", "message"),
    [("", "at least 1 character"), ("x" * 11, "prompt bound")],
)
def test_recursive_tool_rejects_invalid_prompt_before_child_creation(prompt: str, message: str) -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused', evidence=[], gaps=[], result_files=[])"}],
        options=RecursiveRLMOptions(max_prompt_chars=10),
        factory_calls=created,
    )

    with pytest.raises(ValueError, match=message):
        executor.tool(task=prompt, inputs=[])
    assert created == []


def test_recursive_batched_tool_rejects_oversized_prompt_before_reservation() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused', evidence=[], gaps=[], result_files=[])"}],
        options=RecursiveRLMOptions(max_prompt_chars=10),
        factory_calls=created,
    )

    with pytest.raises(ValueError, match="prompt bound"):
        executor.batched_tool(tasks=[{"task": "x"}])
    assert created == []


def test_recursive_tool_applies_child_output_limit_in_characters() -> None:
    answer = "é" * 20
    executor = _executor(
        [{"reasoning": "submit", "code": f"SUBMIT(answer='{answer}', evidence=[], gaps=[], result_files=[])"}],
        # The serialized result is within the character budget while its
        # UTF-8 representation is larger; the bound is intentionally chars.
        options=RecursiveRLMOptions(child_max_output_chars=256),
    )

    assert executor.tool(task="unicode answer", inputs=[])["answer"] == answer


def test_recursive_tool_enforces_shared_call_budget() -> None:
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='ok', evidence=[], gaps=[], result_files=[])"}],
        options=RecursiveRLMOptions(max_calls=1),
    )

    assert executor.tool(task="first", inputs=[])["answer"] == "ok"
    assert executor.tool(task="second", inputs=[])["status"] == "failed"
    assert executor.summary().call_count == 1


def test_recursive_batched_tool_preserves_order_and_bounds_child_concurrency() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        {
            "FANOUT-A": {"reasoning": "a", "code": "SUBMIT(answer='A', evidence=[], gaps=[], result_files=[])"},
            "FANOUT-B": {"reasoning": "b", "code": "SUBMIT(answer='B', evidence=[], gaps=[], result_files=[])"},
            "FANOUT-C": {"reasoning": "c", "code": "SUBMIT(answer='C', evidence=[], gaps=[], result_files=[])"},
        },
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        created.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"batch-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=2),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 30,
        observer=events.append,
    )

    outcomes = executor.batched_tool(tasks=[{"task": task} for task in ["FANOUT-A", "FANOUT-B", "FANOUT-C"]])
    assert [item["answer"] for item in outcomes] == ["A", "B", "C"]
    summary = executor.summary()
    assert summary.recursive_batch_calls == 1
    assert summary.recursive_children_started == 3
    assert summary.recursive_children_completed == 3
    assert 1 <= summary.peak_child_concurrency <= 2
    assert summary.delegation_metrics.child_root_lm_calls_depth_1 == 3
    assert all(interpreter._shutdown for interpreter in created)
    batch_completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert batch_completed.output["answer_count"] == 3
    assert 1 <= batch_completed.output["peak_child_concurrency"] <= 2


def test_recursive_batch_resolves_inputs_before_workers_and_starts_child_spans_in_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    import fleet_rlm.rlm.recursion as recursive_calls

    parent_thread = threading.get_ident()
    span_threads: list[tuple[str, int]] = []

    class Span:
        def finish(self, **_kwargs: object) -> None:
            return None

    def start_span(name: str, **_kwargs: object) -> Span:
        span_threads.append((name, threading.get_ident()))
        return Span()

    monkeypatch.setattr(recursive_calls, "start_turn_span", start_span)
    executor = _executor(
        {
            "first": {"reasoning": "first", "code": "SUBMIT(answer='first', evidence=[], gaps=[], result_files=[])"},
            "second": {"reasoning": "second", "code": "SUBMIT(answer='second', evidence=[], gaps=[], result_files=[])"},
        },  # type: ignore[arg-type]
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
    )

    outcomes = executor.batched_tool(tasks=[{"task": task} for task in ["first", "second"]])
    assert [item["answer"] for item in outcomes] == ["first", "second"]
    assert len(span_threads) >= 2
    assert any(name == "RLM.child.resolve_inputs" and thread_id == parent_thread for name, thread_id in span_threads)
    assert all(thread_id != parent_thread for name, thread_id in span_threads if name != "RLM.child.resolve_inputs")


def test_recursive_batch_join_stops_at_turn_deadline_and_worker_retains_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    import fleet_rlm.rlm.recursion as recursive_calls

    release = threading.Event()
    started = threading.Event()
    closed = threading.Event()

    class Child:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            del prompt
            started.set()
            release.wait(1)
            return dspy.Prediction(answer="late", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            interpreter.shutdown()
            closed.set()

        return ChildRuntimeLease(
            interpreter,
            f"deadline-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )

    adapter = dspy.JSONAdapter()
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(max_calls=1, max_parallel_children=1),
        child_runtime_factory=factory,
        # Leave scheduler slack when this ownership-boundary test runs under
        # the full xdist suite; the child still blocks until the deadline.
        deadline=time.monotonic() + 0.5,
    )

    began = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="batch deadline exceeded"):
            executor.batched_tool(tasks=[{"task": task} for task in ["blocked"]])
        assert time.monotonic() - began < 1.0
        assert started.is_set()
        assert not closed.is_set()
        with pytest.raises(RuntimeError, match="cleanup is still pending"):
            executor.raise_if_cleanup_failed()
    finally:
        release.set()

    assert closed.wait(1)
    executor.wait_owned()
    executor.raise_if_cleanup_failed()


def test_recursive_batched_tool_reserves_the_shared_budget_atomically() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused', evidence=[], gaps=[], result_files=[])"}],
        options=RecursiveRLMOptions(max_calls=2),
        factory_calls=created,
    )

    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(tasks=[{"task": task} for task in ["first", "second", "third"]])
    assert created == []
    assert executor.summary().call_count == 0


def test_recursive_tool_rejects_revoked_authority_before_child_creation() -> None:
    authority = RunAuthority()
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused', evidence=[], gaps=[], result_files=[])"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=lambda: not authority.revoked,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(task="late child request", inputs=[])

    assert created == []
    assert [type(event) for event in events] == [ToolStarted, ToolFailed]


def test_recursive_tool_rechecks_authority_before_child_allocation() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determines whether an authorization check is permitted.

        Returns:
                bool: `true` for the first check, `false` for subsequent checks.
        """
        nonlocal checks
        checks += 1
        return checks == 1

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused', evidence=[], gaps=[], result_files=[])"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(task="revoked before allocation", inputs=[])

    assert created == []
    assert [event.status for event in events if isinstance(event, Status)] == []


def test_recursive_tool_closes_lease_when_authority_is_revoked_after_acquisition() -> None:
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """Revoke on actual allocation, independently of check count."""
        return not created

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused', evidence=[], gaps=[], result_files=[])"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(task="revoked after acquisition", inputs=[])

    assert len(created) == 1
    assert created[0]._shutdown
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message
    assert "cleanup_status=completed" in failed[0].message


def test_recursive_tool_discards_result_when_authority_is_revoked_after_execution() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determines whether authorization remains valid for the next check.

        Returns:
                bool: `True` for the first six checks and `False` thereafter.
        """
        nonlocal checks
        checks += 1
        return checks < 7

    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-secret', evidence=[], gaps=[], result_files=[])"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(task="revoked after execution", inputs=[])

    assert len(created) == 1
    assert created[0]._shutdown
    assert "child-secret" not in repr(events)
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message


def test_recursive_batch_preserves_order_when_workers_finish_out_of_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    finish_order: list[str] = []
    created: list[ChildRuntimeLease] = []
    # Wide spacing keeps the out-of-order finish robust under coverage+xdist
    # load: the assertion is ordering, not the microsecond schedule.
    delays = {"A": 0.60, "B": 0.05, "C": 0.30}

    class Child:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            time.sleep(delays[prompt])
            finish_order.append(prompt)
            return dspy.Prediction(answer=prompt, evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=3),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 5,
    )

    outcomes = executor.batched_tool(tasks=[{"task": task} for task in ["A", "B", "C"]])
    assert [item["answer"] for item in outcomes] == ["A", "B", "C"]
    assert finish_order == ["B", "C", "A"]
    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().recursive_children_completed == 3


def test_recursive_batch_wraps_failure_when_all_children_are_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_batch
    import fleet_rlm.rlm.recursion as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    created: list[ChildRuntimeLease] = []

    class Child:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            if prompt == "fail":
                raise ValueError("provider failure")
            return dspy.Prediction(answer="ok", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)
    real_wait = recursive_batch.wait

    def wait_for_all(futures, *, timeout=None, **_kwargs):
        return real_wait(futures, timeout=timeout)

    monkeypatch.setattr(recursive_batch, "wait", wait_for_all)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 5,
    )

    outcomes = executor.batched_tool(tasks=[{"task": task} for task in ["fail", "ok"]])
    assert [item["status"] for item in outcomes] == ["failed", "completed"]
    executor.wait_owned()
    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().recursive_children_completed == 2


def test_recursive_batch_ordinary_failure_waits_for_successful_sibling_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    a_started = threading.Event()
    b_started = threading.Event()
    release_b = threading.Event()
    created: list[ChildRuntimeLease] = []

    class Child:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            if prompt == "A":
                a_started.set()
                assert b_started.wait(1)
                raise ValueError("A failed")
            b_started.set()
            release_b.wait(2)
            return dspy.Prediction(answer="B", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 5,
    )
    result: dict[str, object] = {}

    def run_batch() -> None:
        try:
            result["outcomes"] = executor.batched_tool(tasks=[{"task": task} for task in ["A", "B"]])
        except BaseException as exc:
            result["error"] = exc

    worker = threading.Thread(target=run_batch)
    worker.start()
    try:
        assert a_started.wait(1)
        assert b_started.wait(1)
        worker.join(0.1)
        assert worker.is_alive()
        assert "outcomes" not in result
        assert any(not lease.interpreter._shutdown for lease in created)
    finally:
        release_b.set()
        worker.join(2)
    assert not worker.is_alive()
    assert "error" not in result
    assert [item["status"] for item in result["outcomes"]] == ["failed", "completed"]
    executor.wait_owned()
    assert all(lease.interpreter._shutdown for lease in created)
    executor.raise_if_cleanup_failed()


def test_recursive_batch_submit_failure_retains_already_submitted_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_batch
    import fleet_rlm.rlm.recursion as recursive_calls

    release = threading.Event()
    started = threading.Event()
    closed = threading.Event()
    real_submit = recursive_batch.ChildAsyncScheduler.submit_blocking
    submits = 0

    def fail_second_submit(scheduler, call):
        nonlocal submits
        submits += 1
        if submits == 1:
            future = real_submit(scheduler, call)
            assert started.wait(1)
            return future
        raise RuntimeError("child worker submit failed")

    monkeypatch.setattr(recursive_batch.ChildAsyncScheduler, "submit_blocking", fail_second_submit)

    class BlockingChild:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            del prompt
            started.set()
            release.wait(2)
            return dspy.Prediction(answer="late", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            interpreter.shutdown()
            closed.set()

        return ChildRuntimeLease(
            interpreter,
            f"submit-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )

    adapter = dspy.JSONAdapter()
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=1),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 10,
    )

    try:
        with pytest.raises(RuntimeError, match="submit failed"):
            executor.batched_tool(tasks=[{"task": task} for task in ["first", "second"]])
        assert started.wait(1)
        assert not closed.is_set()
    finally:
        release.set()
        executor.wait_owned()

    assert closed.is_set()


def test_executor_wait_owned_times_out_when_child_worker_never_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_calls

    adapter = dspy.JSONAdapter()
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(max_calls=1, max_parallel_children=1),
        child_runtime_factory=lambda call_index: ChildRuntimeLease(
            DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            lambda: None,
        ),
        deadline=time.monotonic() + 10,
    )
    never_done: Future[str] = Future()
    executor._retain_pending_batch_futures({never_done})
    monkeypatch.setattr(recursive_calls, "_PENDING_BATCH_WAIT_TIMEOUT_S", 0.05)

    started = time.monotonic()
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        executor.wait_owned()
    assert time.monotonic() - started < 2


def test_recursive_batch_cancels_queued_children_before_they_acquire_a_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursion as recursive_batch
    import fleet_rlm.rlm.recursion as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    started = threading.Event()
    release = threading.Event()
    call_indexes: list[int] = []
    created: list[ChildRuntimeLease] = []

    class BlockingChild:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            del prompt
            started.set()
            release.wait(2)
            return dspy.Prediction(answer="late", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _child: True)
    real_wait = recursive_batch.wait

    def early_wait(futures, *, timeout=None, return_when=None):
        bounded = 0.05 if timeout is None else min(timeout, 0.05)
        if return_when is None:
            return real_wait(futures, timeout=bounded)
        return real_wait(futures, timeout=bounded, return_when=return_when)

    monkeypatch.setattr(recursive_batch, "wait", early_wait)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:  # noqa: ARG001
        call_indexes.append(call_index)
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
            _stage_files=lambda _files: None,
            _read_result_files=lambda _paths: {},
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=1),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 1,
    )

    try:
        with pytest.raises(TimeoutError, match="batch deadline exceeded"):
            executor.batched_tool(tasks=[{"task": task} for task in ["A", "B", "C"]])
        assert started.wait(1)
        assert call_indexes == [1]
        assert len(created) == 1
        with pytest.raises(RuntimeError, match="cleanup is still pending"):
            executor.raise_if_cleanup_failed()
        with pytest.raises(ChildRuntimeCleanupError, match="cleanup is still pending"):
            executor.tool(task="retry", inputs=[])
    finally:
        release.set()
        executor.wait_owned()

    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().call_count == 3
    assert executor.summary().recursive_children_started == 1
    assert executor.summary().recursive_children_completed == 1


# --- live child-result capture boundary --------------------------------
@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_live_batch_capture_rejects_unsuccessful_child_status(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    outcomes = [{"status": "completed", "answer": "first"}, {"status": status, "answer": "second"}]
    monkeypatch.setattr(ProductionRecursiveRLMExecutor, "_call_children_batched", lambda *_args: outcomes)
    evidence = _ChildEvidence()
    _install_batch_answer_capture(monkeypatch, evidence)
    executor = object.__new__(ProductionRecursiveRLMExecutor)
    with pytest.raises(AssertionError):
        executor._call_children_batched([{"task": "first"}, {"task": "second"}])
    assert evidence.batch_answers is None


def test_live_batch_capture_retains_order_and_typed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = [{"status": "completed", "answer": "first"}, {"status": "completed", "answer": "second"}]
    monkeypatch.setattr(ProductionRecursiveRLMExecutor, "_call_children_batched", lambda *_args: outcomes)
    evidence = _ChildEvidence()
    _install_batch_answer_capture(monkeypatch, evidence)
    executor = object.__new__(ProductionRecursiveRLMExecutor)
    result = executor._call_children_batched([{"task": "first"}, {"task": "second"}])
    assert result is outcomes
    assert evidence.batch_answers == ["first", "second"]
