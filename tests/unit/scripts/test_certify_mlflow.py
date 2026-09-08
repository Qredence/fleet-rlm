from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def certification():
    path = Path(__file__).parents[3] / "scripts" / "benchmarks" / "certify_mlflow.py"
    spec = importlib.util.spec_from_file_location("certify_mlflow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_uri_label_does_not_retain_backend_identity(certification) -> None:
    assert certification._safe_uri_label("http://127.0.0.1:5001") == "local_http"
    assert certification._safe_uri_label("https://managed.example") == "https_backend"
    assert certification._safe_uri_label("databricks") == "managed_databricks"


def test_backend_version_accepts_mlflow_plain_text_response(certification, monkeypatch) -> None:
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"3.16.0"

    monkeypatch.setattr(certification.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    assert certification._backend_version("http://127.0.0.1:5001") == "3.16.0"


def test_write_once_refuses_replacement(certification, tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    digest = certification._write_once(path, {"schema": "fleet.mlflow-certification/v1"})
    assert len(digest) == 64
    with pytest.raises(certification.CertificationError, match="already exists"):
        certification._write_once(path, {"schema": "different"})


def test_trace_linkage_checks_tags_trace_ids_and_parent_relationships(certification) -> None:
    session_id = str(uuid4())
    run_id = str(uuid4())
    trace_id = "tr-certification"
    root = SimpleNamespace(name="fleet_turn", span_id="root", parent_id=None, trace_id=trace_id)
    children = [
        SimpleNamespace(name="certification_child", span_id="child-1", parent_id="root", trace_id=trace_id),
        SimpleNamespace(name="certification_child", span_id="child-2", parent_id="root", trace_id=trace_id),
    ]
    trace = SimpleNamespace(
        info=SimpleNamespace(
            trace_id=trace_id,
            tags={
                "fleet.session_id": session_id,
                "fleet.run_id": run_id,
                "fleet.trace_phase": "execution",
            },
        ),
        data=SimpleNamespace(spans=[root, *children]),
    )

    assert all(
        certification._trace_linkage(
            trace,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
        ).values()
    )

    children[1].parent_id = "unrelated-root"
    checks = certification._trace_linkage(
        trace,
        session_id=session_id,
        run_id=run_id,
        trace_id=trace_id,
    )
    assert checks["child_spans_linked"] is False


@pytest.mark.asyncio
async def test_run_trace_fails_closed_without_current_handle_identity(certification, monkeypatch) -> None:
    @contextmanager
    def no_trace(*_args, **_kwargs):
        yield SimpleNamespace(trace_id=None)

    @contextmanager
    def no_phase(*_args, **_kwargs):
        yield None

    class _Proxy:
        def __init__(self, *_args, **_kwargs):
            pass

        async def acall(self, *_args, **_kwargs):
            return "ok"

    monkeypatch.setattr(certification, "turn_trace", no_trace)
    monkeypatch.setattr(certification, "turn_phase_span", no_phase)
    monkeypatch.setattr(certification, "DeadlineLMProxy", _Proxy)
    monkeypatch.setattr(certification, "annotate_trace_io", lambda **_kwargs: None)
    monkeypatch.setattr(certification, "flush_tracing", lambda: None)

    import mlflow

    monkeypatch.setattr(
        mlflow,
        "get_last_active_trace_id",
        lambda: pytest.fail("stale last-active trace must not be used"),
    )
    with pytest.raises(certification.CertificationError, match="trace identity"):
        await certification._run_trace()
