"""Unit tests for PostHog analytics callback behavior."""

from __future__ import annotations

import threading
from importlib import import_module
from types import ModuleType
from typing import Any

import dspy
import pytest

from fleet_rlm.integrations.observability import configure_analytics
from fleet_rlm.integrations.observability.config import PostHogConfig
from fleet_rlm.integrations.observability.posthog_callback import PostHogLLMCallback
from fleet_rlm.integrations.observability.trace_context import (
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


_DSPY_OWNER_THREAD_ERROR = "dspy.settings can only be changed by the thread"


def _restore_dspy_callbacks(callbacks: list[Any]) -> None:
    """Restore DSPy callbacks even when earlier tests configured DSPy in a worker thread."""
    try:
        dspy.configure(callbacks=callbacks)
    except RuntimeError as exc:
        if _DSPY_OWNER_THREAD_ERROR not in str(exc):
            raise

        settings_module = import_module("dspy.dsp.utils.settings")
        settings_lock = getattr(dspy.settings, "lock", None)
        if settings_lock is None:
            msg = "Unable to restore DSPy callbacks after thread-owner RuntimeError; dspy.settings.lock is unavailable."
            raise RuntimeError(msg) from exc

        main_thread_config = getattr(settings_module, "main_thread_config", None)
        if not isinstance(main_thread_config, dict):
            msg = (
                "Unable to restore DSPy callbacks after thread-owner RuntimeError; "
                "dspy.dsp.utils.settings.main_thread_config is unavailable."
            )
            raise RuntimeError(msg) from exc
        with settings_lock:
            main_thread_config["callbacks"] = list(callbacks)


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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(PostHogConfig(enabled=False, api_key="phc_test"))
    callback.on_lm_start("call-1", _FakeLM(), {"prompt": "hello"})
    callback.on_lm_end("call-1", {"choices": [{"text": "world"}]}, None)

    assert fake_client.calls == []


def test_optimization_mode_suppresses_emission(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
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
            "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
            lambda _config: _FakeClient(),
        )

        cb1 = configure_analytics()
        cb2 = configure_analytics()

        assert cb1 is not None
        assert cb1 is cb2
        callbacks = [cb for cb in (getattr(dspy.settings, "callbacks", []) or []) if isinstance(cb, PostHogLLMCallback)]
        assert len(callbacks) == 1
    finally:
        _restore_dspy_callbacks(old_callbacks)


# ===========================================================================
# DSPy thread-owner fallback path
# ===========================================================================


class _SettingsStub:
    """Minimal stand-in for dspy.settings that supports callbacks and an optional lock."""

    def __init__(self, *, callbacks: list[Any], lock: threading.Lock | None) -> None:
        self.callbacks = callbacks
        self.lock = lock


def _make_fake_settings_module(main_thread_config: dict[str, Any] | None = None) -> ModuleType:
    """Return a minimal fake dspy.dsp.utils.settings module."""
    mod = ModuleType("dspy.dsp.utils.settings")
    mod.main_thread_config = main_thread_config  # type: ignore[attr-defined]
    return mod


def _posthog_monkeypatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTHOG_ENABLED", "true")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.posthog_callback.get_posthog_client",
        lambda _config: _FakeClient(),
    )


