"""Unit contracts for fail-soft Turn-rooted MLflow spans."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm.observability.turn_tracing import annotate_trace_io, current_turn_trace_id, turn_trace


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch, *, explode: bool = False) -> SimpleNamespace:
    calls = SimpleNamespace(
        start_span_names=[],
        update_kwargs=[],
        get_trace_calls=0,
    )

    @contextmanager
    def start_span(*, name: str = "span", span_type: Any = None, **_kwargs: Any) -> Iterator[Any]:
        del span_type
        if explode:
            raise RuntimeError("span boom")
        calls.start_span_names.append(name)
        yield SimpleNamespace(request_id="tr-from-span")

    def update_current_trace(**kwargs: Any) -> None:
        calls.update_kwargs.append(kwargs)

    def get_last_active_trace_id(**_kwargs: Any) -> str:
        calls.get_trace_calls += 1
        return "tr-active-123"

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.update_current_trace = update_current_trace  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = get_last_active_trace_id  # type: ignore[attr-defined]

    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


def test_turn_trace_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("mlflow must not be used when disabled")

    mlflow = ModuleType("mlflow")
    mlflow.start_span = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)

    session_id = uuid4()
    run_id = uuid4()
    with turn_trace(session_id, run_id, enabled=False) as handle:
        assert handle.trace_id is None
        assert current_turn_trace_id() is None


def test_turn_trace_enabled_sets_tags_and_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    session_id = uuid4()
    run_id = uuid4()
    with turn_trace(session_id, run_id, enabled=True) as handle:
        assert handle.trace_id == "tr-active-123"
        assert current_turn_trace_id() == "tr-active-123"
        assert calls.start_span_names == ["fleet_turn"]
        assert calls.update_kwargs == [
            {
                "session_id": str(session_id),
                "user": "fleet-local",
                "tags": {
                    "fleet.run_id": str(run_id),
                    "fleet.session_id": str(session_id),
                },
            }
        ]
    assert current_turn_trace_id() is None


def test_turn_trace_span_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch, explode=True)
    with turn_trace(uuid4(), uuid4(), enabled=True) as handle:
        assert handle.trace_id is None


def test_turn_trace_respects_expose_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch)
    with turn_trace(uuid4(), uuid4(), enabled=True, expose_trace_id=False) as handle:
        assert handle.trace_id is None
        assert current_turn_trace_id() is None


def test_annotate_trace_io_updates_trace_request_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    annotate_trace_io(
        request="how are you?",
        response_text="display answer",
        response_outputs={
            "answer": "public answer",
            "final_reasoning": "public reasoning",
            "internal_payload": {"secret": "value"},
        },
    )

    assert calls.update_kwargs[-1] == {
        "request": {"request": "how are you?"},
        "response": {"answer": "public answer", "final_reasoning": "public reasoning"},
    }


def test_annotate_trace_io_falls_back_to_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    annotate_trace_io(request="hello")

    assert calls.update_kwargs[-1] == {
        "request": {"request": "hello"},
        "response": {"answer": ""},
    }
