"""Exact-final ``dspy==3.3.1`` runtime guard certification.

Covers VAL-PKG-005/006/007/008 and the deterministic VAL-CROSS-002 lanes:
composition/import validation and the public ``fleet``/``fleet-rlm`` CLI
surfaces fail closed unless the installed DSPy is exactly the final published
``3.3.1`` release. Rejection is a literal string comparison (not PEP 440
specifier equality, so ``3.3.1+local`` and every prerelease/post-release are
rejected), happens before any provider, database, or Daytona resource
construction, and leaves no listener on the validator ports. Spy/counter
instrumentation lives only in this private test lane plus the
``_dspy_version_guard_spy.py`` subprocess harness.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SPY_HARNESS = Path(__file__).with_name("_dspy_version_guard_spy.py")
CIRCLECI_CONFIG = REPO_ROOT / ".circleci" / "config.yml"
EVIDENCE_ROOT = REPO_ROOT / ".fleet-evidence"
RECEIPTS_DIR = EVIDENCE_ROOT / "receipts" / "p35a" / "exact-version-guard"

CERTIFIED_VERSION = "3.3.1"
REJECTED_VERSIONS = (
    # Neighboring patches (VAL-PKG-006).
    "3.3.0",
    "3.3.2",
    # Prereleases, post release, and local segment (VAL-PKG-007).
    "3.3.1.dev1",
    "3.3.1a1",
    "3.3.1b1",
    "3.3.1rc1",
    "3.3.1.post1",
    "3.3.1+local",
    # Malformed string and other release lines (VAL-PKG-008).
    "not-a-version",
    "3.2.9",
    "3.4.0",
    "4.0.0",
)
# Representative variants re-proven through every startup surface; the strict
# production Daytona composition entry covers the complete matrix on its own.
_SURFACE_SAMPLE_VERSIONS = ("3.3.0", "3.3.2", "3.3.1+local", "not-a-version")

_GUARD_COUNTER_LABELS = ("guard", "database", "daytona", "provider", "server")
_GUARD_REJECTED_EXIT = 3

_ENV_BLOCKED_KEYS = (
    "FLEET_DAYTONA_API_KEY",
    "FLEET_OPENAI_API_KEY",
    "FLEET_LLM_BASE_URL",
    "FLEET_DATABASE_URL",
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_BASE_URL",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DATABRICKS_HOST",
    "DATABRICKS_TOKEN",
    "MLFLOW_TRACKING_URI",
    "PYDANTIC_AI_GATEWAY_API_KEY",
    "MODAL_PROXY_TOKEN",
    "HF_TOKEN",
)


def _hermetic_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Subprocess environment with provider/Daytona access masked out."""
    env = {
        key: value for key, value in os.environ.items() if key not in _ENV_BLOCKED_KEYS and not key.startswith("FLEET_")
    }
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
    if extra:
        env.update(extra)
    return env


def _record_receipt(name: str, payload: dict[str, Any]) -> None:
    """Collect per-lane receipts when the private evidence root exists."""
    if not EVIDENCE_ROOT.is_dir():
        return
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    receipt = RECEIPTS_DIR / f"{name}.json"
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _port_closed(port: int, *, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) != 0


