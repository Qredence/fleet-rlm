from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future

import dspy
import pytest
from dspy.predict.rlm import RLM

import fleet_rlm.rlm.recursion as recursion_module
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import Status, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.recursion import (
    ChildOutcome,
    ChildRuntimeAuthorizationError,
    ChildRuntimeCleanupError,
    ChildUsage,
    DelegationMetrics,
    RecursiveBatchError,
    RecursiveRLMOptions,
    SelectedInputAccess,
    SubproblemCapsule,
)
from tests.support.recursion_scheduler import RecursiveRLMExecutor


def _executor(
    root_actions: list[dict[str, str]],
    *,
    sub_actions: list[dict[str, str]] | None = None,
    options: RecursiveRLMOptions | None = None,
    factory_calls: list[DaytonaCodeInterpreter] | None = None,
    observer=None,
    is_authorized: Callable[[], bool] | None = None,
    selected_input_reader: Callable[[str, int], str] | None = None,
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

    def factory(call_index: int) -> ChildRuntimeLease:
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
        )

    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
        selected_input_reader=selected_input_reader,
    )


def test_subproblem_capsule_is_selected_bounded_input_not_session_copy() -> None:
    capsule = SubproblemCapsule(
        task="compare selected findings",
        fragments=("finding-a", "finding-b"),
        authorized_references=("artifact://00000000-0000-0000-0000-000000000001",),
        expected_result_shape="verdict with citations",
        evidence_requirements=("cite each finding",),
        allocation_bytes=512,
    )
    payload = json.loads(capsule.render())
    assert payload["task"] == "compare selected findings"
    assert payload["selected_fragments"] == ["finding-a", "finding-b"]
    assert "history" not in payload and "workspace" not in payload
    with pytest.raises(ValueError, match="serialized bytes"):
        SubproblemCapsule(task="x", fragments=("x" * 100,), allocation_bytes=10)


def test_capsule_rejects_old_allocation_alias_and_counts_serialized_unicode_bytes() -> None:
    with pytest.raises(ValueError, match="extra"):
        SubproblemCapsule.model_validate({"task": "x", "allocation_chars": 512})
    capsule = SubproblemCapsule(task="inspect café and λ", fragments=("🔎",))
    exact = capsule.serialized_bytes
    assert SubproblemCapsule(task=capsule.task, fragments=capsule.fragments, allocation_bytes=exact)
    with pytest.raises(ValueError, match="serialized bytes"):
        SubproblemCapsule(task=capsule.task, fragments=capsule.fragments, allocation_bytes=exact - 1)


@pytest.mark.parametrize("status", ["complete", "timeout", "unknown"])
def test_child_result_rejects_noncanonical_status(status: str) -> None:
    with pytest.raises(ValueError):
        ChildOutcome(status=status)


def test_child_usage_is_closed_and_unknown_tokens_are_not_zero() -> None:
    usage = ChildUsage(llm_calls=1)
    assert usage.model_dump()["total_tokens"] is None
    assert usage.token_usage_status == "unavailable"
    with pytest.raises(ValueError):
        ChildUsage(llm_calls=-1)
    with pytest.raises(ValueError):
        ChildUsage.model_validate({"private_provider_field": "secret"})


def test_child_metrics_are_local_while_parent_retains_aggregate() -> None:
    parent = DelegationMetrics()
    first, second = DelegationMetrics(parent=parent), DelegationMetrics(parent=parent)
    first.record_lm_call("root", 1, usage={"total_tokens": 7})
    second.record_lm_call("root", 1, usage={"total_tokens": 11})
    assert first.snapshot().child_root_lm_calls_depth_1 == 1
    assert second.snapshot().child_root_lm_calls_depth_1 == 1
    assert parent.snapshot().child_root_lm_calls_depth_1 == 2
    assert first.snapshot().lm_token_totals[0][4] == 7
    assert second.snapshot().lm_token_totals[0][4] == 11


