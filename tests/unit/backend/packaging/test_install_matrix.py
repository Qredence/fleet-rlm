"""Clean-environment installation matrix, CLI entry points, and Python support certification.

Fulfills:
- VAL-PKG-017: Clean base wheel installs on supported Python (3.11, 3.12, 3.13)
- VAL-PKG-018: Clean optimize wheel installs on supported Python (3.11, 3.12, 3.13)
- VAL-PKG-019: Clean base sdist installs on supported Python (3.11, 3.12, 3.13)
- VAL-PKG-020: Clean optimize sdist installs on supported Python (3.11, 3.12, 3.13)
- VAL-PKG-021: Unsupported Python versions fail declaratively (3.10, 3.14)
- VAL-PKG-022: Installed CLI entry-point metadata
- VAL-PKG-023: Installed CLI smoke behavior
- VAL-PKG-027: Artifact installation succeeds without a VCS client
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = ROOT / "pyproject.toml"

SUPPORTED_PYTHONS = ("3.11", "3.12", "3.13")
UNSUPPORTED_PYTHONS = ("3.10", "3.14")


def _vcs_free_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return environment without git in PATH and without repo on PYTHONPATH."""
    env = os.environ.copy()
    paths = env.get("PATH", "").split(os.pathsep)
    vcs_free_paths = [p for p in paths if not (Path(p) / "git").exists()]
    env["PATH"] = os.pathsep.join(vcs_free_paths)
    env.pop("PYTHONPATH", None)
    # Block live provider / daytona env vars during smoke tests
    for k in list(env.keys()):
        if k.startswith("FLEET_") or "API_KEY" in k or "TOKEN" in k:
            env.pop(k, None)
    if extra_env:
        env.update(extra_env)
    return env


@pytest.fixture(scope="module")
def built_artifacts() -> tuple[Path, Path]:
    """Ensure clean build of universal wheel and sdist."""
    dist_dir = Path(os.environ.get("FLEET_RELEASE_DIST", ROOT / "dist"))
    if "FLEET_RELEASE_DIST" not in os.environ:
        subprocess.run(["uv", "build"], cwd=ROOT, check=True, capture_output=True)
    wheels = sorted(dist_dir.glob("fleet_rlm-*.whl"))
    sdists = sorted(dist_dir.glob("fleet_rlm-*.tar.gz"))
    assert len(wheels) == 1, f"Expected 1 wheel in dist/, found {wheels}"
    assert len(sdists) == 1, f"Expected 1 sdist in dist/, found {sdists}"
    return wheels[0], sdists[0]


@pytest.fixture(scope="module")
def project_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


