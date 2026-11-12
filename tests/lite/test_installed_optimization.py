"""Exercise approval evidence and program envelopes outside a source checkout."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import dspy

from fleet_rlm.config import FleetSettings
from fleet_rlm.engine.compiled import program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.optimize import artifacts
from fleet_rlm.optimize.scorers import SCORER_VERSION
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, LOCK_PATH

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_approval_evidence_builds_without_source_lockfile(tmp_path: Path, monkeypatch) -> None:
    """Approval evidence must not require uv.lock in the current working directory."""
    monkeypatch.chdir(tmp_path)
    assert not (Path.cwd() / "uv.lock").exists()

    evidence = artifacts.approval_evidence(FleetSettings(_env_file=None), "incumbent", scorer_version=SCORER_VERSION)
    assert evidence["evidence_format"] == "3"
    assert len(evidence["dependency_fingerprint"]) == 64
    assert "lockfile_digest" not in evidence
    assert LOCK_PATH.is_file()
    assert DEFAULT_DEFINITION.digest() == "3d0cd582c972090c18437dfba13358e055970af6de9407e46515e75bd49fa0e8"

    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    engine = FleetRuntime(
        DaytonaSandboxManager("key", "org", "volume", "wheel-state"),
        lm,
        lm,
    )
    engine.program.routing.predict.signature = engine.program.routing.predict.signature.with_instructions("TUNED ROUTE")
    current = tmp_path / "fleet-program.json"
    current.write_bytes(program_envelope(engine.program))

    restored = FleetRuntime(
        DaytonaSandboxManager("key", "org", "volume", "wheel-fresh"), lm, lm, compiled_path=str(current)
    )
    assert restored.program.dump_state() == engine.program.dump_state()
    assert restored.program.routing.predict.signature.instructions == "TUNED ROUTE"


def test_dependency_fingerprint_changes_when_installed_version_changes(monkeypatch) -> None:
    """Approval identity includes distribution versions, not only package names."""
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.3")],
    )
    original = artifacts.installed_dependency_fingerprint()
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.4")],
    )
    assert artifacts.installed_dependency_fingerprint() != original
