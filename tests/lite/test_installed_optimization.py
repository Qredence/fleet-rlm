"""Exercise approval evidence from an installed wheel, outside the source checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace

from fleet_rlm.optimize import artifacts

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_installed_wheel_can_build_approval_evidence_without_source_lockfile(tmp_path: Path) -> None:
    """The package installation layout must not require uv.lock next to site-packages."""
    build_dir = tmp_path / "wheelhouse"
    target = tmp_path / "installed"
    build_dir.mkdir()
    target.mkdir()
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(build_dir)],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(build_dir.glob("fleet_rlm-*.whl"))
    assert len(wheels) == 1
    subprocess.run(
        ["uv", "pip", "install", "--offline", "--no-deps", "--target", str(target), str(wheels[0])],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    fixture = tmp_path / "native-state.json"
    fixture.write_bytes((REPOSITORY_ROOT / "tests/fixtures/pre_consolidation_program.json").read_bytes())
    code = r"""
import json
import sys
from pathlib import Path

wheel_root = Path(sys.argv[1]).resolve()
# -S avoids executing editable-install .pth files. Add the already-installed host
# dependencies explicitly, with the real wheel target ahead of them.
sys.path[:0] = [str(wheel_root), sys.argv[2]]
from fleet_rlm.config import FleetSettings
from fleet_rlm.optimize import artifacts
from fleet_rlm.optimize.scorers import SCORER_VERSION

module_path = Path(artifacts.__file__).resolve()
assert module_path.is_relative_to(wheel_root), module_path
assert not (Path.cwd() / "uv.lock").exists()
evidence = artifacts.approval_evidence(FleetSettings(_env_file=None), "incumbent", scorer_version=SCORER_VERSION)
assert evidence["evidence_format"] == "2"
assert len(evidence["dependency_fingerprint"]) == 64
assert "lockfile_digest" not in evidence
from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, LOCK_PATH
assert LOCK_PATH.is_file() and LOCK_PATH.is_relative_to(wheel_root)
assert DEFAULT_DEFINITION.digest() == "a5b63e62a03839b8a2fd4182feb09fa9478e2a2097f9074e0194cb4315d574a5"
assert (wheel_root / "fleet_rlm/bundled_skills/delegation/SKILL.md").is_file()
assert not (wheel_root / "fleet_rlm/release_checks.py").exists()
assert not (wheel_root / "fleet_rlm/program_file.py").exists()
import dspy
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
fixture = Path(sys.argv[3])
saved = json.loads(fixture.read_text())
lm = dspy.LM("openai/offline", engine="lm15", cache=False)
engine = AdaptiveFleetEngine(
    DaytonaSandboxManager("key", "org", "volume", "wheel-state"),
    lm, lm, compiled_path=str(fixture),
)
assert engine.dump_state() == saved
assert engine.flex_agent.module_src == saved["flex_agent"]["module_src"]
print(json.dumps({"module": str(module_path), "fingerprint": evidence["dependency_fingerprint"]}))
"""
    environment = os.environ.copy()
    for name in ("FLEET_CONFIG", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "MLFLOW_TRACKING_URI"):
        environment.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code, str(target), sysconfig.get_paths()["purelib"], str(fixture)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert Path(result["module"]).is_relative_to(target)
    assert len(result["fingerprint"]) == 64


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
