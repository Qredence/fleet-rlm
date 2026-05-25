from __future__ import annotations

import sys
import threading
import types
from types import SimpleNamespace

import pytest

from tests.fixtures.env import apply_mlflow_env


class _FakeAnalyticsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def capture(self, event: str, **kwargs: object) -> None:
        self.calls.append({"event": event, **kwargs})


class _FakeLM:
    model = "openai/gpt-4o-mini"


class _SettingsStub:
    def __init__(self) -> None:
        self.callbacks: list[object] = []
        self.lock = threading.Lock()


def test_mlflow_config_from_env_parses_runtime_flags(
    clean_runtime_env: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.integrations.observability.config import MlflowConfig

    apply_mlflow_env(clean_runtime_env)
    clean_runtime_env.setenv("MLFLOW_DSPY_LOG_TRACES_FROM_COMPILE", "true")
    clean_runtime_env.setenv("MLFLOW_DSPY_LOG_TRACES_FROM_EVAL", "false")
    clean_runtime_env.setenv("MLFLOW_ENABLE_SPAN_PROCESSORS", "false")
    clean_runtime_env.setenv("FLEET_RLM_ENABLE_AUTO_ASSESSMENT", "true")
    clean_runtime_env.setenv("FLEET_RLM_AUTO_ASSESSMENT_SCORERS", "safety, custom")
    clean_runtime_env.setenv("FLEET_RLM_AUTO_ASSESSMENT_JUDGE_MODEL", "openai/custom-judge")

    config = MlflowConfig.from_env()

    assert config.enabled is True
    assert config.tracking_uri == "http://127.0.0.1:6001"
    assert config.experiment == "fleet-rlm-test"
    assert config.active_model_id == "model-123"
    assert config.dspy_log_traces_from_compile is True
    assert config.dspy_log_traces_from_eval is False
    assert config.enable_auto_assessment is True
    assert config.auto_assessment_scorers == ["safety", "custom"]
    assert config.auto_assessment_judge_model == "openai/custom-judge"
    assert config.enable_span_processors is False


def test_configure_auto_assessment_passes_explicit_judge_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.observability import auto_assessment
    from fleet_rlm.integrations.observability.config import MlflowConfig

    created_schedules: list[dict[str, object]] = []

    class FakeScorer:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeSchedule:
        def __init__(self, **kwargs: object) -> None:
            created_schedules.append(kwargs)

    scorers_module = types.ModuleType("mlflow.genai.scorers")
    for name in (
        "Correctness",
        "Guidelines",
        "RelevanceToQuery",
        "Safety",
        "ToolCallCorrectness",
        "ToolCallEfficiency",
    ):
        setattr(scorers_module, name, FakeScorer)

    monkeypatch.setitem(sys.modules, "mlflow", types.ModuleType("mlflow"))
    monkeypatch.setitem(sys.modules, "mlflow.genai", types.ModuleType("mlflow.genai"))
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_module)
    monkeypatch.setattr(auto_assessment, "_ScorerScheduleConfig", FakeSchedule)
    auto_assessment._SCORER_REGISTRY.clear()

    configured = auto_assessment.configure_auto_assessment(
        MlflowConfig(
            enable_auto_assessment=True,
            auto_assessment_scorers=["tool_efficiency"],
            auto_assessment_judge_model="openai/custom-judge",
            auto_assessment_sample_rate=0.5,
        )
    )

    assert configured is True
    assert len(created_schedules) == 1
    scorer = created_schedules[0]["scorer"]
    assert isinstance(scorer, FakeScorer)
    assert scorer.kwargs["model"] == "openai/custom-judge"
    assert created_schedules[0]["scheduled_scorer_name"] == "fleet_rlm_tool_efficiency"
    assert created_schedules[0]["sample_rate"] == 0.5


