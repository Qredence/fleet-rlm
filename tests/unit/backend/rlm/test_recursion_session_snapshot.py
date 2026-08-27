"""P47.4 immutable Session snapshot lanes for delegated native children.

Behavior-only evidence:

- The child-visible snapshot is frozen and copied at Turn-preparation time;
  later mutation of the source conversation cannot leak into a child.
- Native production children receive the delegated prompt, current user
  request, committed ``dspy.History`` snapshot, bounded Session context with
  the authorized capability view, and a forked Root/Sub model policy.
- Executor doubles and the no-snapshot path keep the prompt-only contract.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend, sandbox_backend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.files.workspace_models import WorkspaceCapabilityMetadata
from fleet_rlm.rlm.program import RLMModelBundle, build_rlm_input_kwargs, build_session_context_payload
from fleet_rlm.rlm.recursion import (
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
    RecursiveSessionSnapshot,
    RecursiveSessionSubtaskSignature,
    RecursiveSubtaskSignature,
    build_recursive_session_snapshot,
)
from fleet_rlm.sessions.history_transport import CommittedSessionHistory


def _manifest() -> SessionContextManifest:
    return SessionContextManifest(
        session_id=uuid4(),
        checkpoint_version=3,
        message_count=6,
        recent=(TurnPreview(ordinal=1, role="user", preview="earlier request"),),
    )


def _workspace() -> WorkspaceCapabilityMetadata:
    return WorkspaceCapabilityMetadata(available=True, root=".", instructions="read/write workspace enabled")


def _models() -> RLMModelBundle:
    adapter = dspy.JSONAdapter()
    return RLMModelBundle(
        root_lm=dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        sub_lm=dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
    )


def _lease_factory() -> Any:
    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        return ChildRuntimeLease(
            interpreter,
            f"snapshot-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )

    return factory


def _snapshot(**overrides: Any) -> RecursiveSessionSnapshot:
    kwargs: dict[str, Any] = {
        "request": "Summarize the prior decision",
        "history": CommittedSessionHistory([{"request": "prior ask", "answer": "prior answer"}]),
        "session_context": _manifest(),
        "workspace": _workspace(),
        "models": _models(),
    }
    kwargs.update(overrides)
    return build_recursive_session_snapshot(**kwargs)


def _native_child_recorder(captured: list[dict[str, Any]]) -> Any:
    """A spoofed native child whose ``acall`` records every invocation."""

    class RLM:
        async def acall(self, _interpreter: Any, prompt: str, **kwargs: Any) -> dspy.Prediction:
            captured.append({"prompt": prompt, **kwargs})
            return dspy.Prediction(answer="child-ok", trajectory=[])

    RLM.__module__ = "dspy.predict.rlm"
    return RLM()


def _install_child(monkeypatch: pytest.MonkeyPatch, child: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    constructed: list[dict[str, Any]] = []
    built: list[dict[str, Any]] = []
    real_executor = RecursiveRLMExecutor

    class SpyExecutor(real_executor):
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__(**kwargs)

    import fleet_rlm.rlm.recursion as recursion_module

    def _record_build(**kwargs: Any) -> Any:
        built.append(kwargs)
        return child

    monkeypatch.setattr(recursion_module, "RecursiveRLMExecutor", SpyExecutor)
    monkeypatch.setattr(recursion_module, "build_native_rlm", _record_build)
    return constructed, built


def test_p47_4_snapshot_is_frozen_and_committed_history_is_copied() -> None:
    snapshot = _snapshot()
    assert isinstance(snapshot, RecursiveSessionSnapshot)
    assert isinstance(snapshot.history, dspy.History)
    assert snapshot.history.messages == [{"request": "prior ask", "answer": "prior answer"}]

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.request = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError, match="immutable"):
        snapshot.history.messages.append({})
    with pytest.raises(TypeError, match="immutable"):
        snapshot.history.messages[0]["answer"] = "mutated"

    # Mutating the source conversation after materialization cannot leak in.
    source = dspy.History(messages=[{"request": "one", "answer": "two"}])
    snapshot_from_history = build_recursive_session_snapshot(
        request="r",
        history=source,
        session_context=_manifest(),
        workspace=_workspace(),
        models=_models(),
    )
    source.messages.append({"request": "late", "answer": "mutation"})
    assert snapshot_from_history.history.messages == [{"request": "one", "answer": "two"}]


def test_p47_4_snapshot_deep_copies_nested_history_values() -> None:
    source = dspy.History(messages=[{"request": "one", "answer": {"nested": [1]}}])
    snapshot = build_recursive_session_snapshot(
        request="r",
        history=source,
        session_context=_manifest(),
        workspace=_workspace(),
        models=_models(),
    )

    source.messages[0]["answer"]["nested"].append(2)
    assert snapshot.history.messages[0]["answer"] == {"nested": [1]}
    with pytest.raises(TypeError, match="immutable"):
        snapshot.history.messages[0]["answer"]["nested"].append(3)


def test_p47_4_snapshot_materializes_transport_history_and_none() -> None:
    snapshot = _snapshot()
    assert isinstance(snapshot.history, dspy.History)
    assert snapshot.history.messages == [{"request": "prior ask", "answer": "prior answer"}]

    empty = build_recursive_session_snapshot(
        request="r",
        history=None,
        session_context=_manifest(),
        workspace=_workspace(),
        models=_models(),
    )
    assert empty.history.messages == []


def test_p47_4_remote_native_child_receives_transport_history(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    snapshot = _snapshot(
        history=dspy.History(messages=[{"request": "prior ask", "answer": "prior answer"}]),
        workspace_memory_digest="recent memory",
    )
    _, built = _install_child(monkeypatch, _native_child_recorder(captured))

    def remote_factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=sandbox_backend(object()))
        return ChildRuntimeLease(
            interpreter,
            f"remote-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            lambda: None,
        )

    executor = RecursiveRLMExecutor(
        models=snapshot.models,
        options=RecursiveRLMOptions(enabled=True, max_calls=1),
        child_runtime_factory=remote_factory,
        deadline=time.monotonic() + 30,
        snapshot=snapshot,
    )
    assert executor._call("read the prior answer") == "child-ok"

    assert captured[0]["history"] is snapshot.history_transport
    assert isinstance(captured[0]["history"], CommittedSessionHistory)
    assert captured[0]["session_context"]["workspace_memory"]["tail"] == "recent memory"
    assert built[0]["signature"] is RecursiveSessionSubtaskSignature


def test_p47_4_native_child_receives_snapshot_inputs_and_forked_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    snapshot = _snapshot()
    constructed, built = _install_child(monkeypatch, _native_child_recorder(captured))

    executor = RecursiveRLMExecutor(
        models=snapshot.models,
        options=RecursiveRLMOptions(enabled=True, max_calls=1),
        child_runtime_factory=_lease_factory(),
        deadline=time.monotonic() + 30,
        snapshot=snapshot,
    )
    answer = executor._call("classify the selected value")
    assert answer == "child-ok"

    assert len(captured) == 1
    call = captured[0]
    assert call["prompt"] == "classify the selected value"
    assert call["request"] == snapshot.request
    # Identity: the child reads the immutable snapshot, not a live rebuild.
    assert call["history"] is snapshot.history
    expected_payload = build_session_context_payload(
        session_context=snapshot.session_context,
        workspace=snapshot.workspace,
    )
    assert call["session_context"] == expected_payload
    capability = expected_payload["workspace"]
    assert capability == {"available": True, "root": ".", "instructions": "read/write workspace enabled"}

    # The snapshot child is built with the required-inputs Session signature.
    assert built and built[0]["signature"] is RecursiveSessionSubtaskSignature

    # The nested depth-1 executor inherits the same immutable snapshot.
    assert constructed, "child executor was not constructed"
    assert constructed[0]["snapshot"] is snapshot


def test_p47_4_child_context_payload_matches_root_turn_shape() -> None:
    snapshot = _snapshot()
    root_kwargs = build_rlm_input_kwargs(
        request=snapshot.request,
        session_context=snapshot.session_context,
        workspace=snapshot.workspace,
        history=snapshot.history,
    )
    child_payload = build_session_context_payload(
        session_context=snapshot.session_context,
        workspace=snapshot.workspace,
    )
    assert child_payload == root_kwargs["session_context"]


def test_p47_4_without_snapshot_children_receive_prompt_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    _, built = _install_child(monkeypatch, _native_child_recorder(captured))

    executor = RecursiveRLMExecutor(
        models=_models(),
        options=RecursiveRLMOptions(enabled=True, max_calls=1),
        child_runtime_factory=_lease_factory(),
        deadline=time.monotonic() + 30,
    )
    assert executor._call("standalone probe") == "child-ok"
    assert len(captured) == 1
    assert set(captured[0]) == {"prompt"}
    assert built[0]["signature"] is RecursiveSubtaskSignature


def test_p47_4_double_children_keep_the_prompt_only_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    class DoubleChild:
        # The non-native seam passes the interpreter by keyword, so the
        # parameter name is part of the double contract (ARG002: unused).
        async def acall(self, interpreter: Any, prompt: str, **kwargs: Any) -> dspy.Prediction:  # noqa: ARG002
            captured.append({"prompt": prompt, **kwargs})
            return dspy.Prediction(answer="child-ok", trajectory=[])

    snapshot = _snapshot()
    _, built = _install_child(monkeypatch, DoubleChild())

    executor = RecursiveRLMExecutor(
        models=snapshot.models,
        options=RecursiveRLMOptions(enabled=True, max_calls=1),
        child_runtime_factory=_lease_factory(),
        deadline=time.monotonic() + 30,
        snapshot=snapshot,
    )
    assert executor._call("delegated subproblem") == "child-ok"
    # Narrow deterministic doubles keep the pre-P47.4 prompt-only invocation
    # seam; the snapshot policy still selects the Session signature at build.
    assert set(captured[0]) == {"prompt"}
    assert built[0]["signature"] is RecursiveSessionSubtaskSignature


def test_p47_4_batched_children_receive_the_same_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    snapshot = _snapshot()
    _, built = _install_child(monkeypatch, _native_child_recorder(captured))

    executor = RecursiveRLMExecutor(
        models=snapshot.models,
        options=RecursiveRLMOptions(enabled=True, max_calls=2, max_parallel_children=1),
        child_runtime_factory=_lease_factory(),
        deadline=time.monotonic() + 30,
        snapshot=snapshot,
    )
    answers = executor.batched_tool(prompts=["first subproblem", "second subproblem"])
    assert answers == ["child-ok", "child-ok"]
    assert len(captured) == 2
    assert built and built[0]["signature"] is RecursiveSessionSubtaskSignature
    for call in captured:
        assert call["history"] is snapshot.history
        assert call["request"] == snapshot.request
        assert "session_context" in call
