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


def _fake_rlm_factory():
    class _FakeRLM:
        def __init__(self, *, signature, interpreter, **kwargs):
            self.signature = signature
            self.interpreter = interpreter
            self.kwargs = kwargs

        def __call__(self, **call_kwargs):
            # route by input args used by each runner wrapper
            if "question" in call_kwargs:
                return SimpleNamespace(
                    answer="42",
                    trajectory=[{"reasoning": "calc", "code": "print(42)"}],
                    final_reasoning="done",
                )
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
            if "docs" in call_kwargs and "query" in call_kwargs:
                return SimpleNamespace(
                    modules=["m1"],
                    optimizers=["o1"],
                    design_principles="dp",
                    trajectory=[{"reasoning": "arch"}],
                )
            if "docs" in call_kwargs:
                return SimpleNamespace(
                    api_endpoints=["/x"],
                    error_categories={"E": "fix"},
                    total_errors_found=1,
                    headers=["h"],
                    code_blocks=["c"],
                    structure_summary="ok",
                    trajectory=[{"reasoning": "docs"}],
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
        ("run_basic", {"question": "q"}),
        ("run_architecture", {"docs_path": "x.txt", "query": "q"}),
        ("run_api_endpoints", {"docs_path": "x.txt"}),
        ("run_error_patterns", {"docs_path": "x.txt"}),
        ("run_custom_tool", {"docs_path": "x.txt"}),
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
        ("run_basic", {"question": "q", "include_trajectory": False}),
        (
            "run_architecture",
            {"docs_path": "x.txt", "query": "q", "include_trajectory": False},
        ),
        ("run_api_endpoints", {"docs_path": "x.txt", "include_trajectory": False}),
        ("run_error_patterns", {"docs_path": "x.txt", "include_trajectory": False}),
        ("run_custom_tool", {"docs_path": "x.txt", "include_trajectory": False}),
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


def test_runner_includes_final_reasoning_when_available():
    result = runners.run_basic(question="q")
    assert result["final_reasoning"] == "done"
