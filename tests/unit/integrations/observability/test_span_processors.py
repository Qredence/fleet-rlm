from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.integrations.observability.span_processors import (
    build_span_processors,
    fleet_metadata_processor,
)


def test_fleet_metadata_processor_sets_attributes():
    attrs_set = {}
    span = SimpleNamespace(
        set_attributes=lambda d: attrs_set.update(d),
    )

    processor = fleet_metadata_processor(
        app_env="production",
        workspace_id="ws-123",
        version="1.2.3",
    )
    processor(span)

    assert attrs_set["fleet_rlm.app_env"] == "production"
    assert attrs_set["fleet_rlm.workspace_id"] == "ws-123"
    assert attrs_set["fleet_rlm.version"] == "1.2.3"


def test_fleet_metadata_processor_uses_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("WS_DEFAULT_WORKSPACE_ID", "ws-default")

    attrs_set = {}
    span = SimpleNamespace(set_attributes=lambda d: attrs_set.update(d))

    processor = fleet_metadata_processor()
    processor(span)

    assert attrs_set["fleet_rlm.app_env"] == "staging"
    assert attrs_set["fleet_rlm.workspace_id"] == "ws-default"


def test_fleet_metadata_processor_handles_span_errors():
    def raise_on_set(d):
        raise RuntimeError("span dead")

    span = SimpleNamespace(set_attributes=raise_on_set)
    processor = fleet_metadata_processor(app_env="test")
    # Should not raise
    processor(span)


def test_build_span_processors_returns_list():
    processors = build_span_processors(app_env="local")
    assert isinstance(processors, list)
    assert len(processors) == 1
    assert callable(processors[0])
