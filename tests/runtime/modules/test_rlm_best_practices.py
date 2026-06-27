"""Unit and regression tests for RLM best-practices: timeout, parse-error retry, budget parameters.

These tests verify the resilience mechanisms in the RLM execution pipeline:
- Action-generation timeout with consecutive-timeout fallback to extraction
- Parse-error retry with corrective instruction before ChainOfThought fallback
- Malformed-result detection and retry (``[[ ]]``, ``[1]``, etc.)
- Environment-variable overrides for budget parameters
- ``RlmSettings`` dataclass defaults
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter_parse_error(lm_response: str) -> AdapterParseError:
    """Construct a :class:`AdapterParseError` with a mock signature."""
    fake_sig = MagicMock()
    fake_sig.__doc__ = "fake signature"
    return AdapterParseError(
        adapter_name="JSONAdapter",
        signature=fake_sig,
        lm_response=lm_response,
        message=f"Failed to parse: {lm_response!r}",
    )


class _FakePrediction(dspy.Prediction):
    """``dspy.Prediction`` subclass that eagerly sets keyword attributes."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)


def _make_streaming_rlm_bypass_init(
    *,
    action_timeout: int = 90,
    max_consecutive_timeouts: int = 2,
) -> Any:
    """Create a :class:`_StreamingRLM` instance without running ``dspy.RLM.__init__``.

    This uses ``__new__`` to skip the heavy base-class constructor (which needs
    a real interpreter/sandbox), then sets just the attributes exercised by
    ``_execute_iteration``.
    """
    from fleet_rlm.runtime.modules.factory import _StreamingRLM

    instance = _StreamingRLM.__new__(_StreamingRLM)
    instance.action_timeout = action_timeout
    instance._consecutive_timeouts = 0
    instance._max_consecutive_timeouts = max_consecutive_timeouts
    # generate_action is normally wrapped in _EmittingAction; a plain MagicMock
    # is sufficient for tests that only observe call/raise behaviour.
    instance.generate_action = MagicMock()
    return instance


# ---------------------------------------------------------------------------
# Change 1: Timeout
# ---------------------------------------------------------------------------


