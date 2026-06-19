from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any


def test_llm_query_records_provider_wait_span(monkeypatch) -> None:
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
            self.sub_lm = lambda prompt: [{"text": f"answer: {prompt}"}]
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
    assert captured[0]["inputs"]["prompt_chars"] == len("summarize this")
    assert captured[0]["outputs"]["status"] == "ok"
    assert captured[0]["outputs"]["response_chars"] == len("answer: summarize this")
