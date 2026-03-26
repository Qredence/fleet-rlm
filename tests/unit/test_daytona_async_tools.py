from __future__ import annotations

import inspect

import dspy
import pytest

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from tests.unit.fixtures_react import FakeInterpreter, FinalOutput


def _result_factory(code: str, payload: dict[str, object]):
    if "get_buffer(name)" in code:
        return FinalOutput({"items": [{"value": "chunk-1"}]})
    if "load_from_volume" in code:
        return FinalOutput({"status": "ok", "text": "persisted text"})
    if "save_to_volume" in code:
        return FinalOutput(
            {
                "status": "ok",
                "saved_path": payload.get("path", "unknown"),
                "item_count": 1,
            }
        )
    if "findings_count=len(responses)" in code:
        prompts = payload.get("prompts", [])
        prompt_count = len(prompts) if isinstance(prompts, list) else 0
        return FinalOutput(
            {
                "status": "ok",
                "strategy": payload.get("chunk_strategy", "headers"),
                "chunk_count": prompt_count,
                "findings_count": prompt_count,
                "buffer_name": payload.get("buffer_name", "findings"),
            }
        )
    if "chunk_count=len(chunks)" in code:
        return FinalOutput(
            {
                "status": "ok",
                "strategy": payload.get("strategy_norm", "size"),
                "chunk_count": 1,
                "buffer_name": payload.get("buffer_name", "chunks"),
            }
        )
    if "clear_buffer(" in code:
        return FinalOutput({"status": "ok", "scope": "all"})
    return FinalOutput({"status": "ok"})


class _AsyncOnlyInterpreter(FakeInterpreter):
    def __init__(self) -> None:
        super().__init__(execute_result_factory=_result_factory)
        self.async_execute = True
        self.async_execute_calls: list[tuple[str, dict[str, object]]] = []
        self.sync_execute_calls = 0

    async def astart(self) -> None:
        self.start()

    async def ashutdown(self) -> None:
        self.shutdown()

    def execute(self, code: str, variables=None, **kwargs):
        _ = (code, variables, kwargs)
        self.sync_execute_calls += 1
        raise AssertionError(
            "Sync interpreter.execute() should not be used in this test"
        )

    async def aexecute(self, code: str, variables=None, **kwargs):
        _ = kwargs
        payload = variables or {}
        self.async_execute_calls.append((code, payload))
        return _result_factory(code, payload)


def _seed_active_document(
    agent: RLMReActChatAgent, text: str = "alpha\nbeta\ngamma"
) -> None:
    agent._set_document("active", text)
    agent.active_alias = "active"


@pytest.mark.asyncio
async def test_agent_areset_uses_async_clear_buffer(react_records) -> None:
    _ = react_records
    agent = RLMReActChatAgent(interpreter=_AsyncOnlyInterpreter())
    agent.history = dspy.History(
        messages=[{"user_request": "hi", "assistant_response": "ok"}]
    )

    result = await agent.areset(clear_sandbox_buffers=True)

    assert result["status"] == "ok"
    assert result["buffers_cleared"] is True
    assert agent.interpreter.sync_execute_calls == 0
    assert len(agent.interpreter.async_execute_calls) == 1
    assert "clear_buffer(" in agent.interpreter.async_execute_calls[0][0]


@pytest.mark.asyncio
async def test_chunk_sandbox_uses_async_interpreter(react_records) -> None:
    _ = react_records
    agent = RLMReActChatAgent(interpreter=_AsyncOnlyInterpreter())
    _seed_active_document(agent)

    result = agent._get_tool("chunk_sandbox")("size")

    assert inspect.isawaitable(result)
    payload = await result
    assert payload["status"] == "ok"
    assert payload["buffer_name"] == "chunks"
    assert agent.interpreter.sync_execute_calls == 0
    assert len(agent.interpreter.async_execute_calls) == 1
    assert "chunk_count=len(chunks)" in agent.interpreter.async_execute_calls[0][0]


@pytest.mark.asyncio
async def test_clear_buffer_uses_async_interpreter(react_records) -> None:
    _ = react_records
    agent = RLMReActChatAgent(interpreter=_AsyncOnlyInterpreter())

    result = agent._get_tool("clear_buffer")()

    assert inspect.isawaitable(result)
    payload = await result
    assert payload["status"] == "ok"
    assert agent.interpreter.sync_execute_calls == 0
    assert len(agent.interpreter.async_execute_calls) == 1
    assert "clear_buffer(" in agent.interpreter.async_execute_calls[0][0]


@pytest.mark.asyncio
async def test_parallel_semantic_map_uses_async_interpreter(react_records) -> None:
    _ = react_records
    agent = RLMReActChatAgent(interpreter=_AsyncOnlyInterpreter())
    _seed_active_document(agent, "# Header\nOne\n## Subheader\nTwo")

    result = agent._get_tool("parallel_semantic_map")(
        "summarize",
        chunk_strategy="headers",
        max_chunks=4,
        buffer_name="findings",
    )

    assert inspect.isawaitable(result)
    payload = await result
    assert payload["status"] == "ok"
    assert payload["buffer_name"] == "findings"
    assert agent.interpreter.sync_execute_calls == 0
    assert len(agent.interpreter.async_execute_calls) == 1
    assert (
        "findings_count=len(responses)" in agent.interpreter.async_execute_calls[0][0]
    )
