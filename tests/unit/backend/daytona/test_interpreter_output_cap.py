"""Execution-output bounding contracts for the interpreter feedback loop."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from dspy.primitives.code_interpreter import CodeExecutionError

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.interpreter import (
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    sandbox_backend,
)
from fleet_rlm.rlm._dspy_compat import FinalOutput


def test_large_stdout_is_head_tail_capped_with_marker() -> None:
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), execution_output_cap=400)

    result = interpreter.execute("_out = 'a' * 5000")

    assert isinstance(result, str)
    assert len(result) < 500
    assert result.startswith("a" * 200)
    assert result.endswith("a" * 200)
    assert "characters omitted" in result


def test_output_shorter_than_cap_passes_through_verbatim() -> None:
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), execution_output_cap=400)

    assert interpreter.execute("_out = 'small'") == "small"


def test_final_output_is_never_capped() -> None:
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        output_fields=[{"name": "answer", "type": "str"}],
        execution_output_cap=100,
    )

    result = interpreter.execute("SUBMIT(answer='x' * 5000)")

    assert isinstance(result, FinalOutput)
    assert result.output["answer"] == "x" * 5000


def test_error_feedback_includes_capped_stderr() -> None:
    class _StderrBackend:
        def run(self, code: str, variables: dict[str, object] | None = None) -> BackendExecutionResult:
            del code, variables
            return BackendExecutionResult(
                stdout="", error="NameError: name 'missing' is not defined", stderr="s" * 5000
            )

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=_StderrBackend(), execution_output_cap=300)

    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute("missing + 1")
    result = str(caught.value)

    assert isinstance(result, str)
    assert result.startswith("NameError")
    assert "stderr:" in result
    assert len(result) < 450
    assert "characters omitted" in result


def test_sandbox_backend_retains_timeout_for_broker_execution() -> None:
    backend = sandbox_backend(object(), timeout_s=45)
    assert backend.timeout_s == 45

    unbounded = sandbox_backend(object(), timeout_s=None)
    assert unbounded.timeout_s is None


def test_sandbox_backend_rejects_non_positive_timeout() -> None:
    class _FakeSandbox:
        code_interpreter = object()

    with pytest.raises(Exception, match="positive"):
        sandbox_backend(_FakeSandbox(), timeout_s=0)


def test_settings_expose_execution_bounds_and_toml_defaults() -> None:
    settings = Settings()
    assert settings.rlm_max_execution_output_chars == 4_000
    assert settings.rlm_execution_timeout_s == 120

    toml_path = Path(__file__).resolve().parents[4] / "config" / "fleet.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert data["defaults"]["rlm"]["max_execution_output_chars"] == 4000
    assert data["defaults"]["rlm"]["execution_timeout_s"] == 120
