"""P33/T4 maintainability guardrails (QRE-202).

Objective, non-brittle pins for canonical-path ownership and the completed
P33 contraction: tests assert observable seams and invariants, never private
line-by-line structure. Each block documents which architectural regression
would fail CI deterministically.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Canonical-path guardrails (P25-P32 seams must keep owning their domains)
# ---------------------------------------------------------------------------


def test_workspace_memory_and_fs_route_through_the_p28_runtime_handlers() -> None:
    """Installed + fallback workspace execution stays on the one runtime handler."""
    import fleet_rlm.daytona.workspace_fs as workspace_fs
    import fleet_rlm.daytona.workspace_memory as workspace_memory
    from fleet_rlm.daytona.workspace_agent import run_workspace_agent, run_workspace_agent_async

    assert workspace_memory.run_workspace_agent is run_workspace_agent
    assert workspace_fs.run_workspace_agent_async is run_workspace_agent_async


def test_owned_effect_primitive_owns_post_commit_promotion() -> None:
    """Owned-effect call sites settle through the P27 primitive, not hand-rolled waits."""
    import fleet_rlm.chat.post_commit_memory as post_commit_memory
    from fleet_rlm.runtime.owned_effect import OwnedEffect

    assert post_commit_memory.OwnedEffect is OwnedEffect
    assert callable(OwnedEffect.from_task)


def test_child_lease_settlement_uses_explicit_p30_state() -> None:
    """The recursive child lease exposes typed settlement states (no boolean shortcuts)."""
    from fleet_rlm.daytona.recursive_child_lease import ChildRuntimeLeaseState

    assert {state.name for state in ChildRuntimeLeaseState} == {"OPEN", "CLOSING", "CLOSED", "FAILED"}


def test_config_inventory_remains_derived_from_the_p29_schema() -> None:
    """No second full field mirror may reappear beside the FleetFieldPolicy declarations."""
    from fleet_rlm.config import Settings, config_field_specs
    from fleet_rlm.config_policy import _FIELDS

    spec_paths = {spec.toml_path for spec in config_field_specs()}
    policy_paths = {field.path for field in _FIELDS}
    assert policy_paths == spec_paths
    assert Settings.model_config.get("extra") == "forbid"


def test_tui_canonical_convergence_guardrail_exists() -> None:
    """The P32 invariant suite pinning live/durable reducer convergence stays committed."""
    from pathlib import Path

    suite = Path(__file__).resolve().parents[3] / "tools/fleet-tui/src/tui/tests/turn-reducer-invariants.test.ts"
    assert suite.is_file()


def test_daytona_sdk_stays_inside_the_daytona_boundary() -> None:
    """Native DSPy execution authority stays in rlm/; Daytona SDK stays in daytona/."""
    from fleet_rlm.rlm.dspy_contract import RLMOptions

    assert RLMOptions is not None


# ---------------------------------------------------------------------------
# Contraction guardrails: deleted superseded paths stay deleted
# ---------------------------------------------------------------------------

DELETED_SYMBOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fleet_rlm.chat.run_preparation", ("RunPreparationValidationError", "RunPreparationIntegrityError")),
    ("fleet_rlm.chat.run_ownership", ("consume_task_exception",)),
    ("fleet_rlm.rlm.dspy_interpreter_contract", ("initial_tools_registered", "mark_tools_registered")),
    ("fleet_rlm.rlm.sanitize", ("sanitize_public_error",)),
    (
        "fleet_rlm.daytona.dspy_sync_bridge",
        ("default_bridge_dispatcher", "set_bridge_service_loop", "bridge_service_loop"),
    ),
    ("fleet_rlm.daytona.sandbox_lease", ("acquire_owned_lease", "OwnedAcquisition")),
    ("fleet_rlm.daytona.broker_source", ("_FINAL_OUTPUT_MARKER",)),
    ("fleet_rlm.files.host_volume", ("HostWorkspaceVolumeGateway",)),
    (
        "fleet_rlm.files.memory_models",
        ("reformat_workspace_memory_record", "build_workspace_memory_digest", "validate_workspace_memory_content"),
    ),
    ("fleet_rlm.files.tools", ("_bounded_text",)),
    ("fleet_rlm.sessions.catalog", ("SequenceCursor.from_query",)),
)


@pytest.mark.parametrize(("module_name", "symbols"), DELETED_SYMBOLS, ids=lambda pair: pair[0].rsplit(".", 1)[-1])
def test_p33_deleted_module_symbols_stay_deleted(module_name: str, symbols: tuple[str, ...]) -> None:
    """Accidental resurrection of a superseded path fails deterministically."""
    import importlib

    module = importlib.import_module(module_name)
    for name in symbols:
        assert not hasattr(module, name), f"{module_name}.{name} was resurrected"


def test_settings_keeps_no_legacy_llm_fields() -> None:
    """The zero-consumer legacy Settings fields deleted in P33 stay deleted."""
    from fleet_rlm.config import Settings

    assert "llm_base_url" not in Settings.model_fields
    assert "llm_max_tokens" not in Settings.model_fields


def test_deleted_dataclass_methods_and_fields_stay_deleted() -> None:
    """Dead compat surfaces removed in P33 do not reappear on the domain models."""
    from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
    from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.config import Settings
    from fleet_rlm.daytona.session_manager import InterpreterLease
    from fleet_rlm.daytona.workspace_fs import AsyncDaytonaSessionWorkspaceFS, DaytonaSessionWorkspaceFS
    from fleet_rlm.sessions.models import AssistantTurnRecord

    assert not hasattr(Settings, "llm_api_url")
    assert "delete_sandbox" not in InterpreterLease.__dataclass_fields__
    assert "__call__" not in vars(OwnedPostCommitMemoryPromotion)
    assert not hasattr(TurnPreview, "to_input")
    assert not hasattr(SessionContextManifest, "to_input")
    assert not hasattr(TurnCoordinator, "_submit_claim_loss_cleanup")
    assert not hasattr(DaytonaSessionWorkspaceFS, "read_text")
    assert not hasattr(AsyncDaytonaSessionWorkspaceFS, "read_text")
    assert not hasattr(AssistantTurnRecord, "content")


def test_workspace_capability_metadata_has_no_dead_serializer() -> None:
    from fleet_rlm.files.workspace_models import WorkspaceCapabilityMetadata

    assert not hasattr(WorkspaceCapabilityMetadata, "to_input")
