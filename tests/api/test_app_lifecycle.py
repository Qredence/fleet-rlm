"""Lifespan acquisition failures must release resources in dependency order."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.config import FleetSettings


@pytest.mark.parametrize("failure", [None, "database", "tracing", "registry", "experiments"])
def test_lifespan_unwinds_partial_startup_and_waits_for_experiments_before_storage(monkeypatch, failure):
    app_module = importlib.import_module("fleet_rlm.api.app")
    events = []

    class Store:
        @classmethod
        def open(cls, *_args, **_kwargs):
            events.append("store.acquire")
            return cls()

        def initialize(self):
            events.append("store.initialize")
            if failure == "database":
                raise RuntimeError("startup failed")

        def close_migration_pool(self):
            events.append("store.ddl.close")

        def close(self):
            events.append("store.close")

    class Registry:
        def __init__(self, *_args):
            events.append("registry.acquire")
            if failure == "registry":
                raise RuntimeError("startup failed")

        def close(self):
            events.append("registry.close")

    class Executor:
        def shutdown(self, **kwargs):
            assert kwargs == {"wait": False, "cancel_futures": True}
            events.append("executor.close")

    class Experiments:
        def __init__(self, *_args):
            events.append("experiments.acquire")

        async def initialize(self):
            events.append("experiments.initialize")
            if failure == "experiments":
                raise RuntimeError("startup failed")

        async def close(self):
            events.append("experiments.close")

    def tracing(_settings):
        events.append("tracing.initialize")
        if failure == "tracing":
            raise RuntimeError("startup failed")
        return "experiment-id"

    monkeypatch.setattr(app_module, "Store", Store)
    monkeypatch.setattr(app_module, "SessionRuntimeRegistry", Registry)
    monkeypatch.setattr(app_module.turns, "create_turn_executor", Executor)
    monkeypatch.setattr(app_module, "ExperimentManager", Experiments)
    monkeypatch.setattr(app_module, "_configure_tracing", tracing)
    monkeypatch.setattr(app_module.mlflow, "flush_trace_async_logging", lambda: events.append("tracing.flush"))
    app = app_module.create_app(settings=FleetSettings(_env_file=None))
    if failure:
        with pytest.raises(RuntimeError, match="startup failed"), TestClient(app):
            pass
    else:
        with TestClient(app):
            assert "experiments.close" not in events
    assert events.index("store.ddl.close") < events.index("store.close")
    if "experiments.acquire" in events:
        assert events.index("experiments.close") < events.index("executor.close")
    if "registry.close" in events:
        assert events.index("executor.close") < events.index("registry.close") < events.index("store.close")
    if failure not in {"database", "tracing"}:
        assert events[-1] == "tracing.flush"
