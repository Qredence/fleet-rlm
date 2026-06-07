from __future__ import annotations

import builtins
import logging
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


def test_warn_if_persisted_scorers_active_logs_actionable_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fleet_rlm.integrations.observability.auto_assessment import warn_if_persisted_scorers_active
    from fleet_rlm.integrations.observability.config import MlflowConfig

    list_calls: list[str | None] = []

    def list_scorers(*, experiment_id: str | None = None) -> list[object]:
        list_calls.append(experiment_id)
        return [
            SimpleNamespace(name="Trace Judge", status="STARTED", sample_rate=1.0),
            {"scorer_name": "retired-scorer", "status": "STARTED", "sample_rate": 0.25},
            SimpleNamespace(name="stopped-scorer", status="STOPPED", sample_rate=0.0),
        ]

    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(list_scorers=list_scorers),
        get_experiment_by_name=lambda name: SimpleNamespace(experiment_id="exp-123"),
    )

    with caplog.at_level(logging.WARNING):
        count = warn_if_persisted_scorers_active(
            MlflowConfig(enable_auto_assessment=False, experiment="fleet-rlm-test"),
            mlflow=fake_mlflow,
        )

    assert count == 2
    assert list_calls == ["exp-123"]
    assert "Trace Judge" in caplog.text
    assert "retired-scorer" in caplog.text
    assert "stopped-scorer" not in caplog.text
    assert "scripts/mlflow_cli.py scorers list" in caplog.text
    assert "FLEET_RLM_ENABLE_AUTO_ASSESSMENT" in caplog.text


def test_persisted_scorer_names_uses_short_cache() -> None:
    from fleet_rlm.integrations.observability import auto_assessment
    from fleet_rlm.integrations.observability.auto_assessment import persisted_scorer_names
    from fleet_rlm.integrations.observability.config import MlflowConfig

    auto_assessment._PERSISTED_SCORER_CACHE = None
    calls: list[str | None] = []

    def list_scorers(*, experiment_id: str | None = None) -> list[object]:
        calls.append(experiment_id)
        return [SimpleNamespace(name="Trace Judge", status="STARTED", sample_rate=1.0)]

    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(list_scorers=list_scorers),
        get_experiment_by_name=lambda name: SimpleNamespace(experiment_id="exp-123"),
    )
    config = MlflowConfig(enable_auto_assessment=False, experiment="fleet-rlm-test")

    assert persisted_scorer_names(config, mlflow=fake_mlflow) == ["Trace Judge"]
    assert persisted_scorer_names(config, mlflow=fake_mlflow) == ["Trace Judge"]
    assert calls == ["exp-123"]
    auto_assessment._PERSISTED_SCORER_CACHE = None


def test_persisted_scorer_names_ignores_stopped_scorers() -> None:
    from fleet_rlm.integrations.observability import auto_assessment
    from fleet_rlm.integrations.observability.auto_assessment import persisted_scorer_names
    from fleet_rlm.integrations.observability.config import MlflowConfig

    auto_assessment._PERSISTED_SCORER_CACHE = None

    def list_scorers(*, experiment_id: str | None = None) -> list[object]:
        return [
            SimpleNamespace(name="active", status="ScorerStatus.STARTED", sample_rate=1.0),
            SimpleNamespace(name="stopped", status="ScorerStatus.STOPPED", sample_rate=0.0),
        ]

    fake_mlflow = SimpleNamespace(
        genai=SimpleNamespace(list_scorers=list_scorers),
        get_experiment_by_name=lambda name: SimpleNamespace(experiment_id="exp-123"),
    )

    names = persisted_scorer_names(
        MlflowConfig(enable_auto_assessment=False, experiment="fleet-rlm-test"),
        mlflow=fake_mlflow,
        cache_seconds=0,
    )

    assert names == ["active"]


def test_import_mlflow_clears_partial_import_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_runtime

    calls = 0
    real_import = builtins.__import__
    partial_mlflow = types.ModuleType("mlflow")
    partial_child = types.ModuleType("mlflow.genai")
    good_mlflow = types.ModuleType("mlflow")
    good_mlflow.version = SimpleNamespace(VERSION="3.12.0")

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        nonlocal calls
        if name != "mlflow":
            return real_import(name, *args, **kwargs)
        calls += 1
        if calls == 1:
            sys.modules["mlflow"] = partial_mlflow
            sys.modules["mlflow.genai"] = partial_child
            return partial_mlflow
        sys.modules["mlflow"] = good_mlflow
        return good_mlflow

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert mlflow_runtime._import_mlflow() is good_mlflow
    assert calls == 2
    assert sys.modules["mlflow"] is good_mlflow
    assert "mlflow.genai" not in sys.modules


def test_warn_if_persisted_scorers_active_skips_when_auto_assessment_enabled() -> None:
    from fleet_rlm.integrations.observability.auto_assessment import warn_if_persisted_scorers_active
    from fleet_rlm.integrations.observability.config import MlflowConfig

    def list_scorers(*, experiment_id: str | None = None) -> list[object]:
        raise AssertionError("list_scorers should not run when Fleet auto-assessment is enabled")

    fake_mlflow = SimpleNamespace(genai=SimpleNamespace(list_scorers=list_scorers))

    count = warn_if_persisted_scorers_active(
        MlflowConfig(enable_auto_assessment=True),
        mlflow=fake_mlflow,
    )

    assert count == 0


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