def test_selected_access_reports_only_read_reference_and_separates_inline_delivery() -> None:
    reads: list[str] = []
    access = SelectedInputAccess(
        SubproblemCapsule(task="inspect", fragments=("inline",), authorized_references=("a.txt", "b.txt")),
        reader=lambda path, _remaining: reads.append(path) or "selected content",
        check_authority=lambda: None,
    )
    assert access.accessed_references == ()
    assert access.delivered_fragments == ("fragment-1",)
    assert access.read("reference-2") == "selected content"
    assert access.read("reference-2") == "selected content"
    assert reads == ["b.txt"]
    assert access.accessed_references == ("reference-2",)
    with pytest.raises(ChildRuntimeAuthorizationError):
        access.read("../secret")
    assert reads == ["b.txt"]


def test_selected_access_passes_remaining_bytes_and_counts_only_delivered_content() -> None:
    limits: list[int] = []
    capsule = SubproblemCapsule(task="read", authorized_references=("a.txt", "b.txt"))
    access = SelectedInputAccess(
        capsule,
        reader=lambda _path, remaining: limits.append(remaining) or "é",
        check_authority=lambda: None,
    )
    assert access.selected_input_bytes == capsule.serialized_bytes
    access.read("reference-1")
    access.read("reference-1")
    access.read("reference-2")
    assert limits == [
        capsule.allocation_bytes - capsule.serialized_bytes,
        capsule.allocation_bytes - capsule.serialized_bytes - 2,
    ]
    assert access.selected_input_bytes == capsule.serialized_bytes + 4


@pytest.mark.parametrize("failure", ["checksum", "allocation", "authority"])
def test_selected_access_never_records_failed_delivery(failure: str) -> None:
    def authority() -> None:
        if failure == "authority":
            raise ChildRuntimeAuthorizationError("revoked")

    access = SelectedInputAccess(
        SubproblemCapsule(
            task="inspect",
            authorized_references=("a.txt",),
            selected_file_checksums=(("a.txt", "0" * 64),) if failure == "checksum" else (),
        ),
        reader=lambda _path, _remaining: "x" * (5_000 if failure == "allocation" else 1),
        check_authority=authority,
    )
    with pytest.raises((ValueError, ChildRuntimeAuthorizationError)):
        access.read("reference-1")
    assert access.accessed_references == ()


def test_subproblem_capsule_is_strict_deterministic_and_path_scoped() -> None:
    digest = "A" * 64
    first = SubproblemCapsule.model_validate(
        {
            "task": " inspect selected file ",
            "fragments": ["row-1"],
            "authorized_references": ["artifact://00000000-0000-0000-0000-000000000002"],
            "expected_result_shape": "verdict",
            "evidence_requirements": ["cite row-1"],
            "allocation_bytes": 1_024,
            "selected_file_checksums": [["reports/findings.json", digest]],
        }
    )
    second = SubproblemCapsule(
        task="inspect selected file",
        fragments=("row-1",),
        authorized_references=("artifact://00000000-0000-0000-0000-000000000002",),
        expected_result_shape="verdict",
        evidence_requirements=("cite row-1",),
        allocation_bytes=1_024,
        selected_file_checksums=(("reports/findings.json", digest.lower()),),
    )
    assert first.render() == second.render()
    assert first.serialized_bytes == len(first.render().encode("utf-8"))
    assert json.loads(first.render())["selected_file_checksums"] == [
        {"path": "reports/findings.json", "sha256": digest.lower()}
    ]
    with pytest.raises(ValueError, match="extra"):
        SubproblemCapsule.model_validate({"task": "x", "undeclared": "must reject"})
    with pytest.raises(ValueError, match=r"outside|escapes"):
        SubproblemCapsule(task="x", authorized_references=("../secret",))
    for reference in ("artifact://../secret", "artifact://report/%2e%2e/secret", "https://example.test/x"):
        with pytest.raises(ValueError, match=r"UUID|outside|escapes"):
            SubproblemCapsule(task="x", authorized_references=(reference,))
    with pytest.raises(ValueError, match=r"outside|escapes"):
        SubproblemCapsule(task="x", selected_file_checksums=(("/etc/passwd", digest),))
    with pytest.raises(ValueError, match="non-empty"):
        SubproblemCapsule(task="  ")


