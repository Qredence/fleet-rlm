"""Shared scenario setup; not a collected test module.

The doubles here use the canonical DSPy 3.4 LM shape: a custom engine with
``complete(request) -> Response`` passed to ``dspy.LM(..., engine=...)``.
Subclassing ``dspy.BaseLM`` and implementing ``forward`` is deprecated for
removal in DSPy 3.5 (see ``pyproject.toml`` filterwarnings).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import dspy
from dspy.clients.engines.base import validate_request
from dspy.lm15 import Request, Response, response_from_openai_chat, response_to_events


class _IterationActionSignature(dspy.Signature):
    """Minimal native-action-shaped signature for deadline adapter tests."""

    iteration: str = dspy.InputField()
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


def request_messages(request: Request) -> list[dict[str, Any]]:
    """Render a canonical request as the OpenAI-style messages it came from.

    Prompt assertions in scripted-LM tests read ``call["messages"]`` as
    ``{"role": ..., "content": ...}`` pairs, so the system block and the
    conversation messages are flattened back into one ordered list.
    """
    messages: list[dict[str, Any]] = []
    if request.system is not None:
        system = (
            request.system
            if isinstance(request.system, str)
            else "".join(getattr(part, "text", "") for part in request.system)
        )
        messages.append({"role": "system", "content": system})
    messages.extend({"role": message.role, "content": message.text or ""} for message in request.messages)
    return messages


class _ScriptedEngine:
    """Emit one scripted raw completion text per call and record each request."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls: list[dict[str, Any]] = []

    def complete(self, request: Request) -> Response:
        validate_request(request)
        self.calls.append({"prompt": None, "messages": request_messages(request), "kwargs": {}})
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        text = self._texts[index]
        return response_from_openai_chat(
            {
                "model": "scripted-lm",
                "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    def stream(self, request: Request) -> Iterator[Any]:
        return response_to_events(self.complete(request))

    def close(self) -> None:
        pass


class _AsyncScriptedEngine:
    """Async counterpart of :class:`_ScriptedEngine` over the same call log."""

    def __init__(self, sync: _ScriptedEngine) -> None:
        self.sync = sync

    async def complete(self, request: Request) -> Response:
        return self.sync.complete(request)

    async def stream(self, request: Request) -> AsyncIterator[Any]:
        for event in self.sync.stream(request):
            yield event

    async def aclose(self) -> None:
        pass


class _ScriptedLM(dspy.LM):
    """Emit one scripted raw completion text per call and record each request.

    The last scripted text repeats for every call past the end of the list.
    ``calls`` records one ``{"prompt", "messages", "kwargs"}`` entry per call.
    """

    def __init__(self, texts: list[str]) -> None:
        self._scripted_engine = _ScriptedEngine(texts)
        super().__init__(
            "scripted-lm",
            model_type="chat",
            cache=False,
            engine=self._scripted_engine,
            async_engine=_AsyncScriptedEngine(self._scripted_engine),
        )

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._scripted_engine.calls
