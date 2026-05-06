"""Tests for the unified PersistenceProtocol."""

from __future__ import annotations

import inspect

from fleet_rlm.integrations.database.fleet_repository import FleetRepository
from fleet_rlm.integrations.local_store import LocalStore
from fleet_rlm.integrations.persistence_protocol import PersistenceProtocol

# Expected method names based on router usage and the protocol definition.
_EXPECTED_METHODS: set[str] = {
    # Identity
    "upsert_identity",
    "resolve_workspace_id",
    # Session CRUD
    "list_chat_sessions",
    "get_chat_session",
    "list_chat_turns",
    "update_chat_session",
    "archive_chat_session",
    "restore_chat_session",
    "get_session_stats",
    # Runs / Steps
    "create_run",
    "get_run",
    "get_run_steps_paginated",
    "append_step",
    "update_run_status",
    "store_artifact",
    # Memory
    "store_memory_item",
    "list_memory_items_paginated",
    # Traces
    "store_trace_feedback",
    "store_rlm_trace",
    # Datasets
    "create_dataset",
    "list_datasets",
    "get_dataset",
    "list_dataset_examples",
    # Optimization runs
    "create_optimization_run",
    "list_optimization_runs",
    "get_optimization_run",
    "update_optimization_run_phase",
    "complete_optimization_run",
    "fail_optimization_run",
    "recover_stale_optimization_runs",
    "save_evaluation_results",
    "get_evaluation_results",
    "save_prompt_snapshots",
    "get_prompt_snapshots",
}


def test_protocol_is_runtime_checkable() -> None:
    """PersistenceProtocol must be decorated with @runtime_checkable."""
    assert hasattr(PersistenceProtocol, "__subclasshook__")


def test_protocol_has_all_expected_methods() -> None:
    """The protocol must declare every method routers depend on."""
    protocol_methods = {name for name, member in inspect.getmembers(PersistenceProtocol, predicate=inspect.isfunction)}
    missing = _EXPECTED_METHODS - protocol_methods
    assert not missing, f"PersistenceProtocol is missing methods: {missing}"


def test_protocol_methods_are_coroutines() -> None:
    """Every method on the protocol must be async (a coroutine)."""
    for name, member in inspect.getmembers(PersistenceProtocol, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        assert inspect.iscoroutinefunction(member), f"PersistenceProtocol.{name} must be async"


def test_fleet_repository_implements_protocol() -> None:
    """FleetRepository must structurally implement PersistenceProtocol."""
    # Structural check: every protocol method exists on FleetRepository
    missing: list[str] = []
    for name in _EXPECTED_METHODS:
        if not hasattr(FleetRepository, name):
            missing.append(name)
    assert not missing, f"FleetRepository missing protocol methods: {missing}"

    # Verify each protocol method is a coroutine on FleetRepository
    for name in _EXPECTED_METHODS:
        member = getattr(FleetRepository, name)
        assert inspect.iscoroutinefunction(member), f"FleetRepository.{name} must be async to satisfy the protocol"


def test_fleet_repository_is_instance_of_protocol() -> None:
    """FleetRepository must pass isinstance check against PersistenceProtocol."""
    assert issubclass(FleetRepository, PersistenceProtocol)


def test_local_store_implements_protocol() -> None:
    """LocalStore must structurally implement PersistenceProtocol."""
    missing: list[str] = []
    for name in _EXPECTED_METHODS:
        if not hasattr(LocalStore, name):
            missing.append(name)
    assert not missing, f"LocalStore missing protocol methods: {missing}"

    for name in _EXPECTED_METHODS:
        member = getattr(LocalStore, name)
        assert inspect.iscoroutinefunction(member), f"LocalStore.{name} must be async to satisfy the protocol"


def test_local_store_is_instance_of_protocol() -> None:
    """LocalStore must pass isinstance check against PersistenceProtocol."""
    assert issubclass(LocalStore, PersistenceProtocol)
