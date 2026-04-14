"""Package export guards for fleet_rlm root module."""

from __future__ import annotations

import importlib
from importlib.metadata import version

import pytest

import fleet_rlm
import fleet_rlm.agent_host.terminal_flow as agent_host_terminal_flow
from fleet_rlm.integrations import database


def test_root_all_exports_are_resolvable() -> None:
    for name in fleet_rlm.__all__:
        assert hasattr(fleet_rlm, name)
        assert getattr(fleet_rlm, name) is not None


def test_root_all_matches_declared_lazy_exports() -> None:
    expected_exports = {"__version__"} | set(fleet_rlm._LAZY_ATTRS)
    assert set(fleet_rlm.__all__) == expected_exports


def test_root_package_version_matches_installed_metadata() -> None:
    assert fleet_rlm.__version__ == version("fleet-rlm")


def test_database_package_exports_are_resolvable() -> None:
    for name in database.__all__:
        assert hasattr(database, name)
        assert getattr(database, name) is not None


def test_database_models_facade_module_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("fleet_rlm.integrations.database.models")


def test_legacy_orchestration_terminal_flow_import_resolves() -> None:
    module = importlib.import_module("fleet_rlm.orchestration_app.terminal_flow")

    assert module.apply_terminal_event_policy is (
        agent_host_terminal_flow.apply_terminal_event_policy
    )
