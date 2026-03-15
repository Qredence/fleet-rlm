from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import dspy

from fleet_rlm.core.interpreter import ExecutionProfile, ModalInterpreter


def chat_response(reasoning: str, code: str) -> str:
    return (
        f"[[ ## reasoning ## ]]\n{reasoning}\n\n"
        f"[[ ## code ## ]]\n{code}\n\n"
        "[[ ## completed ## ]]"
    )


class ScriptedLM(dspy.LM):
    """Returns a fixed sequence of strings as LM responses."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__("mock/scripted")
        self._responses = responses
        self._idx = 0

    def __call__(self, prompt=None, messages=None, **kwargs):
        _ = prompt, kwargs
        if self._idx >= len(self._responses):
            snippet = messages[-1]["content"][:600] if messages else ""
            raise AssertionError(
                f"LM called {self._idx + 1} times but only "
                f"{len(self._responses)} response(s) were scripted.\n\n"
                f"Last prompt:\n{snippet}"
            )
        resp = self._responses[self._idx]
        self._idx += 1
        return [resp]


def make_mock_interpreter(side_effects: list):
    interp = MagicMock()
    interp.tools = {}
    interp.output_fields = []
    interp._tools_registered = False
    interp.execute.side_effect = side_effects
    return interp


def patch_child_module(monkeypatch, *, answer: str = "ok"):
    created: list[dict[str, object]] = []

    class _FakeChildModule:
        async def acall(self, *, prompt: str, context: str):
            return SimpleNamespace(
                answer=answer,
                trajectory=[{"reasoning": f"handled:{prompt}", "output": context}],
                final_reasoning="done",
            )

    def _fake_builder(
        *,
        interpreter,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ):
        created.append(
            {
                "interpreter": interpreter,
                "max_iterations": max_iterations,
                "max_llm_calls": max_llm_calls,
                "verbose": verbose,
            }
        )
        return _FakeChildModule()

    monkeypatch.setattr(
        "fleet_rlm.react.delegate_sub_agent.build_recursive_subquery_rlm",
        _fake_builder,
    )
    return created


def make_modal_interpreter(
    *, max_llm_calls: int, used_calls: int, sandbox_active: bool = False
) -> ModalInterpreter:
    interpreter = object.__new__(ModalInterpreter)
    interpreter.image = object()
    interpreter._app_obj = None
    interpreter.secrets = []
    interpreter.timeout = 60
    interpreter.idle_timeout = None
    interpreter.execute_timeout = 60
    interpreter._app_name = "test-app"
    interpreter.volume_name = None
    interpreter.volume_mount_path = "/data"
    interpreter.summarize_stdout = True
    interpreter.stdout_summary_threshold = 10_000
    interpreter.stdout_summary_prefix_len = 200
    interpreter.sub_lm = None
    interpreter.max_llm_calls = max_llm_calls
    interpreter._llm_call_count = used_calls
    interpreter._llm_call_lock = threading.Lock()
    interpreter._sub_lm_executor = None
    interpreter._sub_lm_executor_lock = threading.Lock()
    interpreter.llm_call_timeout = 60
    interpreter.default_execution_profile = ExecutionProfile.RLM_DELEGATE
    interpreter.async_execute = True
    interpreter._sandbox = object() if sandbox_active else None
    return interpreter


__all__ = [
    "ScriptedLM",
    "chat_response",
    "make_mock_interpreter",
    "make_modal_interpreter",
    "patch_child_module",
]
