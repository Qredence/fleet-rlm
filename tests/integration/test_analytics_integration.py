"""Integration-style analytics tests using DSPy callback wrappers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.analytics.config import MlflowConfig, PostHogConfig
from fleet_rlm.analytics.mlflow_integration import (
    MlflowTraceRequestContext,
    flush_mlflow_traces,
    initialize_mlflow,
    mlflow_request_context,
    trace_result_metadata,
)
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


@pytest.mark.filterwarnings(
    "ignore:Parameter 'experiment_ids' is deprecated. Please use 'locations' instead.:FutureWarning"
)
def test_mlflow_integration_captures_real_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("fleet_rlm.analytics.mlflow_integration._INIT_IDENTITY", None)
    monkeypatch.setattr("fleet_rlm.analytics.mlflow_integration._INITIALIZED", False)
    monkeypatch.setattr("fleet_rlm.analytics.mlflow_integration._ACTIVE_CONFIG", None)

    experiment_name = f"fleet-rlm-test-{uuid4().hex}"
    config = MlflowConfig(
        enabled=True,
        tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
        experiment=experiment_name,
    )

    old_callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    try:
        assert initialize_mlflow(config) is True

        lm = _FakeResponseLM()
        with dspy.context(lm=lm):
            with mlflow_request_context(
                MlflowTraceRequestContext(
                    client_request_id="integration-trace-request",
                    session_id="integration-session",
                    user_id="integration-user",
                    app_env="test",
                    request_preview="hello trace",
                )
            ):
                result = lm("hello trace")
                metadata = trace_result_metadata(response_preview=str(result))

        flush_mlflow_traces()

        import mlflow

        experiment = mlflow.get_experiment_by_name(experiment_name)
        assert experiment is not None

        traces = mlflow.search_traces(
            experiment_ids=[experiment.experiment_id],
            max_results=20,
            return_type="list",
        )

        assert metadata["mlflow_client_request_id"] == "integration-trace-request"
        assert metadata["mlflow_trace_id"]
        assert len(traces) == 1

        info = traces[0].to_dict()["info"]
        trace_metadata = info.get("trace_metadata", {})
        assert info["trace_id"] == metadata["mlflow_trace_id"]
        assert info["client_request_id"] == "integration-trace-request"
        assert info["request_preview"] == "hello trace"
        assert "echo:hello trace" in info["response_preview"]
        assert trace_metadata["mlflow.trace.session"] == "integration-session"
        assert trace_metadata["mlflow.trace.user"] == "integration-user"
        assert trace_metadata["app_env"] == "test"
    finally:
        dspy.configure(callbacks=old_callbacks)
