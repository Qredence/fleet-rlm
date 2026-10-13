"""Isolate Fleet's process-global MLflow tracing state for one test."""

from __future__ import annotations

import pytest

from fleet_rlm.observability import tracing


def patch_tracing_state(monkeypatch: pytest.MonkeyPatch, *, active: bool) -> None:
    """Set the Turn-span gate over a clean tracing state without a tracking backend.

    Every module global is patched through ``monkeypatch`` so the prior state is
    restored at teardown. The real MLflow module is never imported or mutated,
    and a fake configuration from an earlier test cannot make this one look
    like a live process lifespan.
    """
    monkeypatch.setattr(tracing, "_TRACING_ACTIVE", active)
    monkeypatch.setattr(tracing, "_DSPY_AUTOLOG_ENABLED", False)
    monkeypatch.setattr(tracing, "_TRACE_CONFIG_CONTEXT", None)
    monkeypatch.setattr(tracing, "_TRACKING_URI_BEFORE", tracing._UNKNOWN_TRACKING_URI)
    monkeypatch.setattr(tracing, "_TRACKING_URI_APPLIED", None)
    monkeypatch.setattr(tracing, "_TRACE_EXPERIMENT_ID", None)
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", False)
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_MAX_CHARS", 10_000)
    monkeypatch.setattr(tracing, "_TRACE_ENVIRONMENT_SNAPSHOT", {})
    monkeypatch.setattr(tracing, "_TRACE_ENVIRONMENT_APPLIED", {})