def test_execute_capsule_outcome_classifies_ordinary_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])
    capsule = SubproblemCapsule(
        task="selected analysis",
        authorized_references=("artifact://00000000-0000-0000-0000-000000000003",),
        allocation_bytes=1_024,
    )

    def fail(_prompt: str, *, child_profile: str) -> str:
        assert child_profile == "semantic-child"
        metrics = recursion_module._child_metrics.get()
        assert metrics is not None
        metrics.record_lm_call("root", 1, usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})
        raise ValueError("provider details stay inside the typed outcome")

    monkeypatch.setattr(executor, "_call_with_profile", fail)
    outcome = executor.execute_capsule_outcome(capsule)
    assert outcome.status == "failed"
    assert outcome.error_category == "child_failed"
    assert outcome.answer == ""
    assert outcome.source_references == ()
    assert outcome.selected_input_bytes == capsule.serialized_bytes
    assert outcome.usage.llm_calls == 1
    assert outcome.usage.input_tokens == 12
    assert outcome.usage.output_tokens == 3
    assert outcome.usage.total_tokens == 15


def test_execute_capsule_outcome_propagates_ownership_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])
    capsule = SubproblemCapsule(task="selected analysis", allocation_bytes=1_024)

    def cancel(_prompt: str, *, child_profile: str) -> str:
        assert child_profile == "semantic-child"
        raise FutureCancelledError()

    monkeypatch.setattr(executor, "_call_with_profile", cancel)
    with pytest.raises(FutureCancelledError):
        executor.execute_capsule_outcome(capsule)
    assert recursion_module._child_metrics.get() is None
    assert recursion_module._selected_access.get() is None


def test_selected_tool_accepts_one_capsule_and_returns_typed_outcome() -> None:
    executor = _executor([{"reasoning": "selected calculation", "code": "SUBMIT(answer='42')"}])
    outcome = executor.tool(capsule={"task": "calculate selected input", "fragments": ["6 * 7"]})
    assert executor.tool.name == "rlm_query"
    assert set(executor.tool.args) == {"capsule"}
    assert outcome["status"] == "completed"
    assert outcome["answer"] == "42"
    assert outcome["delivered_fragments"] == ["fragment-1"]
    with pytest.raises(ValueError):
        executor.tool(prompt="old prompt argument")


def test_selected_batch_tool_returns_ordered_partial_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _executor([])

    def execute(reservation, _batch_cancelled, *, child_profile):
        assert child_profile == "semantic-child"
        if json.loads(reservation.prompt)["task"] == "fails":
            raise ValueError("private failure")
        return "42"

    monkeypatch.setattr(executor, "_run_reserved_call", execute)
    outcomes = executor.batched_tool(capsules=[{"task": "fails"}, {"task": "succeeds"}])
    assert executor.batched_tool.name == "rlm_query_batched"
    assert set(executor.batched_tool.args) == {"capsules"}
    assert [item["status"] for item in outcomes] == ["failed", "completed"]
    assert [item["answer"] for item in outcomes] == ["", "42"]


def test_failed_capsule_preserves_access_without_claiming_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([], selected_input_reader=lambda _path, _remaining: "selected content")
    capsule = SubproblemCapsule(task="inspect", fragments=("inline",), authorized_references=("a.txt", "b.txt"))

    def fail_after_read(_prompt: str, *, child_profile: str) -> str:
        assert child_profile == "semantic-child"
        access = recursion_module._selected_access.get()
        assert access is not None
        access.read("reference-2")
        raise ValueError("private provider failure")

    monkeypatch.setattr(executor, "_call_with_profile", fail_after_read)
    outcome = executor.execute_capsule_outcome(capsule)
    assert outcome.status == "failed"
    assert outcome.answer == ""
    assert outcome.source_references == ("reference-2",)
    assert outcome.delivered_fragments == ("fragment-1",)
    assert outcome.usage.total_tokens is None
    assert "private provider" not in json.dumps(outcome.as_dict())


def test_capsule_timeout_with_unsettled_worker_is_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([])
    pending: Future[str] = Future()

    def timeout(_prompt: str, *, child_profile: str) -> str:
        assert child_profile == "semantic-child"
        executor._retain_pending_batch_futures((pending,))
        raise TimeoutError("worker remains owned")

    monkeypatch.setattr(executor, "_call_with_profile", timeout)
    try:
        with pytest.raises(ChildRuntimeCleanupError, match="pending"):
            executor.execute_capsule_outcome(SubproblemCapsule(task="inspect"))
    finally:
        pending.set_result("settled")


