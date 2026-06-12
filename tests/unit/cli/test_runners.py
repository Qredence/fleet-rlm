from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeChatAgent:
    def __init__(self, result: dict[str, object] | None = None, *, error: Exception | None = None) -> None:
        self.result = result or {"response": "done", "trajectory": [{"step": 1}]}
        self.error = error
        self.chat_messages: list[str] = []
        self.shutdown_called = False

    def __enter__(self) -> _FakeChatAgent:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def chat_turn(self, message: str) -> dict[str, object]:
        self.chat_messages.append(message)
        return dict(self.result)

    async def achat_turn(self, message: str) -> dict[str, object]:
        self.chat_messages.append(message)
        if self.error is not None:
            raise self.error
        return dict(self.result)

    def shutdown(self) -> None:
        self.shutdown_called = True


def test_run_react_chat_once_invokes_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli import runners

    captured: dict[str, object] = {}
    agent = _FakeChatAgent()

    def fake_build_chat_agent(**kwargs):
        captured["build_kwargs"] = kwargs
        return agent

    monkeypatch.setattr(runners, "build_chat_agent", fake_build_chat_agent)
    monkeypatch.setattr(runners, "_runner_trace_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(runners, "mlflow_request_context", lambda context: nullcontext())
    monkeypatch.setattr(
        runners,
        "merge_trace_result_metadata",
        lambda result, response_preview: {**result, "response_preview": response_preview},
    )

    result = runners.run_react_chat_once(
        message="hello",
        docs_path="README.md",
        react_max_iters=3,
        include_trajectory=False,
        delegate_lm="delegate-lm",
    )

    assert captured["build_kwargs"] == {
        "docs_path": "README.md",
        "react_max_iters": 3,
        "delegate_lm": "delegate-lm",
        "env_file": None,
    }
    assert agent.chat_messages == ["hello"]
    assert "trajectory" not in result
    assert result["response"] == "done"
    assert result["response_preview"] == "done"


def test_arun_react_chat_once_shuts_down_agent_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli import runners

    agent = _FakeChatAgent(error=RuntimeError("boom"))
    monkeypatch.setattr(runners, "build_chat_agent", lambda **kwargs: agent)
    monkeypatch.setattr(runners, "_runner_trace_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(runners, "mlflow_request_context", lambda context: nullcontext())

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(runners.arun_react_chat_once(message="hello"))

    assert agent.shutdown_called is True


def test_run_long_context_invokes_interpreter_and_rlm(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli import runners

    captured: dict[str, object] = {}

    class _FakeInterpreter:
        def __init__(self, *, timeout: int, volume_name: str | None) -> None:
            captured["interpreter_kwargs"] = {
                "timeout": timeout,
                "volume_name": volume_name,
            }

        def __enter__(self):
            captured["interpreter_entered"] = True
            return "fake-interpreter"

        def __exit__(self, exc_type, exc, tb) -> bool:
            captured["interpreter_exited"] = True
            return False

    class _FakeRLM:
        def __init__(self, *, signature, interpreter, max_iterations: int, max_llm_calls: int, verbose: bool) -> None:
            captured["rlm_init"] = {
                "signature": signature,
                "interpreter": interpreter,
                "max_iterations": max_iterations,
                "max_llm_calls": max_llm_calls,
                "verbose": verbose,
            }

        def __call__(self, *, document: str, focus: str):
            captured["rlm_call"] = {
                "document": document,
                "focus": focus,
            }
            return SimpleNamespace(
                summary="summary",
                key_points=["first"],
                coverage_pct=88,
                trajectory=[{"step": 1}],
                final_reasoning="reasoned",
            )

    monkeypatch.setattr(runners, "_require_planner_ready", lambda env_file: captured.setdefault("env_file", env_file))
    monkeypatch.setattr(runners, "_runner_trace_context", lambda **kwargs: kwargs)
    monkeypatch.setattr(runners, "mlflow_request_context", lambda context: nullcontext())
    monkeypatch.setattr(
        runners,
        "merge_trace_result_metadata",
        lambda result, response_preview: {**result, "response_preview": response_preview},
    )
    monkeypatch.setattr(runners, "DaytonaInterpreter", _FakeInterpreter)
    monkeypatch.setattr(runners.dspy, "RLM", _FakeRLM)
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "read_text", lambda self: "Document body")

    result = runners.run_long_context(
        docs_path="docs.md",
        query="What matters?",
        timeout=12,
        volume_name="shared-volume",
        max_iterations=4,
        max_llm_calls=7,
        verbose=False,
    )

    assert captured["interpreter_kwargs"] == {"timeout": 12, "volume_name": "shared-volume"}
    assert captured["rlm_init"]["interpreter"] == "fake-interpreter"  # ty: ignore[not-subscriptable]
    assert captured["rlm_init"]["max_iterations"] == 4  # ty: ignore[not-subscriptable]
    assert captured["rlm_init"]["max_llm_calls"] == 7  # ty: ignore[not-subscriptable]
    assert captured["rlm_init"]["verbose"] is False  # ty: ignore[not-subscriptable]
    assert captured["rlm_call"] == {"document": "Document body", "focus": "What matters?"}
    assert result["summary"] == "summary"
    assert result["trajectory_steps"] == 1
    assert result["response_preview"] == "summary"
