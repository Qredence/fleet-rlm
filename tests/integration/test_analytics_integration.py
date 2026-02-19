"""Integration-style analytics tests using DSPy callback wrappers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import dspy

from fleet_rlm.analytics.config import PostHogConfig
from fleet_rlm.analytics.posthog_callback import PostHogLLMCallback


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def capture(self, event: str, **kwargs: Any) -> None:
        self.calls.append({"event": event, **kwargs})


class _FakeResponseLM(dspy.LM):
    """LM double that preserves dspy.LM callback hooks without network calls."""

    def __init__(self) -> None:
        super().__init__("openai/fake-model", api_key="sk-test")

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "choices": [{"text": f"echo:{prompt or ''}"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }

    def _process_lm_response(
        self, response: dict[str, Any], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return response


def test_dspy_callback_wrapper_emits_events(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(
        PostHogConfig(enabled=True, api_key="phc_test"),
        distinct_id="integration-user",
    )
    lm = _FakeResponseLM()

    with dspy.context(callbacks=[callback]):
        lm("hello")

    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["event"] == "$ai_generation"


def test_threaded_calls_do_not_leak_trace_state(monkeypatch) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(
        "fleet_rlm.analytics.posthog_callback.get_posthog_client",
        lambda _config: fake_client,
    )

    callback = PostHogLLMCallback(PostHogConfig(enabled=True, api_key="phc_test"))
    lm = _FakeResponseLM()

    old_callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    try:
        dspy.configure(callbacks=[callback])
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda i: lm(f"prompt-{i}"), range(8)))

        assert len(fake_client.calls) == 8
        assert callback._pending_traces == {}
        assert callback._pending_inputs == {}
        assert callback._context_tokens == {}
    finally:
        dspy.configure(callbacks=old_callbacks)
