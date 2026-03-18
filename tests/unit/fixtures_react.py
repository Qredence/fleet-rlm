from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Callable

import pytest

try:
    from dspy.primitives.code_interpreter import FinalOutput
except ImportError:

    class FinalOutput(dict):
        """Minimal compatibility shim when DSPy FinalOutput import is unavailable."""

        def __init__(self, output):
            super().__init__(output if isinstance(output, dict) else {"output": output})
            self.output = output

        def __eq__(self, other):
            if not isinstance(other, FinalOutput):
                return NotImplemented
            return dict.__eq__(self, other) and self.output == other.output


class FakeInterpreter:
    def __init__(
        self,
        *,
        has_volume: bool = True,
        execute_result_factory: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.start_calls = 0
        self.shutdown_calls = 0
        self.commit_calls = 0
        self.reload_calls = 0
        self.execute_calls: list[tuple[str, dict[str, Any]]] = []
        self.default_execution_profile = "RLM_DELEGATE"
        self._volume = object() if has_volume else None
        self._execute_result_factory = execute_result_factory

    def start(self) -> None:
        self.start_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def commit(self) -> None:
        self.commit_calls += 1

    def reload(self) -> None:
        self.reload_calls += 1

    @contextmanager
    def execution_profile(self, profile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        _ = kwargs
        payload = variables or {}
        self.execute_calls.append((code, payload))
        if self._execute_result_factory is not None:
            return self._execute_result_factory(code, payload)
        return FinalOutput(
            {
                "status": "ok",
                "chunk_count": len(payload.get("prompts", [])),
                "findings_count": len(payload.get("prompts", [])),
                "buffer_name": payload.get("buffer_name", "findings"),
                "path": payload.get("path", "unknown"),
                "content": "fake content",
                "items": [{"name": "file1.txt", "type": "file"}],
            }
        )

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        return self.execute(code, variables, **kwargs)


def make_fake_react(
    records: list[dict[str, object]],
    *,
    assistant_response_prefix: str = "echo:",
    trajectory: object | None = None,
):
    final_trajectory = (
        trajectory if trajectory is not None else {"tool_name_0": "finish"}
    )

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters):
            records.append(
                {
                    "signature": signature,
                    "tools": tools,
                    "max_iters": max_iters,
                }
            )

        def __call__(self, **kwargs):
            request = kwargs.get("user_request", "")
            return SimpleNamespace(
                assistant_response=f"{assistant_response_prefix}{request}",
                trajectory=final_trajectory,
            )

    return _FakeReAct


@pytest.fixture(name="react_records")
def react_records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    monkeypatch.setattr(
        "fleet_rlm.core.agent.chat_agent.dspy.ReAct",
        make_fake_react(records),
    )
    return records


__all__ = [
    "FakeInterpreter",
    "FinalOutput",
    "make_fake_react",
    "react_records",
]