def test_capsule_batch_returns_ordered_typed_outcomes_including_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])
    capsules = [
        {"task": "first", "authorized_references": ["artifact://00000000-0000-0000-0000-000000000004"]},
        {"task": "second", "authorized_references": ["artifact://00000000-0000-0000-0000-000000000005"]},
    ]

    def answer(reservation, _batch_cancelled, *, child_profile):
        assert child_profile == "semantic-child"
        return f"answer-{reservation.call_index}"

    monkeypatch.setattr(executor, "_run_reserved_call", answer)
    result = executor._call_capsules_batched(capsules)
    assert [item["answer"] for item in result] == ["answer-1", "answer-2"]
    assert [item["status"] for item in result] == ["completed", "completed"]
    assert [outcome.status for outcome in executor.last_capsule_outcomes] == ["completed", "completed"]
    assert [item["source_references"] for item in result] == [[], []]

    def fail_second(reservation, _batch_cancelled, *, child_profile):
        del child_profile
        if reservation.call_index == 2:
            raise ValueError("second child failed")
        return "first-answer"

    monkeypatch.setattr(executor, "_run_reserved_call", fail_second)
    # The shared monotonic ledger charges the first batch, so a fresh executor
    # proves the second batch's partial-result policy independently.
    failing = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])
    monkeypatch.setattr(failing, "_run_reserved_call", fail_second)
    results = failing.batched_tool(capsules=capsules)
    assert [item["status"] for item in results] == ["completed", "failed"]
    assert [outcome.status for outcome in failing.last_capsule_outcomes] == ["completed", "failed"]


def test_readonly_capsule_batch_returns_completed_siblings_after_ordinary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])
    capsules = [
        {"task": "first", "authorized_references": ["artifact://00000000-0000-0000-0000-000000000004"]},
        {"task": "second", "authorized_references": ["artifact://00000000-0000-0000-0000-000000000005"]},
    ]

    def fail_second(reservation, _batch_cancelled, *, child_profile):
        del child_profile
        if reservation.call_index == 2:
            raise ValueError("provider details must remain internal")
        return "first-answer"

    monkeypatch.setattr(executor, "_run_reserved_call", fail_second)

    result = executor.batched_tool(capsules=capsules)

    assert [item["status"] for item in result] == ["completed", "failed"]
    assert result[0]["answer"] == "first-answer"
    assert result[1]["answer"] == ""
    assert result[1]["error_category"] == "child_failed"
    assert all("provider details" not in str(item) for item in result)
    assert [outcome.status for outcome in executor.last_capsule_outcomes] == ["completed", "failed"]


def test_readonly_capsule_batch_keeps_authority_and_cleanup_failures_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])
    capsules = [{"task": "only", "authorized_references": ["artifact://00000000-0000-0000-0000-000000000006"]}]

    monkeypatch.setattr(
        executor,
        "_run_reserved_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ChildRuntimeAuthorizationError("authority revoked")),
    )

    with pytest.raises(RecursiveBatchError) as raised:
        executor.batched_tool(capsules=capsules)
    assert isinstance(raised.value.__cause__, ChildRuntimeAuthorizationError)


def test_capsule_tool_returns_structured_evidence() -> None:
    executor = _executor([{"reasoning": "submit", "code": "SUBMIT(answer='capsule-ok [fragment-1]')"}])
    result = executor.tool(
        capsule={
            "task": "classify selected row",
            "fragments": ["row-a"],
            "authorized_references": ["artifact://00000000-0000-0000-0000-000000000007"],
            "evidence_requirements": ["cite source"],
        }
    )
    assert result["status"] == "completed"
    assert result["answer"] == "capsule-ok [fragment-1]"
    assert result["cited_evidence"] == ["fragment-1"]
    assert result["source_references"] == []
    assert result["usage"]["child_calls"] == 1


