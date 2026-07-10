from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import dspy
import pytest


def _patch_mlflow(monkeypatch) -> list[dict[str, Any]]:
    """Patch ``mlflow_child_span`` with a capturing FakeSpan; return capture list."""
    captured: list[dict[str, Any]] = []

    class _FakeSpan:
        def __init__(self, name: str, span_type: str | None, attributes: dict[str, Any] | None) -> None:
            self.record = {"name": name, "span_type": span_type, "attributes": attributes or {}, "status": "OK"}

        def __enter__(self) -> "_FakeSpan":
            captured.append(self.record)
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_inputs(self, inputs: Any) -> None:
            self.record["inputs"] = inputs

        def set_outputs(self, outputs: Any) -> None:
            self.record["outputs"] = outputs

        def set_status(self, status: str) -> None:
            self.record["status"] = status

    from fleet_rlm.integrations.observability import mlflow_context

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: object(),
        start_span=lambda name, span_type=None, attributes=None: _FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )
    monkeypatch.setattr(
        mlflow_context,
        "mlflow_child_span",
        lambda name, span_type=None, attributes=None, inputs=None: _FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "set_mlflow_span_outputs",
        lambda span, outputs: span.record.__setitem__("outputs", outputs) if span is not None else None,
    )
    return captured


class _RecordingLM:
    """A callable stand-in for a sub-LM that records every prompt it receives."""

    model = "recording-lm"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, prompt: str, **kwargs: Any) -> list[dict[str, str]]:
        self.calls.append(prompt)
        return [{"text": f"ok:{prompt}"}]


def test_llm_query_records_provider_wait_span(monkeypatch) -> None:
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.runtime.execution.llm_query import LLMQueryMixin

    captured: list[dict[str, Any]] = []

    class FakeSpan:
        def __init__(self, name: str, span_type: str | None, attributes: dict[str, Any] | None) -> None:
            self.record = {"name": name, "span_type": span_type, "attributes": attributes or {}, "status": "OK"}

        def __enter__(self) -> "FakeSpan":
            captured.append(self.record)
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_inputs(self, inputs: Any) -> None:
            self.record["inputs"] = inputs

        def set_outputs(self, outputs: Any) -> None:
            self.record["outputs"] = outputs

        def set_status(self, status: str) -> None:
            self.record["status"] = status

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: object(),
        start_span=lambda name, span_type=None, attributes=None: FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    class Host(LLMQueryMixin):
        def __init__(self) -> None:
            self.sub_lm = lambda prompt, **kwargs: [{"text": f"answer: {prompt}"}]
            self.max_llm_calls = 3
            self.llm_call_timeout = 10
            self._llm_call_count = 0
            self._llm_call_lock = threading.Lock()
            self._sub_lm_executor = ThreadPoolExecutor(max_workers=1)
            self._sub_lm_executor_lock = threading.Lock()

    host = Host()
    try:
        result = host.llm_query("summarize this")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert result == "answer: summarize this"
    assert captured[0]["name"] == "fleet_rlm.llm_query"
    assert captured[0]["span_type"] == "LLM"
    assert captured[0]["attributes"]["fleet_rlm.tool_name"] == "llm_query"
    assert captured[0]["attributes"]["fleet_rlm.max_llm_calls"] == "3"
    assert captured[0]["attributes"]["fleet_rlm.llm_call_count"] == "1"
    assert captured[0]["attributes"]["fleet_rlm.llm_calls_remaining"] == "2"
    assert captured[0]["inputs"]["prompt_chars"] == len("summarize this")
    assert captured[0]["outputs"]["status"] == "ok"
    assert captured[0]["outputs"]["response_chars"] == len("answer: summarize this")


def _make_host(sub_lm: Any, *, timeout: int = 10) -> Any:
    """Build a minimal LLMQueryMixin host with the given sub-LM."""
    from fleet_rlm.runtime.execution.llm_query import LLMQueryMixin

    class Host(LLMQueryMixin):
        def __init__(self) -> None:
            self.sub_lm = sub_lm
            self.max_llm_calls = 5
            self.llm_call_timeout = timeout
            self._llm_call_count = 0
            self._llm_call_lock = threading.Lock()
            self._sub_lm_executor = ThreadPoolExecutor(max_workers=2)
            self._sub_lm_executor_lock = threading.Lock()
            self._sub_lm_auth_failed = False
            self._sub_lm_auth_error = None
            self._bounded_sub_lm = None
            self._bounded_sub_lm_base = None

    return Host()


def test_initialize_llm_query_state_resets_auth_failure_flag() -> None:
    from fleet_rlm.runtime.execution.interpreter_support import initialize_llm_query_state

    host = SimpleNamespace(_sub_lm_auth_failed=True)

    initialize_llm_query_state(
        host,
        sub_lm=None,
        max_llm_calls=2,
        llm_call_timeout=5,
    )

    assert host._sub_lm_auth_failed is False
    assert host._sub_lm_auth_error is None


