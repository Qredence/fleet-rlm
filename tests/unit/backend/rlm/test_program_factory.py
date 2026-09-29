"""Behavior contracts for native DSPy program construction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import dspy
import pytest

from fleet_rlm.rlm.program import RLMOptions
from fleet_rlm.rlm.result import RLMConfigError
from tests.support.native_rlm import build_native_rlm_for_test


class _CopyableLM:
    """Minimal copyable role-LM double: isolates history and records call kwargs."""

    def __init__(self) -> None:
        self.history: list[object] = []
        self.calls: list[dict[str, object]] = []

    def copy(self) -> _CopyableLM:
        return _CopyableLM()

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()


def host_echo(value: str = "ok") -> str:
    """Host tool with a valid Python identifier name."""
    return value


def test_model_bundle_forks_isolated_child_lms() -> None:
    from fleet_rlm.rlm.program import RLMModelBundle

    root = _CopyableLM()
    sub = _CopyableLM()
    bundle = RLMModelBundle(root, sub)

    first = bundle.fork_for_child()
    second = bundle.fork_for_child()

    assert first.root_lm is not root
    assert first.sub_lm is not sub
    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    # Child copies may never consume the Turn's root-only finalization capacity.
    assert first.root_lm._fleet_can_finalize is False
    assert first.sub_lm._fleet_can_finalize is False
    # Forking never marks the shared role templates.
    assert not hasattr(root, "_fleet_can_finalize")
    assert not hasattr(sub, "_fleet_can_finalize")


@pytest.mark.asyncio
async def test_turn_bound_lm_preserves_dspy_sync_async_usage_and_callbacks():
    from collections.abc import AsyncIterator, Iterator
    from typing import Any

    from dspy.clients.engines.base import validate_request
    from dspy.lm15 import Request, Response, response_from_openai_chat, response_to_events
    from dspy.utils.callback import BaseCallback

    from fleet_rlm.rlm.program import RLMModelBundle

    class Callback(BaseCallback):
        def __init__(self):
            self.starts = []

        def on_lm_start(self, call_id, instance, inputs):
            """
            Record the model associated with a language-model invocation.

            Parameters:
                instance: The language-model instance whose model name is recorded.
            """
            del call_id, inputs
            self.starts.append(instance.model)

    class OkEngine:
        """Scripted engine returning one fixed ``ok`` completion per call."""

        def complete(self, request: Request) -> Response:
            validate_request(request)
            return response_from_openai_chat(
                {
                    "model": "test/script",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }
            )

        def stream(self, request: Request) -> Iterator[Any]:
            return response_to_events(self.complete(request))

        def close(self) -> None:
            pass

    class AsyncOkEngine:
        """Async counterpart of :class:`OkEngine` over the same completion."""

        def __init__(self, sync: OkEngine) -> None:
            self.sync = sync

        async def complete(self, request: Request) -> Response:
            return self.sync.complete(request)

        async def stream(self, request: Request) -> AsyncIterator[Any]:
            for event in self.sync.stream(request):
                yield event

        async def aclose(self) -> None:
            pass

    sync_engine = OkEngine()
    callback = Callback()
    source = dspy.LM(
        "test/script",
        model_type="chat",
        cache=False,
        engine=sync_engine,
        async_engine=AsyncOkEngine(sync_engine),
        callbacks=[callback],
    )
    bound = RLMModelBundle(source, source).bind_turn().root_lm
    assert bound("sync") == ["ok"]
    assert await bound.acall("async") == ["ok"]
    assert len(bound.history) == 2
    assert bound.history[-1]["usage"]["total_tokens"] == 3
    assert source.history == []
    assert callback.starts == ["test/script", "test/script"]


def test_turn_binding_isolates_role_copies_and_marks_finalization() -> None:
    from fleet_rlm.rlm.budget import TurnBudget
    from fleet_rlm.rlm.program import RLMModelBundle

    root = _CopyableLM()
    sub = _CopyableLM()
    source = RLMModelBundle(root, sub)
    budget = TurnBudget(deadline=None)
    bound = source.bind_turn(budget=budget)

    bound.root_lm.forward(prompt="root")
    bound.sub_lm.forward(prompt="sub")

    assert bound is not source
    assert bound.root_lm is not root
    assert bound.sub_lm is not sub
    # The provider call happens on the Turn copy, never on the shared template.
    assert root.calls == []
    assert sub.calls == []
    # Only the Turn root may consume shared finalization capacity.
    assert bound.root_lm._fleet_can_finalize is True
    assert bound.sub_lm._fleet_can_finalize is False
    assert bound.budget is budget
    assert source.budget is None


def test_turn_binding_without_budget_inherits_the_source_budget() -> None:
    from fleet_rlm.rlm.budget import TurnBudget
    from fleet_rlm.rlm.program import RLMModelBundle

    budget = TurnBudget(deadline=None)
    source = RLMModelBundle(_CopyableLM(), _CopyableLM(), budget=budget)

    assert source.bind_turn().budget is budget


def test_sequential_and_concurrent_turn_bindings_return_fresh_copies() -> None:
    from fleet_rlm.rlm.program import RLMModelBundle

    source = RLMModelBundle(_CopyableLM(), _CopyableLM())

    def call(_index: int) -> RLMModelBundle:
        return source.bind_turn()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(call, (0, 1)))

    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    # Binding a Turn never accumulates state on the shared role templates.
    assert not hasattr(source.root_lm, "_fleet_can_finalize")
    assert not hasattr(source.sub_lm, "_fleet_can_finalize")


def _tool(name):
    """Create a test tool with the specified name."""
    return dspy.Tool(lambda: "ok", name=name)


@pytest.mark.parametrize("name", ["llm_query"])
def test_native_builder_rejects_namespace_collisions(name):
    with pytest.raises(RLMConfigError):
        build_native_rlm_for_test(signature="question -> answer", options=RLMOptions(), tools=[_tool(name)])


def test_native_builder_rejects_duplicate_names_and_keeps_authorized_tools():
    rlm = build_native_rlm_for_test(signature="question -> answer", options=RLMOptions(), tools=[_tool("read_data")])

    assert set(rlm.tools) == {"read_data"}
    with pytest.raises(RLMConfigError, match="duplicate"):
        build_native_rlm_for_test(
            signature="question -> answer",
            options=RLMOptions(),
            tools=[_tool("read_data"), _tool("read_data")],
        )