def test_native_capsule_child_reads_only_selected_reference() -> None:
    reads: list[str] = []
    executor = _executor(
        [
            {"reasoning": "read selected evidence", "code": "text = read_selected_input(evidence_id='reference-2')"},
            {"reasoning": "submit", "code": "SUBMIT(answer=text)"},
        ],
        selected_input_reader=lambda reference, _remaining: reads.append(reference) or "verified input bytes",
    )
    try:
        result = executor.tool(
            capsule={
                "task": "Read reference-2 and return its text",
                "fragments": ["inline instruction"],
                "authorized_references": ["unused.txt", "selected.txt"],
            }
        )
        assert result["answer"] == "verified input bytes"
        assert result["source_references"] == ["reference-2"]
        assert result["delivered_fragments"] == ["fragment-1"]
        assert reads == ["selected.txt"]
        assert result["usage"]["llm_calls"] == 2
    finally:
        executor.wait_owned()


@pytest.mark.parametrize("claim", ["reference-1", "reference-99", "fragment-2", "reference-01"])
def test_child_cannot_cite_unread_or_undeclared_input(claim: str) -> None:
    executor = _executor(
        [
            {"reasoning": "read", "code": "text = read_selected_input(evidence_id='reference-2')"},
            {"reasoning": "unsupported citation", "code": f"SUBMIT(answer='claim [{claim}]')"},
        ],
        selected_input_reader=lambda _path, _remaining: "selected evidence",
    )
    result = executor.tool(
        capsule={
            "task": "inspect",
            "fragments": ["inline"],
            "authorized_references": ["a.txt", "b.txt"],
        }
    )
    assert result["status"] == "failed"
    assert result["answer"] == ""
    assert result["source_references"] == ["reference-2"]
    assert result["cited_evidence"] == []


def test_child_citations_are_distinct_from_access_and_deduplicated() -> None:
    access = SelectedInputAccess(
        SubproblemCapsule(task="inspect", fragments=("inline",), authorized_references=("a.txt", "b.txt")),
        reader=lambda _path, _remaining: "evidence",
        check_authority=lambda: None,
    )
    access.read("reference-1")
    access.read("reference-2")
    assert access.validate_citations("claim [reference-2] [fragment-1] [reference-2]") == (
        "reference-2",
        "fragment-1",
    )
    assert access.accessed_references == ("reference-1", "reference-2")


def test_required_evidence_cannot_be_silently_omitted() -> None:
    executor = _executor([{"reasoning": "unsupported answer", "code": "SUBMIT(answer='claim')"}])
    result = executor.tool(capsule={"task": "inspect", "fragments": ["input"], "evidence_requirements": ["cite input"]})
    assert result["status"] == "failed"
    assert result["answer"] == ""


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
            {"reasoning": "submit child", "code": "SUBMIT(answer=inner)"},
        ],
        sub_actions=[{"answer": "fallback-answer"}],
        factory_calls=created,
        observer=events.append,
    )

    outcome = executor.tool(capsule={"task": "outer slice"})
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
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok')"}],
        factory_calls=created,
        observer=events.append,
    )

    result = executor.tool(capsule={"task": "classify selected row"})

    assert result["answer"] == "child-ok"
    assert len(created) == 1
    assert created[0]._shutdown
    assert executor.summary().call_count == 1
    assert executor.summary().child_iterations == 1
    started = next(event for event in events if isinstance(event, ToolStarted))
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert started.input == {"selected_input_bytes": SubproblemCapsule(task="classify selected row").serialized_bytes}
    assert completed.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event, Status)]
    assert [event.status for event in statuses] == ["child_started", "child_completed"]
    assert statuses[0].message == "call_index=1 recursive_depth=1"
    assert statuses[1].message is not None
    assert "duration_ms=" in statuses[1].message
    assert "cleanup_status=completed" in statuses[1].message
    assert "classify selected row" not in repr(events)
    assert "child-ok" not in repr(events)


@pytest.mark.asyncio
async def test_recursive_tool_awaits_native_child_from_an_active_event_loop() -> None:
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok')"}],
    )

    assert executor.tool(capsule={"task": "classify selected row"})["answer"] == "child-ok"


def test_capsule_child_uses_native_semantic_calls_without_new_interpreter() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [
            {"reasoning": "semantic judgment", "code": "inner = llm_query('inner slice')"},
            {"reasoning": "submit child", "code": "SUBMIT(answer=inner)"},
        ],
        sub_actions=[{"answer": "fallback-answer"}],
        factory_calls=created,
    )

    outcome = executor.tool(capsule={"task": "outer slice"})
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
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        options=RecursiveRLMOptions(max_prompt_chars=10),
        factory_calls=created,
    )

    with pytest.raises(ValueError, match=message):
        executor.tool(capsule={"task": prompt})
    assert created == []