class TestInstallMatrixAndCliSmoke:
    """VAL-PKG-017, VAL-PKG-018, VAL-PKG-019, VAL-PKG-020, VAL-PKG-022, VAL-PKG-023, VAL-PKG-027."""

    @pytest.mark.parametrize("py_ver", SUPPORTED_PYTHONS)
    @pytest.mark.parametrize("artifact_type", ["wheel", "sdist"])
    def test_clean_base_install_and_cli_smoke(
        self,
        py_ver: str,
        artifact_type: str,
        built_artifacts: tuple[Path, Path],
        project_version: str,
    ) -> None:
        """VAL-PKG-017 (wheel base), VAL-PKG-019 (sdist base), VAL-PKG-022, VAL-PKG-023, VAL-PKG-027."""
        wheel, sdist = built_artifacts
        artifact_path = wheel if artifact_type == "wheel" else sdist

        with tempfile.TemporaryDirectory() as tmpdir:
            venv_dir = Path(tmpdir) / "venv"
            # Create clean venv with uv
            subprocess.run(
                ["uv", "venv", "--python", py_ver, str(venv_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            python_bin = venv_dir / "bin" / "python"
            assert python_bin.exists()

            # Install base artifact using uv pip in vcs-free environment
            env = _vcs_free_env({"VIRTUAL_ENV": str(venv_dir)})
            proc_install = subprocess.run(
                ["uv", "pip", "install", "--python", str(python_bin), str(artifact_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_install.returncode == 0, f"Install failed:\n{proc_install.stdout}\n{proc_install.stderr}"
            # Ensure no VCS cloning or git command occurred during install
            assert "git clone" not in proc_install.stderr.lower()
            assert "git+" not in proc_install.stderr.lower()

            # Verify import outside repo directory (cwd=tmpdir)
            verify_script = (
                "import importlib.metadata\n"
                "import fleet_rlm\n"
                "import dspy\n"
                "assert fleet_rlm.__version__ == '" + project_version + "'\n"
                "assert dspy.__version__ == '3.3.1'\n"
                "eps = {ep.name: ep.value for ep in importlib.metadata.entry_points(group='console_scripts')}\n"
                "assert eps['fleet'] == 'fleet_rlm.cli.main:fleet_main'\n"
                "assert eps['fleet-rlm'] == 'fleet_rlm.cli.main:fleet_rlm_main'\n"
                "print('OK')\n"
            )
            proc_verify = subprocess.run(
                [str(python_bin), "-c", verify_script],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_verify.returncode == 0, f"Verification failed:\n{proc_verify.stdout}\n{proc_verify.stderr}"
            assert "OK" in proc_verify.stdout

            # Verify CLI entry points: fleet --help and fleet-rlm --help
            fleet_bin = venv_dir / "bin" / "fleet"
            fleet_rlm_bin = venv_dir / "bin" / "fleet-rlm"
            assert fleet_bin.exists()
            assert fleet_rlm_bin.exists()

            proc_fleet_help = subprocess.run(
                [str(fleet_bin), "--help"],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_fleet_help.returncode == 0, f"fleet --help failed:\n{proc_fleet_help.stderr}"
            assert "web" in proc_fleet_help.stdout
            assert "cli" in proc_fleet_help.stdout
            assert "doctor" in proc_fleet_help.stdout

            proc_fleet_rlm_help = subprocess.run(
                [str(fleet_rlm_bin), "--help"],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_fleet_rlm_help.returncode == 0, f"fleet-rlm --help failed:\n{proc_fleet_rlm_help.stderr}"
            assert "serve-api" in proc_fleet_rlm_help.stdout

    @pytest.mark.parametrize("py_ver", SUPPORTED_PYTHONS)
    @pytest.mark.parametrize("artifact_type", ["wheel", "sdist"])
    def test_clean_optimize_install(
        self,
        py_ver: str,
        artifact_type: str,
        built_artifacts: tuple[Path, Path],
        project_version: str,
    ) -> None:
        """VAL-PKG-018 (wheel optimize), VAL-PKG-020 (sdist optimize), VAL-PKG-027."""
        wheel, sdist = built_artifacts
        artifact_spec = f"{wheel}[optimize]" if artifact_type == "wheel" else f"{sdist}[optimize]"

        with tempfile.TemporaryDirectory() as tmpdir:
            venv_dir = Path(tmpdir) / "venv"
            # Create clean venv with uv
            subprocess.run(
                ["uv", "venv", "--python", py_ver, str(venv_dir)],
                check=True,
                capture_output=True,
                text=True,
            )
            python_bin = venv_dir / "bin" / "python"

            # Install optimize extra artifact using uv pip in vcs-free environment
            env = _vcs_free_env({"VIRTUAL_ENV": str(venv_dir)})
            proc_install = subprocess.run(
                ["uv", "pip", "install", "--python", str(python_bin), artifact_spec],
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_install.returncode == 0, (
                f"Optimize install failed:\n{proc_install.stdout}\n{proc_install.stderr}"
            )
            assert "git clone" not in proc_install.stderr.lower()
            assert "git+" not in proc_install.stderr.lower()

            # Verify gepa and dspy in clean optimize environment
            verify_script = (
                "import importlib.metadata\n"
                "import fleet_rlm\n"
                "import dspy\n"
                "import gepa\n"
                "assert fleet_rlm.__version__ == '" + project_version + "'\n"
                "assert dspy.__version__ == '3.3.1'\n"
                "assert importlib.metadata.version('gepa') == '0.1.4'\n"
                "print('OK_OPTIMIZE')\n"
            )
            proc_verify = subprocess.run(
                [str(python_bin), "-c", verify_script],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_verify.returncode == 0, (
                f"Optimize verification failed:\n{proc_verify.stdout}\n{proc_verify.stderr}"
            )
            assert "OK_OPTIMIZE" in proc_verify.stdout


class TestUnsupportedPythonRejection:
    """VAL-PKG-021: Unsupported Python versions fail declaratively."""

    @pytest.mark.parametrize("py_ver", UNSUPPORTED_PYTHONS)
    @pytest.mark.parametrize("artifact_type", ["wheel", "sdist"])
    def test_unsupported_python_fails_declaratively(
        self,
        py_ver: str,
        artifact_type: str,
        built_artifacts: tuple[Path, Path],
    ) -> None:
        wheel, sdist = built_artifacts
        artifact_path = wheel if artifact_type == "wheel" else sdist

        with tempfile.TemporaryDirectory() as tmpdir:
            venv_dir = Path(tmpdir) / "venv"
            # Create clean seeded venv with uv for unsupported python
            proc_venv = subprocess.run(
                ["uv", "venv", "--python", py_ver, "--seed", str(venv_dir)],
                capture_output=True,
                text=True,
            )
            if proc_venv.returncode != 0:
                pytest.skip(f"Python {py_ver} not available on host: {proc_venv.stderr}")

            python_bin = venv_dir / "bin" / "python"
            assert python_bin.exists()

            # Attempt install via pip; must fail declaratively due to Requires-Python
            env = _vcs_free_env({"VIRTUAL_ENV": str(venv_dir)})
            proc_install = subprocess.run(
                [str(python_bin), "-m", "pip", "install", "--no-deps", str(artifact_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc_install.returncode != 0, f"Expected installation failure on Python {py_ver}, but it succeeded!"
            combined_out = proc_install.stdout + proc_install.stderr
            assert (
                "requires a different python" in combined_out.lower()
                or "requires-python" in combined_out.lower()
                or "incompatible" in combined_out.lower()
                or "not in '<3.14,>=3.11'" in combined_out
                or "not in '>=3.11,<3.14'" in combined_out
            ), f"Expected Requires-Python rejection on Python {py_ver}, got:\n{combined_out}"
