"""Facade import and delegation coverage for the Daytona substrate package."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock


def test_daytona_facade_modules_import_without_live_credentials() -> None:
    for module_name in (
        "fleet_rlm.daytona",
        "fleet_rlm.daytona.interpreter",
        "fleet_rlm.daytona.sandbox",
        "fleet_rlm.daytona.volume",
        "fleet_rlm.daytona.files",
        "fleet_rlm.daytona.workspace",
        "fleet_rlm.daytona.session_state",
        "fleet_rlm.daytona.diagnostics",
    ):
        importlib.import_module(module_name)


def test_daytona_interpreter_facade_matches_existing_implementation() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaInterpreter
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter as LegacyDaytonaInterpreter

    assert DaytonaInterpreter is LegacyDaytonaInterpreter


def test_daytona_interpreter_facade_does_not_retain_a_temporary_legacy_patch(monkeypatch) -> None:
    from fleet_rlm.daytona import interpreter as facade
    from fleet_rlm.integrations.daytona import interpreter as implementation

    original = implementation.DaytonaInterpreter
    monkeypatch.setattr(implementation, "DaytonaInterpreter", MagicMock(name="DaytonaInterpreter"))
    importlib.reload(facade)
    monkeypatch.undo()

    assert facade.DaytonaInterpreter is original


def test_existing_integration_root_imports_resolve_through_facades() -> None:
    from fleet_rlm.daytona.diagnostics import ResolvedDaytonaConfig
    from fleet_rlm.daytona.interpreter import DaytonaInterpreter
    from fleet_rlm.daytona.sandbox import DaytonaSandboxRuntime
    from fleet_rlm.daytona.volume import init_memory_db
    from fleet_rlm.integrations.daytona import (
        DaytonaInterpreter as LegacyDaytonaInterpreter,
    )
    from fleet_rlm.integrations.daytona import (
        DaytonaSandboxRuntime as LegacyDaytonaSandboxRuntime,
    )
    from fleet_rlm.integrations.daytona import (
        ResolvedDaytonaConfig as LegacyResolvedDaytonaConfig,
    )
    from fleet_rlm.integrations.daytona import init_memory_db as legacy_init_memory_db

    assert LegacyDaytonaInterpreter is DaytonaInterpreter
    assert LegacyDaytonaSandboxRuntime is DaytonaSandboxRuntime
    assert LegacyResolvedDaytonaConfig is ResolvedDaytonaConfig
    assert legacy_init_memory_db is init_memory_db


def test_sandbox_facade_delegates_to_existing_lifecycle_helpers() -> None:
    from fleet_rlm.daytona import sandbox as facade
    from fleet_rlm.integrations.daytona import runtime as implementation

    assert facade.DaytonaSandboxRuntime is implementation.DaytonaSandboxRuntime
    assert facade.DaytonaSandboxSession is implementation.DaytonaSandboxSession
    assert facade.get_sandbox is implementation.get_sandbox
    assert facade.resume_workspace_session is implementation.resume_workspace_session
    assert facade.fork_sandbox is implementation.fork_sandbox
    assert facade.get_sandbox_id_from_interpreter is implementation.get_sandbox_id_from_interpreter
    assert facade.resolve_snapshot_for_skills is implementation.resolve_snapshot_for_skills


def test_volume_facade_exposes_current_durable_volume_helpers() -> None:
    from fleet_rlm.daytona import volume as facade
    from fleet_rlm.integrations.daytona import volumes as implementation

    assert facade.ensure_daytona_volume_layout is implementation.ensure_daytona_volume_layout
    assert facade.aensure_daytona_volume_layout is implementation.aensure_daytona_volume_layout
    assert facade.seed_system_skills is implementation.seed_system_skills
    assert facade.seed_remote_system_skills is implementation.seed_remote_system_skills
    assert facade.init_memory_db is implementation.init_memory_db
    assert facade.list_daytona_volume_tree is implementation.list_daytona_volume_tree
    assert facade.read_daytona_volume_file_text is implementation.read_daytona_volume_file_text


def test_files_facade_preserves_safe_file_operation_helpers() -> None:
    from fleet_rlm.daytona import files as facade
    from fleet_rlm.integrations.daytona import sandbox_executor as implementation

    assert facade.SandboxExecutor is implementation.SandboxExecutor
    assert facade.sanitize_execution_code is implementation.sanitize_execution_code
    assert facade.prepare_execution_code is implementation.prepare_execution_code
    assert facade.execute_in_session is implementation.execute_in_session
    assert facade.execute_direct is implementation.execute_direct
    assert facade.python_parses("workspace_write('notes.txt', 'ok')\n")


def test_workspace_facade_exposes_repo_and_context_helpers() -> None:
    from fleet_rlm.daytona import workspace as facade
    from fleet_rlm.integrations.daytona import _repo, isolation, workspace_manager

    assert facade.WorkspaceManager is workspace_manager.WorkspaceManager
    assert facade.create_workspace_session is workspace_manager.create_workspace_session
    assert facade.acreate_workspace_session is workspace_manager.acreate_workspace_session
    assert facade.reconcile_workspace_session is workspace_manager.reconcile_workspace_session
    assert facade.amount_local_repo_tree is _repo.amount_local_repo_tree
    assert facade._aclone_repo is _repo._aclone_repo
    assert facade.stage_context_paths is isolation.stage_context_paths


def test_session_state_facade_preserves_session_manifest_surface() -> None:
    from fleet_rlm.daytona import session_state as facade
    from fleet_rlm.integrations.daytona import session_runtime, workspace_manager

    assert facade.DaytonaSandboxSession is session_runtime.DaytonaSandboxSession
    assert facade.WorkspaceManager is workspace_manager.WorkspaceManager
    assert facade._run_admin_code is session_runtime._run_admin_code


def test_diagnostics_facade_exposes_provider_readiness_helpers() -> None:
    from fleet_rlm.daytona import diagnostics as facade
    from fleet_rlm.integrations.daytona import config
    from fleet_rlm.integrations.daytona import diagnostics as implementation

    assert facade.resolve_daytona_config is config.resolve_daytona_config
    assert facade.resolve_daytona_lm_runtime_config is config.resolve_daytona_lm_runtime_config
    assert facade.classify_daytona_sdk_error is config.classify_daytona_sdk_error
    assert facade.run_daytona_smoke is implementation.run_daytona_smoke
    assert facade.category_for_phase("config") == "config_error"
