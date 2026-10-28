"""Approval identity follows behavior owners, resources, and file membership."""

from __future__ import annotations

import pytest

from fleet_rlm.optimize import artifacts


@pytest.mark.parametrize(
    "relative",
    [
        "engine/programs.py",
        "engine/children.py",
        "engine/signatures.py",
        "optimize/objectives.py",
        "optimize/validation.py",
        "optimize/new_component.py",
        "sandbox/runtime/repl.py",
        "sandbox/snapshots.py",
        "sandbox/new_bridge.py",
        "sandbox/snapshot-requirements.txt",
        "bundled_skills/example/SKILL.md",
        "bundled_skills/example/references/policy.txt",
        "adapters.py",
        "config.py",
        "contracts.py",
        "observability.py",
        "execution_evidence.py",
        "replay.py",
        "workspace.py",
        "skills.py",
        "session.py",
        "store.py",
        "optimization_runs.py",
    ],
)
def test_source_identity_covers_mutation_addition_and_deletion(tmp_path, monkeypatch, relative):
    package = tmp_path / "fleet_rlm"
    owner = package / "optimize/artifacts.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# identity owner\n")
    monkeypatch.setattr(artifacts, "__file__", str(owner))
    initial = artifacts.optimization_code_digest()
    path = package / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"first behavior")
    added = artifacts.optimization_code_digest()
    assert added != initial
    path.write_bytes(b"second behavior")
    assert artifacts.optimization_code_digest() not in {initial, added}
    path.unlink()
    assert artifacts.optimization_code_digest() == initial


def test_presentation_is_excluded_from_execution_identity(tmp_path, monkeypatch):
    package = tmp_path / "fleet_rlm"
    owner = package / "optimize/artifacts.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# identity owner\n")
    monkeypatch.setattr(artifacts, "__file__", str(owner))
    before = artifacts.optimization_code_digest()
    for name in ("api/schemas.py", "tui/app.py", "setup.py", "README.md"):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("presentation-only fixture")
    assert artifacts.optimization_code_digest() == before