def test_recursive_tool_enforces_shared_call_budget() -> None:
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='ok')"}],
        options=RecursiveRLMOptions(max_calls=1),
    )

    assert executor.tool(capsule={"task": "first"})["answer"] == "ok"
    assert executor.tool(capsule={"task": "second"})["status"] == "failed"
    assert executor.summary().call_count == 1


def test_recursive_batched_tool_preserves_order_and_bounds_child_concurrency() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        {
            "FANOUT-A": {"reasoning": "a", "code": "SUBMIT(answer='A')"},
            "FANOUT-B": {"reasoning": "b", "code": "SUBMIT(answer='B')"},
            "FANOUT-C": {"reasoning": "c", "code": "SUBMIT(answer='C')"},
        },
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        created.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"batch-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=2),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 30,
        observer=events.append,
    )

    outcomes = executor.batched_tool(capsules=[{"task": task} for task in ["FANOUT-A", "FANOUT-B", "FANOUT-C"]])
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


def test_recursive_batch_starts_each_trace_span_in_its_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import fleet_rlm.rlm.recursion as recursive_calls

    parent_thread = threading.get_ident()
    span_threads: list[int] = []

    class Span:
        def finish(self, **_kwargs: object) -> None:
            return None

    def start_span(_name: str, *, inputs: object) -> Span:
        del inputs
        span_threads.append(threading.get_ident())
        return Span()

    monkeypatch.setattr(recursive_calls, "start_turn_span", start_span)
    executor = _executor(
        {
            "first": {"reasoning": "first", "code": "SUBMIT(answer='first')"},
            "second": {"reasoning": "second", "code": "SUBMIT(answer='second')"},
        },  # type: ignore[arg-type]
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
    )

    outcomes = executor.batched_tool(capsules=[{"task": task} for task in ["first", "second"]])
    assert [item["answer"] for item in outcomes] == ["first", "second"]
    assert len(span_threads) == 2
    assert all(thread_id != parent_thread for thread_id in span_threads)


def test_recursive_batch_join_stops_at_turn_deadline_and_worker_retains_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    import fleet_rlm.rlm.recursion as recursive_calls

    release = threading.Event()
    started = threading.Event()
    closed = threading.Event()

    class Child:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            del prompt
            started.set()
            release.wait(1)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())

    def factory(call_index: int) -> ChildRuntimeLease:
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
            executor.batched_tool(capsules=[{"task": task} for task in ["blocked"]])
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
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        options=RecursiveRLMOptions(max_calls=2),
        factory_calls=created,
    )

    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(capsules=[{"task": task} for task in ["first", "second", "third"]])
    assert created == []
    assert executor.summary().call_count == 0


def test_recursive_tool_rejects_revoked_authority_before_child_creation() -> None:
    authority = RunAuthority()
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=lambda: not authority.revoked,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(capsule={"task": "late child request"})

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
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(capsule={"task": "revoked before allocation"})

    assert created == []
    assert [event.status for event in events if isinstance(event, Status)] == []


def test_recursive_tool_closes_lease_when_authority_is_revoked_after_acquisition() -> None:
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """Revoke on actual allocation, independently of check count."""
        return not created

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(capsule={"task": "revoked after acquisition"})

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
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-secret')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(capsule={"task": "revoked after execution"})

    assert len(created) == 1
    assert created[0]._shutdown
    assert "child-secret" not in repr(events)
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message


