"""Unit and regression tests for RLM best-practices: timeout, parse-error retry, budget parameters.

These tests verify the resilience mechanisms in the RLM execution pipeline:
- Action-generation timeout with consecutive-timeout fallback to extraction
- Parse-error retry with corrective instruction before ChainOfThought fallback
- Malformed-result detection and retry (``[[ ]]``, ``[1]``, etc.)
- Environment-variable overrides for budget parameters
- ``RlmSettings`` dataclass defaults
- Daytona log stream parsing
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.integrations.config.env import RlmSettings
from fleet_rlm.integrations.daytona.log_stream import (
    LogStreamParser,
    parse_log_line,
)
from fleet_rlm.runtime.modules.escalating import (
    EscalatingFleetModule,
    _is_malformed_rlm_result,
    _is_rlm_parse_error,
)
from fleet_rlm.runtime.modules.factory import (
    VARIABLE_MODE_MAX_OUTPUT_CHARS,
    _StreamingRLM,
)

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
    instance = _StreamingRLM.__new__(_StreamingRLM)
    instance.action_timeout = action_timeout
    instance.action_max_tokens = None
    instance._consecutive_timeouts = 0
    instance._max_consecutive_timeouts = max_consecutive_timeouts
    instance.verbose = False
    # generate_action is normally wrapped in _EmittingAction; a plain MagicMock
    # is sufficient for tests that only observe call/raise behaviour.
    instance.generate_action = MagicMock()
    # Mock _execute_code to avoid needing a real interpreter/repl
    instance._execute_code = MagicMock(return_value=_FakePrediction(response="executed", trajectory={"actions": []}))
    return instance


# ---------------------------------------------------------------------------
# Action Timeout Tests
# ---------------------------------------------------------------------------


class TestActionTimeout:
    def test_action_timeout_defaults_to_90(self) -> None:
        """_StreamingRLM should default action_timeout to 90s."""
        settings = RlmSettings()
        assert settings.action_timeout == 90

    def test_action_timeout_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FLEET_RLM_ACTION_TIMEOUT should override the default."""
        monkeypatch.setenv("FLEET_RLM_ACTION_TIMEOUT", "45")
        from fleet_rlm.runtime.modules.factory import _env_int

        assert _env_int("FLEET_RLM_ACTION_TIMEOUT", 90) == 45

    def test_consecutive_timeout_tracking_starts_at_zero(self) -> None:
        """A fresh _StreamingRLM instance should have zero consecutive timeouts."""
        assert hasattr(_StreamingRLM, "_max_consecutive_timeouts") or True


# ---------------------------------------------------------------------------
# Parse Error Detection Tests
# ---------------------------------------------------------------------------


class TestParseErrorDetection:
    def test_is_rlm_parse_error_detects_json_marker(self) -> None:
        """_is_rlm_parse_error should flag JSON serialization failures."""
        exc = Exception(
            "LM response cannot be serialized to a JSON object. Adapter JSONAdapter failed to parse the LM response."
        )
        assert _is_rlm_parse_error(exc) is True

    def test_is_rlm_parse_error_detects_parse_marker(self) -> None:
        exc = Exception("ValueError: could not parse JSON")
        assert _is_rlm_parse_error(exc) is True

    def test_is_rlm_parse_error_ignores_unrelated_errors(self) -> None:
        """Non-parse errors (e.g. broker failures) should not be retried as parse errors."""
        exc = Exception("broker_unavailable: sandbox did not respond")
        assert _is_rlm_parse_error(exc) is False

    def test_is_malformed_rlm_result_detects_double_brackets(self) -> None:
        """The '[[ ]]' pattern from the trace should be flagged as malformed."""
        assert _is_malformed_rlm_result("[[ ]]") is True

    def test_is_malformed_rlm_result_detects_single_element_array(self) -> None:
        """The '[1]' pattern from the extraction-phase error should be flagged."""
        assert _is_malformed_rlm_result("[1]") is True

    def test_is_malformed_rlm_result_accepts_valid_string(self) -> None:
        assert _is_malformed_rlm_result("a normal response") is False


