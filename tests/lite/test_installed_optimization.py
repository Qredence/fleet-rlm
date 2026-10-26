"""Exercise approval evidence from an installed wheel, outside the source checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace

from fleet_rlm.optimize import gepa

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

    code = r"""
import json
import sys
from pathlib import Path

wheel_root = Path(sys.argv[1]).resolve()
# -S avoids executing editable-install .pth files. Add the already-installed host
# dependencies explicitly, with the real wheel target ahead of them.
sys.path[:0] = [str(wheel_root), sys.argv[2]]
from fleet_rlm.config import FleetSettings
from fleet_rlm.optimize import gepa

module_path = Path(gepa.__file__).resolve()
assert module_path.is_relative_to(wheel_root), module_path
assert not (Path.cwd() / "uv.lock").exists()
evidence = gepa.approval_evidence(FleetSettings(_env_file=None), "incumbent")
assert evidence["evidence_format"] == "2"
assert len(evidence["dependency_fingerprint"]) == 64
assert "lockfile_digest" not in evidence
print(json.dumps({"module": str(module_path), "fingerprint": evidence["dependency_fingerprint"]}))
"""
    environment = os.environ.copy()
    for name in ("FLEET_CONFIG", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "MLFLOW_TRACKING_URI"):
        environment.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code, str(target), sysconfig.get_paths()["purelib"]],
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
        gepa,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.3")],
    )
    original = gepa.installed_dependency_fingerprint()
    monkeypatch.setattr(
        gepa,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.4")],
    )
    assert gepa.installed_dependency_fingerprint() != original