def test_llm_query_succeeds_with_configured_fake_lm(monkeypatch) -> None:
    _patch_mlflow(monkeypatch)

    lm = _RecordingLM()
    host = _make_host(lm)
    try:
        result = host.llm_query("summarize this")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert result == "ok:summarize this"
    assert lm.calls == ["summarize this"]
    assert host._llm_call_count == 1


def test_llm_query_without_configured_lm_has_actionable_error(monkeypatch) -> None:
    captured = _patch_mlflow(monkeypatch)

    host = _make_host(None)
    try:
        with dspy.context(lm=None):
            with pytest.raises(RuntimeError) as exc_info:
                host.llm_query("summarize this")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    message = str(exc_info.value)
    assert "No LM configured for llm_query" in message
    assert "active delegate LLM profile" in message
    assert "DSPY_DELEGATE_LM_MODEL" in message
    assert "DSPY_LLM_API_KEY" in message
    assert host._llm_call_count == 0
    assert captured[-1]["attributes"]["fleet_rlm.auth_preflight_result"] == "missing_lm"
    assert captured[-1]["outputs"]["error_kind"] == "missing_lm"


def test_llm_query_auth_failure_disables_session(monkeypatch) -> None:
    captured = _patch_mlflow(monkeypatch)

    class UnauthorizedLM:
        model = "bad-profile-model"

        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, prompt: str, **kwargs: Any) -> list[dict[str, str]]:
            _ = prompt, kwargs
            self.calls += 1
            raise RuntimeError("401 Unauthorized sk-secret-token")

    lm = UnauthorizedLM()
    host = _make_host(lm)
    try:
        with pytest.raises(RuntimeError, match="Sub-LM authentication failed"):
            host.llm_query("first")
        with pytest.raises(RuntimeError, match="previously failed"):
            host.llm_query("second")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert host._sub_lm_auth_failed is True
    assert host._sub_lm_auth_error is not None
    assert lm.calls == 1
    first_outputs = captured[0]["outputs"]
    assert first_outputs["error_kind"] == "auth_failure"
    assert first_outputs["auth_failure"] is True
    assert first_outputs["provider_status"] == "401"
    assert "sk-secret-token" not in first_outputs["provider_error"]
    assert "***REDACTED***" in first_outputs["provider_error"]
    second_outputs = captured[1]["outputs"]
    assert second_outputs["error_kind"] == "auth_fail_fast"
    assert second_outputs["skipped_calls_due_to_auth_fail_fast"] == 1
    assert host._llm_call_count == 1


def test_query_sub_lm_wraps_sub_lm_in_bounded_lm(monkeypatch) -> None:
    """``_query_sub_lm`` must resolve stateless call-time config overrides using
    ``build_lm_config`` with a per-IO timeout + max_tokens cap.
    """
    from fleet_rlm.runtime import config as rt_config

    _patch_mlflow(monkeypatch)

    build_calls: list[dict[str, Any]] = []

    def fake_build(base: Any, *, max_tokens: int, temperature: float, timeout: float | None):
        build_calls.append(
            {
                "base": base,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": timeout,
            }
        )
        return {"max_tokens": max_tokens, "temperature": temperature, "timeout": timeout}

    monkeypatch.setattr(rt_config, "build_lm_config", fake_build)

    fake_bounded = _RecordingLM()
    host = _make_host(fake_bounded, timeout=12)
    try:
        host.llm_query("hello")
        host.llm_query("again")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    # Config built twice per call (once for span setup, once inside executor thread).
    assert len(build_calls) == 4
    assert build_calls[0]["timeout"] == 12, "timeout must mirror llm_call_timeout"
    assert build_calls[0]["max_tokens"] == 4096
    # The LM actually received the calls (with correct overrides passed).
    assert fake_bounded.calls == ["hello", "again"]


def test_llm_query_prepends_context_and_preserves_single_arg(monkeypatch) -> None:
    """``llm_query(prompt, context=...)`` prepends context to the prompt reaching
    the sub-LM; single-arg calls are unchanged (back-compat).

    Regression (tr-5671ce47 iter-4): the agent called ``llm_query(query)`` to
    analyze ``context['document_text']`` but the sub-LLM never received the
    document — ``llm_query`` took only a prompt. The ``context=`` parameter
    lets the agent pass workspace content explicitly.
    """
    _patch_mlflow(monkeypatch)

    rec = _RecordingLM()  # no extractable credentials → build_bounded_lm returns None → raw fallback
    host = _make_host(rec)
    try:
        host.llm_query("what is this?", context="DOC")
        host.llm_query("plain")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert rec.calls == ["DOC\n\nwhat is this?", "plain"], (
        f"context must be prepended; single-arg call unchanged. got {rec.calls!r}"
    )