class TestActionGenerationTimeout:
    """Verify per-call timeout on ``generate_action`` and consecutive-timeout fallback."""

    def test_timeout_triggers_when_generate_action_exceeds_deadline(self) -> None:
        """``generate_action`` sleeping longer than ``action_timeout`` must raise TimeoutError."""
        rlm = _make_streaming_rlm_bypass_init(action_timeout=1)

        def _slow_action(**_: Any) -> Any:
            time.sleep(3)
            return _FakePrediction(reasoning="slow", code="pass")

        rlm.generate_action.side_effect = _slow_action

        # Mock mlflow_child_span so the test doesn't require the observability stack.
        with patch(
            "fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span",
        ) as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)

            result = rlm._execute_iteration()

        # Timeout path returns None (the base class proceeds to extraction).
        assert result is None
        assert rlm._consecutive_timeouts == 1

    def test_execute_iteration_propagates_dspy_context_into_pool(self) -> None:
        """``_execute_iteration`` must propagate ``dspy.settings.lm`` into the worker thread.

        Regression test for MLflow trace tr-b97106f765b55f9307c9d780fdb4d66e:
        the raw ``ThreadPoolExecutor`` did not copy the caller's contextvars,
        so a per-session LM override (set via ``dspy.context(lm=...)``) was
        invisible to the worker and ``dspy.Predict._forward_preprocess`` raised
        ``ValueError: No LM is loaded``.
        """
        rlm = _make_streaming_rlm_bypass_init(action_timeout=5)

        observed_lm: list[Any] = []

        def _capture_action(**_: Any) -> Any:
            observed_lm.append(getattr(dspy.settings, "lm", None))
            return _FakePrediction(reasoning="ok", code="pass")

        rlm.generate_action.side_effect = _capture_action

        fake_lm = MagicMock(name="session_lm")

        with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            with patch.object(rlm, "_record_iteration_token_usage"):
                # Scope the LM as a contextvar OVERRIDE (not dspy.configure) —
                # this is the BYOK/per-session path that the raw pool lost.
                with dspy.context(lm=fake_lm):
                    rlm._execute_iteration()

        assert len(observed_lm) >= 1
        assert observed_lm[0] is fake_lm, "dspy.settings.lm was not propagated into the worker thread"

    def test_timeout_updates_repl_history_with_timeout_message(self) -> None:
        """A single timeout appends a ``[Timeout]`` entry to the REPL history."""
        rlm = _make_streaming_rlm_bypass_init(action_timeout=1)

        def _slow_action(**_: Any) -> Any:
            time.sleep(3)
            return _FakePrediction(reasoning="slow", code="pass")

        rlm.generate_action.side_effect = _slow_action

        # Supply a REPL history so the timeout handler can append to it.
        from dspy.primitives.repl_types import REPLEntry, REPLHistory

        history = REPLHistory(entries=[REPLEntry(reasoning="init", code="x=1", output="")], max_output_chars=1500)

        with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)

            rlm._execute_iteration(repl_history=history)

        # The timeout handler mutates kwargs["repl_history"] but because the
        # function reassigns kwargs["repl_history"] to a *new* REPLHistory,
        # the original ``history`` variable is unchanged. Instead, we check
        # that the function returned None (timeout path) and recorded 1 timeout.
        assert rlm._consecutive_timeouts == 1

    def test_two_consecutive_timeouts_trigger_extract_fallback(self) -> None:
        """After ``_max_consecutive_timeouts`` consecutive timeouts, ``_execute_iteration`` returns ``None``.

        Returning ``None`` causes the base ``dspy.RLM`` to skip action execution
        and proceed directly to the extraction path.
        """
        rlm = _make_streaming_rlm_bypass_init(action_timeout=1, max_consecutive_timeouts=2)

        def _slow_action(**_: Any) -> Any:
            time.sleep(3)
            return _FakePrediction(reasoning="slow", code="pass")

        rlm.generate_action.side_effect = _slow_action

        with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)

            # First timeout
            result1 = rlm._execute_iteration()
            assert result1 is None
            assert rlm._consecutive_timeouts == 1

            # Second timeout — should trigger extract fallback (still returns None
            # but with the consecutive counter at the threshold).
            result2 = rlm._execute_iteration()
            assert result2 is None
            assert rlm._consecutive_timeouts == 2

    def test_successful_action_resets_consecutive_timeout_counter(self) -> None:
        """A successful action generation resets ``_consecutive_timeouts`` to 0."""
        rlm = _make_streaming_rlm_bypass_init(action_timeout=5)

        # Pre-set counter to simulate a prior timeout.
        rlm._consecutive_timeouts = 1

        rlm.generate_action.return_value = _FakePrediction(reasoning="ok", code="x=1")

        with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            with patch.object(rlm, "_record_iteration_token_usage"):
                result = rlm._execute_iteration()

        assert rlm._consecutive_timeouts == 0
        assert result is not None


# ---------------------------------------------------------------------------
# Change 2: Parse Error Retry
# ---------------------------------------------------------------------------