def _run_spy(mode: str, reported_version: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SPY_HARNESS), mode, reported_version],
        cwd=REPO_ROOT,
        env=_hermetic_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    json_lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    assert json_lines, (
        f"harness produced no JSON record for {mode}/{reported_version}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    report = json.loads(json_lines[-1])
    return {
        "mode": mode,
        "reported_version": reported_version,
        "exit_code": proc.returncode,
        "stderr": proc.stderr,
        "report": report,
    }


def _assert_zero_resource_construction(report: dict[str, Any]) -> None:
    counts = report["counts"]
    assert set(counts) == set(_GUARD_COUNTER_LABELS)
    assert counts["database"] == 0, counts
    assert counts["daytona"] == 0, counts
    assert counts["provider"] == 0, counts
    assert counts["server"] == 0, counts


def _assert_bounded_exact_version_error(message: str) -> None:
    assert "exactly DSPy 3.3.1" in message
    assert len(message) <= 256
    for forbidden in ("Traceback", "Exception", "site-packages"):
        assert forbidden not in message


class TestGuardStaticContract:
    """The certified constant and error shape stay pinned in production code."""

    def test_certified_constant_is_exact_final_release(self) -> None:
        from fleet_rlm.rlm.dspy_contract import CERTIFIED_DSPY_VERSION, UncertifiedDSpyVersionError

        assert CERTIFIED_DSPY_VERSION == "3.3.1"
        assert issubclass(UncertifiedDSpyVersionError, RuntimeError)

    def test_guard_uses_literal_comparison_only(self) -> None:
        source = (REPO_ROOT / "src" / "fleet_rlm" / "rlm" / "dspy_contract.py").read_text(encoding="utf-8")
        assert "packaging.version" not in source
        assert "Version(" not in source


class TestCircleCiExactVersionCheck:
    """CI must pin the literal final release, not a floating 3.3.x window."""

    def test_python_compat_job_requires_literal_3_3_1(self) -> None:
        text = CIRCLECI_CONFIG.read_text(encoding="utf-8")
        assert 'assert dspy.__version__ == "3.3.1", dspy.__version__' in text
        for legacy in ('startswith("3.3.', "startswith('3.3."):
            assert legacy not in text


@pytest.mark.parametrize("mode", ("create-app", "composition-local", "composition-daytona"))
class TestCertifiedReleaseAccepted:
    """VAL-PKG-005: exact final 3.3.1 composes; resources only after the guard."""

    def test_guard_accepts_certified_release(self, mode: str) -> None:
        lane = _run_spy(mode, CERTIFIED_VERSION)
        report = lane["report"]
        assert lane["exit_code"] == 0, lane
        assert report["outcome"] == "accepted", report
        order = report["order"]
        assert order[:1] == ["guard"], f"guard must fire first in {mode}: {order}"
        # Every resource construction (if any) happens strictly after the guard.
        resource_counts = {label: report["counts"][label] for label in ("database", "daytona", "provider")}
        first_resource_index = next(
            (index for index, label in enumerate(order) if label in ("database", "daytona", "provider")),
            None,
        )
        assert first_resource_index is None or first_resource_index > order.index("guard")
        _record_receipt(
            f"spy-{mode}-accepted-{CERTIFIED_VERSION}",
            {
                **lane,
                "assertion": "VAL-PKG-005 acceptance; spies prove guard precedes resource construction",
                "resource_counts": resource_counts,
            },
        )


@pytest.mark.parametrize("reported_version", REJECTED_VERSIONS)
class TestUncertifiedRuntimeRejectedFailClosed:
    """VAL-PKG-006/007/008: every uncertified runtime fails closed first."""

    def test_production_daytona_composition_rejects_before_resources(self, reported_version: str) -> None:
        lane = _run_spy("composition-daytona", reported_version)
        report = lane["report"]
        assert lane["exit_code"] == _GUARD_REJECTED_EXIT, lane
        assert report["outcome"] == "rejected"
        assert report["error_type"] == "UncertifiedDSpyVersionError"
        _assert_bounded_exact_version_error(report["error_message"])
        assert report["order"] == ["guard"]
        _assert_zero_resource_construction(report)
        _record_receipt(f"spy-composition-daytona-rejected-{reported_version}", lane)


@pytest.mark.parametrize("mode", ("create-app", "composition-local", "composition-daytona"))
@pytest.mark.parametrize("reported_version", _SURFACE_SAMPLE_VERSIONS)
def test_composition_entry_points_reject_uncertified_runtimes(mode: str, reported_version: str) -> None:
    lane = _run_spy(mode, reported_version)
    report = lane["report"]
    assert lane["exit_code"] == _GUARD_REJECTED_EXIT, lane
    assert report["outcome"] == "rejected"
    assert report["error_type"] == "UncertifiedDSpyVersionError"
    _assert_bounded_exact_version_error(report["error_message"])
    assert report["order"] == ["guard"]
    _assert_zero_resource_construction(report)
    _record_receipt(f"spy-{mode}-rejected-{reported_version}", lane)


@pytest.mark.parametrize("mode", ("cli-serve-api", "cli-web"))
@pytest.mark.parametrize("reported_version", _SURFACE_SAMPLE_VERSIONS)
def test_cli_entry_points_exit_nonzero_on_uncertified_runtimes(mode: str, reported_version: str) -> None:
    lane = _run_spy(mode, reported_version)
    report = lane["report"]
    assert lane["exit_code"] != 0, lane
    assert report["cli_exit_code"] != 0
    _assert_bounded_exact_version_error(report["cli_stderr"])
    assert report["order"] == ["guard"]
    _assert_zero_resource_construction(report)
    _record_receipt(f"spy-{mode}-rejected-{reported_version}", lane)


@pytest.mark.parametrize("mode", ("cli-serve-api", "cli-web"))
def test_cli_entry_points_proceed_normally_on_certified_runtime(mode: str) -> None:
    lane = _run_spy(mode, CERTIFIED_VERSION)
    report = lane["report"]
    assert lane["exit_code"] == 0, lane
    assert report["cli_exit_code"] == 0
    # The serving call (stubbed) is reached only after the guard succeeded.
    assert report["counts"]["server"] == 1
    assert report["order"][0] == "guard"
    server_index = report["order"].index("server")
    assert server_index > report["order"].index("guard")
    _record_receipt(f"spy-{mode}-accepted-{CERTIFIED_VERSION}", lane)


class TestBlackBoxPublicStartupRejection:
    """VAL-CROSS-002 lanes: the installed console scripts fail closed with no listener.

    Each lane injects the reported version through a private ``sitecustomize``
    shim and runs the real installed entry point with credential-free
    environment; the guard must exit the process before any bind.
    """

    @staticmethod
    def _entry_script(name: str) -> Path:
        candidate = Path(sys.executable).parent / name
        if not candidate.exists():
            pytest.fail(f"installed console script {name} not found next to {sys.executable}")
        return candidate

    @staticmethod
    def _write_version_shim(base: Path, reported_version: str) -> Path:
        shim_dir = base / f"shim-{reported_version.replace('+', 'PLUS').replace('/', '_')}"
        shim_dir.mkdir(parents=True, exist_ok=True)
        (shim_dir / "sitecustomize.py").write_text(
            "import dspy\n\ndspy.__version__ = " + repr(reported_version) + "\n",
            encoding="utf-8",
        )
        return shim_dir

    def test_fleet_and_fleet_rlm_reject_uncertified_runtimes_without_listener(self, tmp_path: Path) -> None:
        port = 8011
        assert _port_closed(port), "validator port 8011 must be free before black-box guard lanes"
        lanes = [("fleet-rlm", "serve-api", version) for version in REJECTED_VERSIONS]
        lanes += [("fleet", "web", version) for version in _SURFACE_SAMPLE_VERSIONS]
        transcripts: list[dict[str, Any]] = []
        for entry_name, command, reported_version in lanes:
            script = self._entry_script(entry_name)
            shim_dir = self._write_version_shim(tmp_path, reported_version)
            run_cwd = tmp_path / f"run-{entry_name}-{command}-{len(transcripts)}"
            run_cwd.mkdir(parents=True)
            env = _hermetic_env({"PYTHONPATH": str(shim_dir)})
            try:
                proc = subprocess.run(
                    [str(script), command, "--host", "127.0.0.1", "--port", str(port)],
                    cwd=run_cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise AssertionError(
                    f"{entry_name} {command} with reported dspy {reported_version!r} did not exit promptly; "
                    "the guard must fail closed before serving"
                ) from exc
            combined_output = f"{proc.stdout}\n{proc.stderr}"
            assert proc.returncode != 0, (
                f"{entry_name} {command} accepted reported dspy {reported_version!r}:\n{combined_output}"
            )
            assert "exactly DSPy 3.3.1" in combined_output, combined_output
            assert _port_closed(port), f"listener left behind on 127.0.0.1:{port}"
            transcripts.append(
                {
                    "entry_point": entry_name,
                    "command": command,
                    "reported_version": reported_version,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "port_listening_after_exit": not _port_closed(port),
                }
            )
        _record_receipt(
            "blackbox-public-startup-rejections-port-8011",
            {"port": port, "assertion": "VAL-CROSS-002 rejection lanes", "lanes": transcripts},
        )
        assert _port_closed(port), "validator port 8011 must still be free after black-box guard lanes"

    def test_certified_runtime_proceeds_past_guard(self, tmp_path: Path) -> None:
        """Acceptance control: installed exact 3.3.1 gets past the version guard."""
        port = 8011
        assert _port_closed(port), "validator port 8011 must be free before the acceptance control"
        script = self._entry_script("fleet-rlm")
        env = _hermetic_env()
        proc = subprocess.run(
            [str(script), "serve-api", "--host", "127.0.0.1", "--port", str(port)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        combined_output = f"{proc.stdout}\n{proc.stderr}"
        # With no Fleet policy/credentials available in the hermetic cwd the app
        # still must fail closed on configuration AFTER the version guard; the
        # guard itself must not fire on the certified release.
        assert proc.returncode != 0
        assert "exactly DSPy 3.3.1" not in combined_output
        assert _port_closed(port), f"acceptance control left a listener on 127.0.0.1:{port}"
        _record_receipt(
            "blackbox-public-startup-acceptance-3.3.1",
            {
                "port": port,
                "assertion": "certified runtime passes the guard (startup then fails closed on absent policy)",
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
        )


def test_guard_matrix_wall_clock_smoke_budget() -> None:
    """Guard lanes are deterministic: the harness itself always exits decisively."""
    started = time.monotonic()
    lane = _run_spy("composition-daytona", "3.3.2")
    assert lane["report"]["outcome"] == "rejected"
    assert time.monotonic() - started < 90, "rejection lanes must stay bounded in wall-clock time"
