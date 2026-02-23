"""Unit tests for PostHog analytics callback behavior."""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.analytics import configure_analytics
from fleet_rlm.analytics.config import PostHogConfig
from fleet_rlm.analytics.posthog_callback import PostHogLLMCallback
from fleet_rlm.analytics.trace_context import (
    runtime_distinct_id_context,
    runtime_telemetry_enabled_context,
)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def capture(self, event: str, **kwargs: Any) -> None:
        self.calls.append({"event": event, **kwargs})


class _FakeLM:
    model = "openai/gpt-4o-mini"


def _enabled_config(*, optimization: bool = False) -> PostHogConfig:
    return PostHogConfig(
        enabled=True,
        api_key="phc_test",
        enable_dspy_optimization=optimization,
        input_truncation_chars=100,
        output_truncation_chars=100,
    )


def test_callback_emits_generation_event(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config(), distinct_id="user-123")
    callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
    callback.on_lm_end(
        "call-1",
        {
            "choices": [{"text": "world"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
        None,
    )

    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["event"] == "$ai_generation"
    assert call["distinct_id"] == "user-123"
    props = call["properties"]
    assert props["$ai_model"] == "openai/gpt-4o-mini"
    assert props["$ai_provider"] == "openai"
    assert props["$ai_input_tokens"] == 3
    assert props["$ai_output_tokens"] == 4
    assert props["$ai_total_tokens"] == 7
    assert props["$ai_success"] is True
    assert props["$ai_parent_trace_id"] is None


def test_callback_emits_error_shape(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config(), distinct_id="user-123")
    callback.on_lm_start("call-err", _FakeLM(), {"prompt": "hello"})
    callback.on_lm_end("call-err", None, RuntimeError("boom"))

    assert len(fake_client.calls) == 1
    props = fake_client.calls[0]["properties"]
    assert props["$ai_success"] is False
    assert props["$ai_error_type"] == "RuntimeError"
    assert "boom" in props["$ai_error"]


def test_callback_disabled_no_emission(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(PostHogConfig(enabled=False, api_key="phc_test"))
    callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
    callback.on_lm_end("call-1", {"choices": [{"text": "world"}]}, None)

    assert fake_client.calls == []


def test_optimization_mode_suppresses_emission(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config(optimization=False))
    callback.enter_optimization_mode()
    callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
    callback.on_lm_end("call-1", {"choices": [{"text": "world"}]}, None)

    assert fake_client.calls == []


def test_optimization_mode_can_be_enabled(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config(optimization=True))
    callback.enter_optimization_mode()
    callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
    callback.on_lm_end("call-1", {"choices": [{"text": "world"}]}, None)

    assert len(fake_client.calls) == 1


def test_nested_calls_have_parent_trace_id(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config())
    callback.on_lm_start("parent", _FakeLM(), {"prompt": "p"})
    callback.on_lm_start("child", _FakeLM(), {"prompt": "c"})
    callback.on_lm_end("child", {"choices": [{"text": "child"}]}, None)
    callback.on_lm_end("parent", {"choices": [{"text": "parent"}]}, None)

    child_props = fake_client.calls[0]["properties"]
    parent_props = fake_client.calls[1]["properties"]
    assert child_props["$ai_parent_trace_id"] == parent_props["$ai_trace_id"]


def test_runtime_distinct_id_takes_precedence(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config(), distinct_id="fallback")
    with runtime_distinct_id_context("runtime-user"):
        callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
        callback.on_lm_end("call-1", {"choices": [{"text": "world"}]}, None)

    assert fake_client.calls[0]["distinct_id"] == "runtime-user"


def test_runtime_telemetry_disable_suppresses_emission(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(_enabled_config(), distinct_id="user-123")
    with runtime_telemetry_enabled_context(False):
        callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
        callback.on_lm_end("call-1", {"choices": [{"text": "world"}]}, None)

    assert fake_client.calls == []


def test_configure_analytics_is_idempotent(monkeypatch) -> None:
    old_callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    try:
        monkeypatch.setenv("POSTHOG_ENABLED", "true")
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        monkeypatch.setattr(
            "fleet_rlm.analytics.posthog_callback.get_posthog_client",
            lambda _config: _FakeClient(),
        )

        cb1 = configure_analytics()
        cb2 = configure_analytics()

        assert cb1 is not None
        assert cb1 is cb2
        callbacks = [
            cb
            for cb in (getattr(dspy.settings, "callbacks", []) or [])
            if isinstance(cb, PostHogLLMCallback)
        ]
        assert len(callbacks) == 1
    finally:
        dspy.configure(callbacks=old_callbacks)
