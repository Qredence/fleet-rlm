"""A DummyLM that returns raw completions through DSPy's real call and parse path, shared by engine tests."""

from __future__ import annotations

import json
from copy import deepcopy
from threading import Barrier

import dspy


class ScriptedLM(dspy.utils.DummyLM):
    """Supply raw completions while keeping DSPy's real formatting/call/parsing pipeline."""

    def __init__(self, responses, barrier: Barrier | None = None):
        super().__init__([{"raw": response} for response in responses])
        self.calls = []
        self.adapters = []
        self.barrier = barrier

    @property
    def prompts(self) -> list[str]:
        return [json.dumps(call.get("messages")) for call in self.calls]

    def _format_answer_fields(self, fields):
        response = fields["raw"]
        if isinstance(response, BaseException):
            raise response
        return response

    def _record(self, kwargs):
        self.calls.append(deepcopy(kwargs))
        self.adapters.append(dspy.settings.adapter)
        if self.barrier is not None and len(self.calls) == 1:
            self.barrier.wait(timeout=10)

    def __call__(self, *args, **kwargs):
        self._record(kwargs)
        return super().__call__(*args, **kwargs)

    async def acall(self, *args, **kwargs):
        self._record(kwargs)
        return await super().acall(*args, **kwargs)