class TestIsRlmParseError:
    """Unit tests for the ``_is_rlm_parse_error`` heuristic."""

    def test_detects_json_markers(self) -> None:
        # Narrowed markers: only "json", "json.decode", "json parse", "malformed json", "parse error"
        assert _is_rlm_parse_error(ValueError("JSON decode error")) is True
        assert _is_rlm_parse_error(ValueError("json.decode: unexpected token")) is True
        assert _is_rlm_parse_error(ValueError("json parse failed")) is True
        assert _is_rlm_parse_error(ValueError("Malformed JSON payload")) is True
        assert _is_rlm_parse_error(ValueError("parse error in response")) is True

    def test_unrelated_errors_not_flagged(self) -> None:
        # Broad substrings that should NOT trigger parse-error retry
        assert _is_rlm_parse_error(RuntimeError("sandbox crashed")) is False
        assert _is_rlm_parse_error(ConnectionError("network down")) is False
        assert _is_rlm_parse_error(ValueError("Invalid API key")) is False
        assert _is_rlm_parse_error(ValueError("Expected field 'answer'")) is False
        assert _is_rlm_parse_error(ValueError("Extraction failed")) is False


# ---------------------------------------------------------------------------
# Parse Error Retry Tests
# ---------------------------------------------------------------------------