def test_configure_analytics_uses_settings_lock_on_thread_owner_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_analytics writes callbacks via the settings lock when dspy.configure raises
    the DSPy thread-ownership RuntimeError, so the private DSPy compatibility path is exercised."""
    _posthog_monkeypatches(monkeypatch)

    lock = threading.Lock()
    main_thread_config: dict[str, Any] = {"callbacks": []}
    fake_settings_mod = _make_fake_settings_module(main_thread_config)

    # Replace dspy.settings with a stub that has an empty callback list and a real lock.
    monkeypatch.setattr(dspy, "settings", _SettingsStub(callbacks=[], lock=lock))

    def _raising_configure(**_kw: Any) -> None:
        raise RuntimeError(_DSPY_OWNER_THREAD_ERROR)

    monkeypatch.setattr(dspy, "configure", _raising_configure)

    # Return the fake settings module only for the DSPy-internal path; delegate everything else.
    _real_import_module = import_module

    def _selective_import(name: str, package: str | None = None) -> Any:
        if name == "dspy.dsp.utils.settings":
            return fake_settings_mod
        return _real_import_module(name, package)

    monkeypatch.setattr("fleet_rlm.integrations.observability.import_module", _selective_import)

    result = configure_analytics()

    assert result is not None
    assert isinstance(result, PostHogLLMCallback)
    # Verify the callback was registered under the settings lock via main_thread_config.
    assert any(isinstance(cb, PostHogLLMCallback) for cb in main_thread_config["callbacks"])


def test_configure_analytics_fallback_preserves_main_thread_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback registration should append to the owner-thread callback list instead of
    copying worker-thread callbacks into global DSPy settings."""
    _posthog_monkeypatches(monkeypatch)

    local_callback = object()
    owner_thread_callback = object()
    lock = threading.Lock()
    main_thread_config: dict[str, Any] = {"callbacks": [owner_thread_callback]}
    fake_settings_mod = _make_fake_settings_module(main_thread_config)

    monkeypatch.setattr(dspy, "settings", _SettingsStub(callbacks=[local_callback], lock=lock))

    def _raising_configure(**_kw: Any) -> None:
        raise RuntimeError(_DSPY_OWNER_THREAD_ERROR)

    monkeypatch.setattr(dspy, "configure", _raising_configure)
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.import_module",
        lambda name, package=None: fake_settings_mod,
    )

    result = configure_analytics()

    assert result is not None
    assert main_thread_config["callbacks"][0] is owner_thread_callback
    assert local_callback not in main_thread_config["callbacks"]
    assert any(isinstance(cb, PostHogLLMCallback) for cb in main_thread_config["callbacks"])


def test_configure_analytics_raises_when_settings_lock_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_analytics re-raises with a clear message when dspy.settings.lock is None
    after a thread-owner RuntimeError."""
    _posthog_monkeypatches(monkeypatch)

    # lock=None causes the fallback to fail with an informative RuntimeError.
    monkeypatch.setattr(dspy, "settings", _SettingsStub(callbacks=[], lock=None))

    def _raising_configure(**_kw: Any) -> None:
        raise RuntimeError(_DSPY_OWNER_THREAD_ERROR)

    monkeypatch.setattr(dspy, "configure", _raising_configure)

    with pytest.raises(RuntimeError, match="dspy.settings.lock is unavailable"):
        configure_analytics()


def test_configure_analytics_raises_when_main_thread_config_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_analytics re-raises with a clear message when main_thread_config is not a dict
    after a thread-owner RuntimeError."""
    _posthog_monkeypatches(monkeypatch)

    lock = threading.Lock()
    # main_thread_config=None is not a dict, so the fallback cannot proceed.
    fake_settings_mod = _make_fake_settings_module(main_thread_config=None)

    monkeypatch.setattr(dspy, "settings", _SettingsStub(callbacks=[], lock=lock))

    def _raising_configure(**_kw: Any) -> None:
        raise RuntimeError(_DSPY_OWNER_THREAD_ERROR)

    monkeypatch.setattr(dspy, "configure", _raising_configure)
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.import_module",
        lambda name, package=None: fake_settings_mod,
    )

    with pytest.raises(RuntimeError, match="main_thread_config is unavailable"):
        configure_analytics()


def test_configure_analytics_reraises_unrelated_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_analytics does not suppress RuntimeErrors whose message is unrelated to DSPy
    thread ownership."""
    _posthog_monkeypatches(monkeypatch)

    monkeypatch.setattr(dspy, "settings", _SettingsStub(callbacks=[], lock=None))

    def _raising_configure(**_kw: Any) -> None:
        raise RuntimeError("something completely unrelated")

    monkeypatch.setattr(dspy, "configure", _raising_configure)

    with pytest.raises(RuntimeError, match="something completely unrelated"):
        configure_analytics()
