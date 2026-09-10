"""Shared scenario setup; not a collected test module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dspy


class _IterationActionSignature(dspy.Signature):
    """Minimal native-action-shaped signature for deadline adapter tests."""

    iteration: str = dspy.InputField()
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


class _ScriptedLM(dspy.BaseLM):
    """Emit one scripted raw completion text per call and record each request."""

    forward_contract = "legacy"

    def __init__(self, texts: list[str]) -> None:
        super().__init__("scripted-lm", "chat", 0.0, 1000, True)
        self._texts = list(texts)
        self.calls: list[dict[str, Any]] = []

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"prompt": prompt, "messages": list(messages or []), "kwargs": dict(kwargs)})
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        text = self._texts[index]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model="scripted-lm",
        )

    async def aforward(self, prompt=None, messages=None, **kwargs):
        return self.forward(prompt=prompt, messages=messages, **kwargs)