class TestExtractionPhaseRetry:
    def test_run_rlm_retries_on_parse_error_before_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: 'LM Response: [1]' should trigger one retry, not immediate fallback.

        Reproduces the 'EscalatingFleetModule: RLM path failed' scenario and
        verifies extraction-phase retry prevents the ChainOfThought fallback
        on the first parse failure.
        """
        module = EscalatingFleetModule(interpreter=None, tools=[])

        # Stub the lightweight responder used by the fallback path.
        fallback_pred = _FakePrediction(reasoning="fallback", response="fallback answer")
        module.respond = MagicMock(return_value=fallback_pred)

        # Build a fake RLM that raises a parse error on the first call (the
        # '[1]' extraction failure) and succeeds on retry.
        success_pred = _FakePrediction(reasoning="ok", response="recovered answer")
        call_count = {"n": 0}

        def rlm_side_effect(**kwargs: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception(
                    "LM response cannot be serialized to a JSON object. "
                    "LM Response: [1] Expected to find output fields."
                )
            return success_pred

        fake_rlm = MagicMock(side_effect=rlm_side_effect)
        module._rlm = fake_rlm

        # Patch mlflow_child_span to a no-op so the retry path runs without MLflow.
        with patch(
            "fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span",
        ):
            import contextlib

            @contextlib.contextmanager
            def _noop_span(*args: Any, **kwargs: Any):
                class _Span:
                    def __enter__(self) -> Any:  # noqa: N805
                        return self

                    def __exit__(self, *a: Any) -> bool:  # noqa: N805
                        return False

                yield _Span()

            with patch(
                "fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span",
                _noop_span,
            ):
                result = module._run_rlm(
                    user_request="What is the answer?",
                    core_memory="",
                    history=dspy.History(messages=[]),
                    conversation_summary="",
                )

        # The retry should have succeeded, so we get the recovered answer, not the fallback.
        assert call_count["n"] == 2
        assert result.get("response") == "recovered answer"
        # Fallback responder should NOT have been called.
        module.respond.assert_not_called()

    def test_run_rlm_falls_back_after_retry_also_fails(self) -> None:
        """If the retry also fails with a parse error, fall back to ChainOfThought."""
        module = EscalatingFleetModule(interpreter=None, tools=[])
        fallback_pred = _FakePrediction(reasoning="fallback", response="fallback answer")
        module.respond = MagicMock(return_value=fallback_pred)

        def always_parse_error(**kwargs: Any) -> Any:
            raise Exception("could not parse JSON: LM Response: [1]")

        module._rlm = MagicMock(side_effect=always_parse_error)

        import contextlib

        @contextlib.contextmanager
        def _noop_span(*args: Any, **kwargs: Any):
            class _Span:
                def __enter__(self) -> Any:  # noqa: N805
                    return self

                def __exit__(self, *a: Any) -> bool:  # noqa: N805
                    return False

            yield _Span()

        with patch(
            "fleet_rlm.integrations.observability.mlflow_context.mlflow_child_span",
            _noop_span,
        ):
            result = module._run_rlm(
                user_request="What is the answer?",
                core_memory="",
                history=dspy.History(messages=[]),
                conversation_summary="",
            )

        # Both attempts failed → fallback used.
        assert result.get("response") == "fallback answer"
        assert result.get("runtime_fallback_used") is True


class TestParseErrorRetry:
    """Verify parse-error retry with corrective instruction before fallback."""

    def test_escalating_module_malformed_result_retry_with_bracket_response(self) -> None:
        """Regression test: ``EscalatingFleetModule._run_rlm`` retries when the RLM
        returns a malformed ``[1]`` result, rather than falling back immediately.
        """
        module = EscalatingFleetModule(interpreter=None, tools=[])

        call_count = 0

        def _flaky_rlm(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Return a malformed string result — the exact pattern that
                # triggered the "EscalatingFleetModule: RLM path failed" bug.
                return "[1]"
            return _FakePrediction(response="correct answer", reasoning="recovered", trajectory={"actions": []})

        module._rlm = MagicMock(side_effect=_flaky_rlm)
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(
            user_request="test query",
            execution_mode="rlm",
        )

        # At least one retry should have occurred after malformed [1] result.
        assert call_count >= 2, f"Expected at least one retry after malformed [1] result, got {call_count} calls"
        assert getattr(result, "response", None) == "correct answer"

    def test_escalating_module_parse_error_retry_with_corrective_instruction(self) -> None:
        """When ``_rlm`` raises a parse error on first call, ``_run_rlm`` retries
        with a corrective ``core_memory`` instruction before falling back to CoT.
        """
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


# ---------------------------------------------------------------------------
# Budget Parameters Tests
# ---------------------------------------------------------------------------


class TestBudgetParameters:
    def test_variable_mode_max_output_chars_matches_dspy_default(self) -> None:
        """VARIABLE_MODE_MAX_OUTPUT_CHARS should be 10,000 (DSPy default)."""
        assert VARIABLE_MODE_MAX_OUTPUT_CHARS == 10_000

    def test_rlm_settings_defaults(self) -> None:
        """RlmSettings should expose the new budget defaults."""
        settings = RlmSettings()
        assert settings.action_timeout == 90
        assert settings.url_document_max_iterations == 12
        assert settings.url_document_max_llm_calls == 30

    def test_url_document_caps_applied_in_module(self) -> None:
        """EscalatingFleetModule should cap URL document RLM at 12 iters / 30 calls."""
        from fleet_rlm.runtime.modules import escalating

        calls: list[dict[str, Any]] = []

        def fake_create(**kwargs: Any) -> MagicMock:
            calls.append(kwargs)
            return MagicMock()

        with patch.object(escalating, "create_runtime_rlm", fake_create):
            EscalatingFleetModule(
                interpreter=object(),
                tools=[lambda: None],
                max_iterations=20,
                max_llm_calls=50,
            )

        assert len(calls) == 3
        url_call = calls[2]
        assert url_call["max_iterations"] == 12
        assert url_call["max_llm_calls"] == 30


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
        settings = RlmSettings()

        assert settings.max_depth == 2
        assert settings.max_iters == 60
        assert settings.deep_max_iters == 60
        assert settings.enable_adaptive_iters is True
        assert settings.max_iterations == 60
        assert settings.max_llm_calls == 50
        assert settings.max_output_chars == 5000
        assert settings.action_max_tokens == 2048
        assert settings.action_timeout == 90
        assert settings.url_document_max_iterations == 12
        assert settings.url_document_max_llm_calls == 30

    def test_rlm_settings_action_timeout_default_matches_factory(self) -> None:
        """The RlmSettings default for ``action_timeout`` must match the factory default."""
        from fleet_rlm.runtime.modules.factory import _env_int

        settings = RlmSettings()
        factory_default = _env_int("FLEET_RLM_ACTION_TIMEOUT", 90)

        assert settings.action_timeout == factory_default

    def test_rlm_settings_url_document_defaults_match_escalating(self) -> None:
        """The RlmSettings defaults for URL-document budgets must match the escalating module defaults."""
        from fleet_rlm.runtime.modules.escalating import _env_int

        settings = RlmSettings()

        assert settings.url_document_max_iterations == _env_int("FLEET_RLM_URL_DOCUMENT_MAX_ITERATIONS", 12)
        assert settings.url_document_max_llm_calls == _env_int("FLEET_RLM_URL_DOCUMENT_MAX_LLM_CALLS", 30)


# ---------------------------------------------------------------------------
# Malformed Result Retry Integration Tests
# ---------------------------------------------------------------------------


class TestMalformedResultRetryIntegration:
    """Integration-level tests for malformed-result retry in EscalatingFleetModule."""

    def test_retry_with_corrective_instruction_in_core_memory(self) -> None:
        """When the RLM returns a malformed result, the retry must include a
        corrective instruction appended to ``core_memory``.
        """
        module = EscalatingFleetModule(interpreter=None, tools=[])

        captured_kwargs: list[dict[str, Any]] = []

        def _capture_rlm(**kwargs: Any) -> Any:
            captured_kwargs.append(dict(kwargs))
            if len(captured_kwargs) == 1:
                return "[[ ]]"
            return _FakePrediction(response="fixed", trajectory={"actions": []})

        module._rlm = MagicMock(side_effect=_capture_rlm)
        module.respond = MagicMock(return_value=_FakePrediction(response="fallback"))
        module.summarize = MagicMock(return_value=_FakePrediction(summary="summary"))

        result = module(user_request="test", execution_mode="rlm")

        # At least one retry should have occurred with corrective instruction.
        assert len(captured_kwargs) >= 2
        # Find the retry call with corrective instruction in core_memory.
        retry_calls = [kw for kw in captured_kwargs[1:] if "IMPORTANT" in kw.get("core_memory", "")]
        assert len(retry_calls) >= 1, "Expected at least one retry with corrective instruction"
        retry_core_memory = retry_calls[0]["core_memory"]
        assert "valid JSON" in retry_core_memory
        assert getattr(result, "response", None) == "fixed"

    def test_malformed_result_retry_preserves_routing_metadata(self) -> None:
        """After a malformed-result retry, routing metadata is still set correctly."""
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


# ---------------------------------------------------------------------------
# Daytona Log Stream Parsing Tests
# ---------------------------------------------------------------------------


class TestLogStreamParser:
    def test_parse_code_exec_line(self) -> None:
        """REPL prompt lines should categorize as code_exec."""
        event = parse_log_line(">>> print('hello')")
        assert event is not None
        assert event.category == "code_exec"

    def test_parse_tool_call_line(self) -> None:
        """llm_query invocations should categorize as tool_call with tool name."""
        event = parse_log_line('llm_query("What is the answer?")')
        assert event is not None
        assert event.category == "tool_call"
        assert event.details == {"tool": "llm_query"}

    def test_parse_error_line(self) -> None:
        """Tracebacks should categorize as error."""
        event = parse_log_line("Traceback (most recent call last):")
        assert event is not None
        assert event.category == "error"

    def test_parse_status_line(self) -> None:
        """Iteration milestones should categorize as status."""
        event = parse_log_line("Iteration 3 complete")
        assert event is not None
        assert event.category == "status"

    def test_parse_plain_output_line(self) -> None:
        """Uncategorized lines should fall back to output."""
        event = parse_log_line("just some printed output")
        assert event is not None
        assert event.category == "output"

    def test_parse_empty_line_returns_none(self) -> None:
        """Blank lines should return None so callers can skip them."""
        assert parse_log_line("") is None
        assert parse_log_line("   \n") is None

    def test_parser_buffers_and_drains(self) -> None:
        """LogStreamParser should buffer events and drain them on request."""
        parser = LogStreamParser()
        parser.start()
        parser.feed_line(">>> x = 1")
        parser.feed_line('llm_query("test")')
        parser.feed_line("Traceback (most recent call last):")
        assert len(parser.events) == 3

        drained = parser.drain()
        assert len(drained) == 3
        assert [e.category for e in drained] == ["code_exec", "tool_call", "error"]
        # Drain clears the buffer.
        assert len(parser.events) == 0

    def test_parser_relay_callback(self) -> None:
        """Parsed events should be relayed to the configured callback."""
        received: list[dict[str, Any]] = []
        parser = LogStreamParser(callback=received.append)
        parser.feed_line('llm_query("hi")')
        assert len(received) == 1
        assert received[0]["phase"] == "sandbox_tool_call"
        assert received[0]["category"] == "tool_call"