def test_llm_query_batched_prepends_context(monkeypatch) -> None:
    """``llm_query_batched(prompts, context=...)`` prepends context to every prompt."""
    captured = _patch_mlflow(monkeypatch)

    rec = _RecordingLM()
    host = _make_host(rec)
    try:
        results = host.llm_query_batched(["a", "b"], context="CTX")
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert rec.calls == ["CTX\n\na", "CTX\n\nb"], f"got {rec.calls!r}"
    assert results == ["ok:CTX\n\na", "ok:CTX\n\nb"]
    batch_record = next(record for record in captured if record["name"] == "fleet_rlm.llm_query_batched")
    assert batch_record["attributes"]["fleet_rlm.max_llm_calls"] == "5"
    assert batch_record["attributes"]["fleet_rlm.llm_call_count"] == "2"
    assert batch_record["attributes"]["fleet_rlm.llm_calls_remaining"] == "3"


def test_llm_query_batched_short_circuits_existing_auth_failure(monkeypatch) -> None:
    captured = _patch_mlflow(monkeypatch)

    rec = _RecordingLM()
    host = _make_host(rec)
    host._sub_lm_auth_failed = True
    host._sub_lm_auth_error = "RuntimeError provider_status=401: unauthorized"
    try:
        with pytest.raises(RuntimeError, match="previously failed"):
            host.llm_query_batched(["a", "b", "c"])
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert rec.calls == []
    assert host._llm_call_count == 0
    batch_record = next(record for record in captured if record["name"] == "fleet_rlm.llm_query_batched")
    assert batch_record["attributes"]["fleet_rlm.auth_preflight_result"] == "auth_fail_fast"
    assert batch_record["outputs"]["skipped_calls_due_to_auth_fail_fast"] == 3


def test_llm_query_batched_auth_failure_stops_launching_remaining_prompts(monkeypatch) -> None:
    captured = _patch_mlflow(monkeypatch)

    class FirstPromptUnauthorizedLM:
        model = "bad-profile-model"

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.lock = threading.Lock()

        def __call__(self, prompt: str, **kwargs: Any) -> list[dict[str, str]]:
            _ = kwargs
            with self.lock:
                self.calls.append(prompt)
            if prompt == "auth":
                raise RuntimeError("HTTP Error 401: Unauthorized token=sk-batch-secret")
            time.sleep(0.05)
            return [{"text": f"ok:{prompt}"}]

    lm = FirstPromptUnauthorizedLM()
    host = _make_host(lm)
    host.max_llm_calls = 10
    prompts = ["auth", "b", "c", "d", "e", "f"]
    try:
        with pytest.raises(RuntimeError, match="llm_query_batched failed"):
            host.llm_query_batched(prompts)
    finally:
        if host._sub_lm_executor is not None:
            host._sub_lm_executor.shutdown(wait=False)

    assert host._sub_lm_auth_failed is True
    assert len(lm.calls) < len(prompts)
    assert "e" not in lm.calls or "f" not in lm.calls
    batch_record = next(record for record in captured if record["name"] == "fleet_rlm.llm_query_batched")
    batch_outputs = batch_record["outputs"]
    assert batch_outputs["auth_failure"] is True
    assert batch_outputs["skipped_calls_due_to_auth_fail_fast"] >= 1
    assert batch_outputs["submitted_count"] < len(prompts)
    assert host._llm_call_count == batch_outputs["submitted_count"]


def test_invoke_tool_forwards_context_for_llm_query(monkeypatch) -> None:
    """``bridge_callbacks.invoke_tool`` forwards ``context`` for ``llm_query``."""
    from fleet_rlm.integrations.daytona.bridge import invoke_tool

    seen: list[tuple[str, str]] = []

    class FakeInterpreter:
        def llm_query(self, prompt: str, context: str = "") -> str:
            seen.append((prompt, context))
            return f"r:{prompt}"

        def llm_query_batched(self, prompts: list[str], context: str = "") -> list[str]:
            seen.append((",".join(prompts), context))
            return [f"r:{p}" for p in prompts]

        _tools: dict[str, Any] = {}

    interp = FakeInterpreter()
    invoke_tool(interp, "llm_query", ["q", "DOC"], {})
    assert seen == [("q", "DOC")]
    invoke_tool(interp, "llm_query", ["q"], {"context": "KW"})
    assert seen[-1] == ("q", "KW")
    invoke_tool(interp, "llm_query_batched", [["a", "b"], "C"], {})
    assert seen[-1] == ("a,b", "C")
