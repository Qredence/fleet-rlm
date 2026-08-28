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

def test_config_inventory_remains_derived_from_the_p29_schema() -> None:
    """No second full field mirror may reappear beside the FleetFieldPolicy declarations."""
    from fleet_rlm.config.settings import Settings, config_field_specs

    specs = config_field_specs()
    spec_fields = {spec.settings_field for spec in specs if spec.settings_field is not None}
    model_fields = set(Settings.model_fields)
    assert spec_fields <= model_fields
    assert model_fields - spec_fields == {
        "database_url",
        "daytona_api_key",
        "llm_api_key",
        "posthog_project_token",
    }
    assert Settings.model_config.get("extra") == "forbid"


DELETED_SYMBOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fleet_rlm.chat.preparation", ("RunPreparationValidationError", "RunPreparationIntegrityError")),
    ("fleet_rlm.chat.run_ownership", ("consume_task_exception",)),
    ("fleet_rlm.rlm._dspy_compat", ("initial_tools_registered", "mark_tools_registered")),
    ("fleet_rlm.rlm.result", ("sanitize_public_error",)),
    (
        "fleet_rlm.daytona.broker",
        ("default_bridge_dispatcher", "set_bridge_service_loop", "bridge_service_loop"),
    ),
    ("fleet_rlm.daytona.sandbox_lease", ("acquire_owned_lease", "OwnedAcquisition")),
    ("fleet_rlm.daytona.broker", ("_FINAL_OUTPUT_MARKER",)),
    ("fleet_rlm.workspace.storage", ("HostWorkspaceVolumeGateway",)),
    (
        "fleet_rlm.workspace.models",
        ("reformat_workspace_memory_record", "build_workspace_memory_digest", "validate_workspace_memory_content"),
    ),
    ("fleet_rlm.attachments.tools", ("_bounded_text",)),
    ("fleet_rlm.sessions.catalog", ("SequenceCursor.from_query",)),
)


@pytest.mark.parametrize(("module_name", "symbols"), DELETED_SYMBOLS, ids=lambda pair: pair[0].rsplit(".", 1)[-1])
def test_p33_deleted_module_symbols_stay_deleted(module_name: str, symbols: tuple[str, ...]) -> None:
    """Accidental resurrection of a superseded path fails deterministically."""
    import importlib.util

    if importlib.util.find_spec(module_name) is None:
        pytest.skip(f"{module_name} is absent")
    module = __import__(module_name, fromlist=["*"])
    for name in symbols:
        assert not hasattr(module, name), f"{module_name}.{name} was resurrected"


def test_settings_keeps_no_legacy_llm_fields() -> None:
    """The zero-consumer legacy Settings fields deleted in P33 stay deleted."""
    from fleet_rlm.config.settings import Settings

    assert "llm_base_url" not in Settings.model_fields
    assert "llm_max_tokens" not in Settings.model_fields


def test_deleted_dataclass_methods_and_fields_stay_deleted() -> None:
    """Dead compat surfaces removed in P33 do not reappear on the domain models."""
    from fleet_rlm.chat import post_commit_memory
    from fleet_rlm.chat.session_context import SessionContextManifest, TurnPreview
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.config.settings import Settings
    from fleet_rlm.daytona.session_manager import InterpreterLease
    from fleet_rlm.sessions.models import AssistantTurnRecord
    from fleet_rlm.workspace import storage as workspace_fs

    assert not hasattr(Settings, "llm_api_url")
    assert "delete_sandbox" not in InterpreterLease.__dataclass_fields__
    assert "__call__" not in vars(post_commit_memory.OwnedPostCommitMemoryPromotion)
    assert not hasattr(TurnPreview, "to_input")
    assert not hasattr(SessionContextManifest, "to_input")
    assert not hasattr(TurnRuntime, "_submit_claim_loss_cleanup")
    assert not hasattr(workspace_fs.DaytonaSessionWorkspaceFS, "read_text")
    assert not hasattr(workspace_fs.AsyncDaytonaSessionWorkspaceFS, "read_text")
    assert not hasattr(AssistantTurnRecord, "content")


def test_workspace_capability_metadata_has_no_dead_serializer() -> None:
    from fleet_rlm.workspace.models import WorkspaceCapabilityMetadata

    assert not hasattr(WorkspaceCapabilityMetadata, "to_input")