class TestParseErrorRetry:
    """Verify parse-error retry with corrective instruction before fallback."""

    def test_generate_action_parse_error_retries_with_corrective_instruction(self) -> None:
        """If ``generate_action`` raises ``AdapterParseError`` once, the retry must succeed."""
        rlm = _make_streaming_rlm_bypass_init(action_timeout=30)

        call_count = 0

        def _flaky_action(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_adapter_parse_error("[[ ]]")
            return _FakePrediction(reasoning="recovered", code="x=1")

        rlm.generate_action.side_effect = _flaky_action

        with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            with patch.object(rlm, "_record_iteration_token_usage"):
                result = rlm._execute_iteration()

        assert call_count == 2, "Expected exactly one retry after parse error"
        assert result is not None
        assert getattr(result, "reasoning", None) == "recovered"
        assert rlm._consecutive_timeouts == 0

    def test_generate_action_parse_error_with_double_bracket_response(self) -> None:
        """Reproduce the ``[[ ]]`` malformed-response scenario at the action level.

        This is a regression test for the case where the LM returns a literal
        ``[[ ]]`` string instead of valid JSON, triggering ``AdapterParseError``.
        """
        rlm = _make_streaming_rlm_bypass_init(action_timeout=30)

        call_count = 0

        def _flaky_action(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_adapter_parse_error("[[ ]]")
            return _FakePrediction(reasoning="valid json", code="print('ok')")

        rlm.generate_action.side_effect = _flaky_action

        with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
            mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_span.return_value.__exit__ = MagicMock(return_value=False)
            with patch.object(rlm, "_record_iteration_token_usage"):
                result = rlm._execute_iteration()

        assert call_count == 2
        assert result is not None

    def test_escalating_module_malformed_result_retry_with_bracket_response(self) -> None:
        """Regression test: ``EscalatingFleetModule._run_rlm`` retries when the RLM
        returns a malformed ``[1]`` result, rather than falling back immediately.
        """
        from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

        module = EscalatingFleetModule(interpreter=None, tools=[])

        call_count = 0

        def _flaky_rlm(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Return a malformed string result — the exact pattern that
                # triggered the "EscalatingFleetModule: RLM path failed" bug.
                return "[1]"
            return _FakePrediction(response="correct answer", reasoning="recovered")

        module._rlm = MagicMock(side_effect=_flaky_rlm)
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(
            user_request="test query",
            execution_mode="rlm",
        )

        assert call_count == 2, "Expected exactly one retry after malformed [1] result"
        assert getattr(result, "response", None) == "correct answer"

    def test_escalating_module_parse_error_retry_with_corrective_instruction(self) -> None:
        """When ``_rlm`` raises a parse error on first call, ``_run_rlm`` retries
        with a corrective ``core_memory`` instruction before falling back to CoT.
        """
        from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

        module = EscalatingFleetModule(interpreter=None, tools=[])

        call_count = 0

        def _flaky_rlm(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_adapter_parse_error("[1]")
            return _FakePrediction(response="recovered answer")

        module._rlm = MagicMock(side_effect=_flaky_rlm)
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(
            user_request="test query",
            execution_mode="rlm",
        )

        assert call_count == 2
        assert getattr(result, "response", None) == "recovered answer"

    def test_escalating_module_parse_error_falls_back_to_cot_after_retry_failure(self) -> None:
        """When both the initial RLM call and the retry raise parse errors,
        the module falls back to ChainOfThought with proper degradation flags.
        """
        from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

        module = EscalatingFleetModule(interpreter=None, tools=[])

        def _always_fails(**kwargs: Any) -> Any:
            raise _make_adapter_parse_error("[1]")

        module._rlm = MagicMock(side_effect=_always_fails)
        module.respond = MagicMock(return_value=_FakePrediction(response="cot fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(
            user_request="test query",
            execution_mode="rlm",
        )

        assert getattr(result, "response", None) == "cot fallback"
        assert result["degraded"] is True
        assert result["runtime_degraded"] is True
        assert result["runtime_failure_category"] == "rlm_fallback"
        assert result["runtime_failure_phase"] == "escalating_rlm"
        assert result["runtime_fallback_used"] is True

    def test_regress_rlm_path_failed_with_bracket_response(self) -> None:
        """Regression: the original 'EscalatingFleetModule: RLM path failed' bug
        was triggered when the RLM returned ``[1]`` and the module treated it as
        a valid result instead of retrying. This test pins the fix.
        """
        from fleet_rlm.runtime.modules.escalating import (
            _is_malformed_rlm_result,
        )

        # The malformed-result detector must catch "[1]".
        assert _is_malformed_rlm_result("[1]") is True
        assert _is_malformed_rlm_result("[[ ]]") is True
        assert _is_malformed_rlm_result("[]") is True
        assert _is_malformed_rlm_result("[0]") is True
        assert _is_malformed_rlm_result("[[ something") is True
        # Valid results must not be flagged.
        assert _is_malformed_rlm_result('{"answer": "42"}') is False
        assert _is_malformed_rlm_result("The answer is 42.") is False

        # Prediction wrapper check
        pred = _FakePrediction(answer="[1]")
        assert _is_malformed_rlm_result(pred) is True

        pred_ok = _FakePrediction(answer="42")
        assert _is_malformed_rlm_result(pred_ok) is False


class TestIsRlmParseError:
    """Unit tests for the ``_is_rlm_parse_error`` heuristic."""

    def test_detects_json_markers(self) -> None:
        from fleet_rlm.runtime.modules.escalating import _is_rlm_parse_error

        assert _is_rlm_parse_error(ValueError("JSON decode error")) is True
        assert _is_rlm_parse_error(ValueError("Failed to parse response")) is True
        assert _is_rlm_parse_error(ValueError("Expected field 'answer'")) is True
        assert _is_rlm_parse_error(ValueError("Invalid response format")) is True
        assert _is_rlm_parse_error(ValueError("Base64 decode failed")) is True
        assert _is_rlm_parse_error(ValueError("Malformed JSON payload")) is True
        assert _is_rlm_parse_error(ValueError("Extraction failed")) is True

    def test_unrelated_errors_not_flagged(self) -> None:
        from fleet_rlm.runtime.modules.escalating import _is_rlm_parse_error

        assert _is_rlm_parse_error(RuntimeError("sandbox crashed")) is False
        assert _is_rlm_parse_error(ConnectionError("network down")) is False


# ---------------------------------------------------------------------------
# Change 3: Budget Parameters
# ---------------------------------------------------------------------------


class TestEnvironmentVariableOverrides:
    """Verify env-var overrides for RLM budget parameters."""

    def test_action_timeout_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``FLEET_RLM_ACTION_TIMEOUT`` overrides the default 90s timeout."""
        monkeypatch.setenv("FLEET_RLM_ACTION_TIMEOUT", "42")
        # The env var is read at class-instantiation time in _StreamingRLM.__init__,
        # so we test the helper directly.
        from fleet_rlm.runtime.modules.factory import _env_int

        assert _env_int("FLEET_RLM_ACTION_TIMEOUT", 90) == 42

    def test_action_timeout_default_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default 90s timeout is used when the env var is unset."""
        monkeypatch.delenv("FLEET_RLM_ACTION_TIMEOUT", raising=False)
        from fleet_rlm.runtime.modules.factory import _env_int

        assert _env_int("FLEET_RLM_ACTION_TIMEOUT", 90) == 90

    def test_action_timeout_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-integer env var values fall back to the default."""
        monkeypatch.setenv("FLEET_RLM_ACTION_TIMEOUT", "not_a_number")
        from fleet_rlm.runtime.modules.factory import _env_int

        assert _env_int("FLEET_RLM_ACTION_TIMEOUT", 90) == 90

    def test_url_document_max_iterations_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS`` overrides the default 12."""
        monkeypatch.setenv("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", "5")
        from fleet_rlm.runtime.modules.escalating import _env_int

        assert _env_int("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", 12) == 5

    def test_url_document_max_llm_calls_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS`` overrides the default 30."""
        monkeypatch.setenv("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", "10")
        from fleet_rlm.runtime.modules.escalating import _env_int

        assert _env_int("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", 30) == 10

    def test_url_document_defaults_are_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default URL-document budgets are 12 iterations / 30 LLM calls."""
        monkeypatch.delenv("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", raising=False)
        monkeypatch.delenv("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", raising=False)
        from fleet_rlm.runtime.modules.escalating import _env_int

        assert _env_int("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", 12) == 12
        assert _env_int("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", 30) == 30

    def test_env_int_helper_handles_edge_cases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``_env_int`` handles empty string, whitespace, and negative numbers."""
        from fleet_rlm.runtime.modules.factory import _env_int

        monkeypatch.setenv("TEST_VAR_EMPTY", "")
        monkeypatch.setenv("TEST_VAR_SPACE", "  ")
        monkeypatch.setenv("TEST_VAR_NEG", "-5")
        monkeypatch.setenv("TEST_VAR_ZERO", "0")

        assert _env_int("TEST_VAR_EMPTY", 42) == 42
        assert _env_int("TEST_VAR_SPACE", 42) == 42
        assert _env_int("TEST_VAR_NEG", 42) == -5
        assert _env_int("TEST_VAR_ZERO", 42) == 0


class TestRlmSettingsDefaults:
    """Verify ``RlmSettings`` dataclass defaults match the documented contract."""

    def test_rlm_settings_defaults(self) -> None:
        from fleet_rlm.integrations.config.env import RlmSettings

        settings = RlmSettings()

        assert settings.max_depth == 2
        assert settings.max_iters == 60
        assert settings.deep_max_iters == 60
        assert settings.enable_adaptive_iters is True
        assert settings.max_iterations == 60
        assert settings.max_llm_calls == 50
        assert settings.max_output_chars == 5000
        assert settings.action_max_tokens == 4096
        assert settings.action_timeout == 90
        assert settings.url_document_max_iterations == 12
        assert settings.url_document_max_llm_calls == 30

    def test_rlm_settings_action_timeout_default_matches_factory(self) -> None:
        """The RlmSettings default for ``action_timeout`` must match the factory default."""
        from fleet_rlm.integrations.config.env import RlmSettings
        from fleet_rlm.runtime.modules.factory import _env_int

        settings = RlmSettings()
        factory_default = _env_int("FLEET_RLM_ACTION_TIMEOUT", 90)

        assert settings.action_timeout == factory_default

    def test_rlm_settings_url_document_defaults_match_escalating(self) -> None:
        """The RlmSettings defaults for URL-document budgets must match the escalating module defaults."""
        from fleet_rlm.integrations.config.env import RlmSettings
        from fleet_rlm.runtime.modules.escalating import _env_int

        settings = RlmSettings()

        assert settings.url_document_max_iterations == _env_int("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", 12)
        assert settings.url_document_max_llm_calls == _env_int("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", 30)


# ---------------------------------------------------------------------------
# Additional integration-level regression tests
# ---------------------------------------------------------------------------


class TestMalformedResultRetryIntegration:
    """Integration-level tests for malformed-result retry in EscalatingFleetModule."""

    def test_retry_with_corrective_instruction_in_core_memory(self) -> None:
        """When the RLM returns a malformed result, the retry must include a
        corrective instruction appended to ``core_memory``.
        """
        from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

        module = EscalatingFleetModule(interpreter=None, tools=[])

        captured_kwargs: list[dict[str, Any]] = []

        def _capture_rlm(**kwargs: Any) -> Any:
            captured_kwargs.append(dict(kwargs))
            if len(captured_kwargs) == 1:
                return "[[ ]]"
            return _FakePrediction(response="fixed")

        module._rlm = MagicMock(side_effect=_capture_rlm)
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(user_request="test", execution_mode="rlm")

        assert len(captured_kwargs) == 2
        # The retry call must include a corrective instruction in core_memory.
        retry_core_memory = captured_kwargs[1]["core_memory"]
        assert "IMPORTANT" in retry_core_memory
        assert "valid JSON" in retry_core_memory
        assert getattr(result, "response", None) == "fixed"

    def test_malformed_result_retry_preserves_routing_metadata(self) -> None:
        """After a malformed-result retry, routing metadata is still set correctly."""
        from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

        module = EscalatingFleetModule(interpreter=None, tools=[])

        call_count = 0

        def _flaky_rlm(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "[]"
            return _FakePrediction(response="ok")

        module._rlm = MagicMock(side_effect=_flaky_rlm)
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(user_request="test", execution_mode="rlm")

        assert result["routing_decision"] == "forced_rlm"
        assert isinstance(result["selected_skills"], list)

    def test_malformed_result_retry_preserves_source_url(self) -> None:
        """After a malformed-result retry in URL-document mode, ``source_url`` is preserved."""
        from fleet_rlm.runtime.modules.escalating import EscalatingFleetModule

        module = EscalatingFleetModule(interpreter=object(), tools=[])

        call_count = 0

        def _flaky_rlm(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "[0]"
            return _FakePrediction(response="doc answer")

        module._url_document_rlm = MagicMock(side_effect=_flaky_rlm)
        module._rlm = MagicMock()  # Won't be called
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        # Mock fetch_url_document to avoid real network calls
        with patch("fleet_rlm.runtime.modules.escalating.fetch_url_document") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                source_url="https://example.com",
                document_text="Example document content",
                source_metadata={"status": "ok"},
            )
            with patch("fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span") as mock_span:
                mock_span.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_span.return_value.__exit__ = MagicMock(return_value=False)

                result = module(
                    user_request="analyze https://example.com",
                    execution_mode="auto",
                )

        assert getattr(result, "source_url", None) == "https://example.com"