def test_mlflow_callback_sets_lm_span_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_runtime

    captured_attributes: dict[str, object] = {}

    class FakeSpan:
        def set_attributes(self, attributes: dict[str, object]) -> None:
            captured_attributes.update(attributes)

    fake_mlflow = SimpleNamespace(get_current_active_span=lambda: FakeSpan())
    monkeypatch.setattr(mlflow_runtime, "_import_mlflow", lambda: fake_mlflow)

    callback = mlflow_runtime.FleetMlflowTraceCallback()
    callback.on_lm_end(
        "call-1",
        {"usage": {"prompt_tokens": 12, "completion_tokens": 5}, "choices": [{"text": "done"}]},
        None,
    )

    assert captured_attributes["mlflow.chat.tokenUsage"] == {
        "input_tokens": 12,
        "output_tokens": 5,
        "total_tokens": 17,
    }
    assert captured_attributes["mlflow.chat.inputTokens"] == 12
    assert captured_attributes["mlflow.chat.outputTokens"] == 5


def test_posthog_config_from_env_and_configure_analytics_is_idempotent(
    clean_runtime_env: pytest.MonkeyPatch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dspy

    from fleet_rlm.integrations.observability import configure_analytics
    from fleet_rlm.integrations.observability.config import PostHogConfig
    from fleet_rlm.integrations.observability.posthog_callback import PostHogLLMCallback

    clean_runtime_env.setenv("POSTHOG_API_KEY", "phc_test")
    clean_runtime_env.setenv("POSTHOG_HOST", "https://eu.i.posthog.com")
    clean_runtime_env.setenv("POSTHOG_FLUSH_INTERVAL", "3.5")
    clean_runtime_env.setenv("POSTHOG_FLUSH_AT", "4")
    settings = _SettingsStub()

    def fake_configure(*, callbacks: list[object]) -> None:
        settings.callbacks = callbacks

    monkeypatch.setattr(dspy, "settings", settings)
    monkeypatch.setattr(dspy, "configure", fake_configure)

    config = PostHogConfig.from_env()
    first = configure_analytics()
    second = configure_analytics()

    assert config.enabled is True
    assert config.host == "https://eu.i.posthog.com"
    assert config.flush_interval == 3.5
    assert config.flush_at == 4
    assert isinstance(first, PostHogLLMCallback)
    assert first is second
    assert len([callback for callback in settings.callbacks if isinstance(callback, PostHogLLMCallback)]) == 1


def test_posthog_callback_sanitizes_generation_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.observability.config import PostHogConfig
    from fleet_rlm.integrations.observability.posthog_callback import PostHogLLMCallback

    fake_client = _FakeAnalyticsClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(
        PostHogConfig(enabled=True, api_key="phc_test", input_truncation_chars=100, output_truncation_chars=100),
        distinct_id="user-123",
    )
    callback.on_lm_start("call-1", _FakeLM(), {"prompt": "Authorization: Bearer abc.def api_key=sk-secret-123"})
    callback.on_lm_end("call-1", {"choices": [{"text": "token=my-secret-token"}]}, None)

    props = fake_client.calls[0]["properties"]
    assert props["$ai_input"] == "Authorization: Bearer ***REDACTED*** api_key=***REDACTED***"  # ty: ignore[not-subscriptable]
    assert props["$ai_output_choices"] == ["token=***REDACTED***"]  # ty: ignore[not-subscriptable]


def test_redact_sensitive_masks_keys_tokens_and_bearer_headers() -> None:
    from fleet_rlm.integrations.observability.sanitization import redact_sensitive

    redacted = redact_sensitive("api_key=sk-abc12345DEF token=my-secret-token Authorization: Bearer abc.def.ghi")

    assert "api_key=***REDACTED***" in redacted
    assert "token=***REDACTED***" in redacted
    assert "Authorization: Bearer ***REDACTED***" in redacted


def test_span_processors_add_metadata_and_swallow_span_failures() -> None:
    from fleet_rlm.integrations.observability.span_processors import build_span_processors, fleet_metadata_processor

    seen: dict[str, object] = {}
    span = SimpleNamespace(set_attributes=lambda attrs: seen.update(attrs))

    processors = build_span_processors(app_env="staging", workspace_id="ws-42")
    processors[0](span)

    assert seen["fleet_rlm.app_env"] == "staging"
    assert seen["fleet_rlm.workspace_id"] == "ws-42"

    processor = fleet_metadata_processor(app_env="local", workspace_id="ws-1", version="1.2.3")
    processor(SimpleNamespace(set_attributes=lambda _attrs: (_ for _ in ()).throw(RuntimeError("boom"))))
