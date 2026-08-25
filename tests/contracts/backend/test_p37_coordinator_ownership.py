"""P37 ownership contract: orchestration is coordinator-owned."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_ROOT = REPO_ROOT / "src" / "fleet_rlm" / "chat"


def test_p37_deletes_process_local_orchestration_modules() -> None:
    assert not (CHAT_ROOT / "preparation_attempt.py").exists()
    assert not (CHAT_ROOT / "run_execution.py").exists()
    assert not (CHAT_ROOT / "run_runtime_owner.py").exists()


def test_p37_production_sources_have_one_coordinator_owner() -> None:
    source_root = REPO_ROOT / "src" / "fleet_rlm"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    for forbidden in (
        "PreparationAttempt",
        "RunExecutionDriver",
        "RunOwnership",
        "RunLifetimeReceipt",
        "OwnershipComponentReceipt",
        "PreparedResourcesReceipt",
        "cleanup_receipt",
    ):
        assert forbidden not in source


def test_p37_route_uses_the_coordinator_owned_open_handle() -> None:
    route_source = (REPO_ROOT / "src" / "fleet_rlm" / "api" / "routes" / "turns.py").read_text(encoding="utf-8")
    assert "coordinator.open(" not in route_source
    assert "fleet-turn-open-compat" not in route_source
