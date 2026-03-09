from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm import runners


class _FakeInterpreter:
    def shutdown(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeChatAgent:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def chat_turn(self, message: str) -> dict[str, object]:
        return {
            "assistant_response": f"echo:{message}",
            "trajectory": {"tool_name_0": "finish"},
            "history_turns": 1,
        }


def _fake_rlm_factory():
    class _FakeRLM:
        def __init__(self, *, signature, interpreter, **kwargs):
            self.signature = signature
            self.interpreter = interpreter
            self.kwargs = kwargs

        def __call__(self, **call_kwargs):
            if "document" in call_kwargs and "query" in call_kwargs:
                return SimpleNamespace(
                    findings=["f1"],
                    answer="a1",
                    sections_examined=3,
                    trajectory=[{"reasoning": "analyze"}],
                    final_reasoning="analysis done",
                )
            if "document" in call_kwargs and "focus" in call_kwargs:
                return SimpleNamespace(
                    summary="s1",
                    key_points=["k1"],
                    coverage_pct=88,
                    trajectory=[{"reasoning": "summarize"}],
                )
            raise AssertionError(f"Unexpected call kwargs: {call_kwargs}")

    return _FakeRLM


@pytest.fixture(autouse=True)
def _patch_runners(monkeypatch):
    monkeypatch.setattr(runners, "_require_planner_ready", lambda env_file=None: None)
    monkeypatch.setattr(runners, "_read_docs", lambda path: "doc text\nline2")
    monkeypatch.setattr(runners, "_interpreter", lambda **kwargs: _FakeInterpreter())
    monkeypatch.setattr(runners.dspy, "RLM", _fake_rlm_factory())


@pytest.mark.parametrize(
    ("fn_name", "kwargs"),
    [
        ("run_long_context", {"docs_path": "x.txt", "query": "q", "mode": "analyze"}),
        ("run_long_context", {"docs_path": "x.txt", "query": "q", "mode": "summarize"}),
    ],
)
def test_runners_include_trajectory_by_default(fn_name, kwargs):
    fn = getattr(runners, fn_name)
    result = fn(**kwargs)
    assert "trajectory_steps" in result
    assert "trajectory" in result
    assert isinstance(result["trajectory"], list)


@pytest.mark.parametrize(
    ("fn_name", "kwargs"),
    [
        (
            "run_long_context",
            {
                "docs_path": "x.txt",
                "query": "q",
                "mode": "analyze",
                "include_trajectory": False,
            },
        ),
        (
            "run_long_context",
            {
                "docs_path": "x.txt",
                "query": "q",
                "mode": "summarize",
                "include_trajectory": False,
            },
        ),
    ],
)
def test_runners_can_suppress_trajectory(fn_name, kwargs):
    fn = getattr(runners, fn_name)
    result = fn(**kwargs)
    assert "trajectory_steps" not in result
    assert "trajectory" not in result
    assert "final_reasoning" not in result


def test_run_react_chat_once_merges_mlflow_metadata(monkeypatch):
    monkeypatch.setattr(
        runners,
        "_build_react_agent_from_options",
        lambda **kwargs: _FakeChatAgent(),
    )
    monkeypatch.setattr(
        "fleet_rlm.analytics.mlflow_integration.trace_result_metadata",
        lambda response_preview=None: {
            "mlflow_trace_id": "trace-123",
            "mlflow_client_request_id": "req-123",
        },
    )

    result = runners.run_react_chat_once(message="hello")

    assert result["assistant_response"] == "echo:hello"
    assert result["mlflow_trace_id"] == "trace-123"
    assert result["mlflow_client_request_id"] == "req-123"