def test_rlm_query_wrapper_forwards_kwargs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """RC-1: the recursive ``rlm_query`` sandbox wrapper forwards prompts by name."""
    executor = _executor([{"reasoning": "submit", "code": "SUBMIT(answer='ok')"}])
    tool = executor.tool
    rlm = RLM("prompt -> answer", max_iters=1)
    invoke = rlm._make_interpreter_tool(tool)

    # The pinned DSPy interpreter tool accepts keyword arguments only.
    with pytest.raises(TypeError, match="positional"):
        invoke("delegate this slice")

    broker = DaytonaHttpToolBroker(sandbox=object())
    namespace: dict[str, object] = {}
    exec(broker._tool_wrapper_source("rlm_query", invoke), namespace, namespace)
    wrapper = namespace["rlm_query"]

    class _StubbedResponse:
        def read(self) -> bytes:
            return json.dumps({"result": "child-ok"}).encode("utf-8")

        def __enter__(self) -> _StubbedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    captured: list[dict[str, object]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedResponse:
        del timeout
        captured.append(json.loads(bytes(request.data).decode("utf-8")))
        return _StubbedResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # Sandbox-side ergonomics stay positional, exactly as model code writes them.
    assert wrapper({"task": "delegate this slice"}) == "child-ok"

    assert len(captured) == 1
    payload = captured[0]
    assert payload["tool_name"] == "rlm_query"
    assert payload["args"] == []
    assert payload["kwargs"] == {"capsule": {"task": "delegate this slice"}}


def test_capsule_child_receives_only_selected_input_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.recursion as recursive_calls

    captured: list[dict[str, object]] = []

    class Child:
        def __call__(self, _interpreter, *, prompt):
            del prompt
            return dspy.Prediction(answer="child-ok", trajectory=[])

    def capture_build(**kwargs):
        captured.append(dict(kwargs))
        return Child()

    monkeypatch.setattr(recursive_calls, "build_native_rlm", capture_build)
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])

    assert executor.tool(capsule={"task": "memory tools stay in the root"})["answer"] == "child-ok"
    assert len(captured) == 1
    tools = tuple(str(tool.name) for tool in captured[0]["tools"])
    assert tools == ("read_selected_input",)


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
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            time.sleep(delays[prompt])
            finish_order.append(prompt)
            return dspy.Prediction(answer=prompt, trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=3),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 5,
    )

    outcomes = executor.batched_tool(capsules=[{"task": task} for task in ["A", "B", "C"]])
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
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            if prompt == "fail":
                raise ValueError("provider failure")
            return dspy.Prediction(answer="ok", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    real_wait = recursive_batch.wait

    def wait_for_all(futures, *, timeout=None, **_kwargs):
        return real_wait(futures, timeout=timeout)

    monkeypatch.setattr(recursive_batch, "wait", wait_for_all)

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        child_runtime_factory=factory,
        deadline=time.monotonic() + 5,
    )

    outcomes = executor.batched_tool(capsules=[{"task": task} for task in ["fail", "ok"]])
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
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            if prompt == "A":
                a_started.set()
                assert b_started.wait(1)
                raise ValueError("A failed")
            b_started.set()
            release_b.wait(2)
            return dspy.Prediction(answer="B", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
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
            result["outcomes"] = executor.batched_tool(capsules=[{"task": task} for task in ["A", "B"]])
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
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            del prompt
            started.set()
            release.wait(2)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())

    def factory(call_index: int) -> ChildRuntimeLease:
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
            executor.batched_tool(capsules=[{"task": task} for task in ["first", "second"]])
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
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            prompt = json.loads(prompt)["task"]
            del prompt
            started.set()
            release.wait(2)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    real_wait = recursive_batch.wait

    def early_wait(futures, *, timeout=None, return_when=None):
        bounded = 0.05 if timeout is None else min(timeout, 0.05)
        if return_when is None:
            return real_wait(futures, timeout=bounded)
        return real_wait(futures, timeout=bounded, return_when=return_when)

    monkeypatch.setattr(recursive_batch, "wait", early_wait)

    def factory(call_index: int) -> ChildRuntimeLease:
        call_indexes.append(call_index)
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
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
            executor.batched_tool(capsules=[{"task": task} for task in ["A", "B", "C"]])
        assert started.wait(1)
        assert call_indexes == [1]
        assert len(created) == 1
        with pytest.raises(RuntimeError, match="cleanup is still pending"):
            executor.raise_if_cleanup_failed()
        with pytest.raises(ChildRuntimeCleanupError, match="cleanup is still pending"):
            executor.tool(capsule={"task": "retry"})
    finally:
        release.set()
        executor.wait_owned()

    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().call_count == 3
    assert executor.summary().recursive_children_started == 1
    assert executor.summary().recursive_children_completed == 1
