"""Unit contracts for the narrow Phase 2 recursive Daytona verifier."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import live_phase2_recursive_verify as verifier

_LOAD_REPO_ENV = verifier._load_repo_env


@pytest.fixture(autouse=True)
def _avoid_loading_repository_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Disable repository credential loading and path restrictions for the test.
    """
    monkeypatch.setattr(verifier, "_load_repo_env", lambda: None)
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)


def _test_receipt() -> dict[str, object]:
    return {
        "schema": verifier.RECEIPT_SCHEMA,
        "timing": {"turn_duration_ms": 800, "child_duration_ms": 180},
        "assertions": {name: True for name in verifier._REQUIRED_ASSERTIONS},
        "failure": None,
        "passed": True,
    }


def _settings() -> object:
    """
    Create Daytona recursive execution settings for verifier tests.
    
    Returns:
    	object: Settings configured with the live verifier models and the Daytona recursive profile.
    """
    class Settings:
        run_environment = "daytona"
        root_model = verifier._LIVE_ROOT_MODEL
        sub_model = verifier._LIVE_SUB_MODEL
        rlm_recursion_enabled = True

    settings = Settings()
    settings._active_profile = "daytona-recursive"
    return settings


def test_pytest_command_is_exact_and_has_no_retry() -> None:
    assert verifier.pytest_command(900) == [
        "uv",
        "run",
        "pytest",
        "tests/live/backend/test_phase2_daytona_recursive.py::test_phase2_daytona_recursive_through_fastapi",
        "-q",
        "-n",
        "0",
        "--timeout=900",
    ]


def test_disabled_policy_stops_before_candidate_or_provider_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    calls: list[str] = []
    monkeypatch.setattr(
        verifier,
        "require_live_execution",
        lambda: (_ for _ in ()).throw(verifier.FleetConfigurationError("disabled")),
    )
    monkeypatch.setattr(verifier, "_candidate", lambda: calls.append("candidate"))
    monkeypatch.setattr(verifier.subprocess, "run", lambda *_args, **_kwargs: calls.append("subprocess"))

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert calls == []
    assert json.loads(output.read_text(encoding="utf-8"))["failure"] == {
        "category": "precondition_failed",
        "phase": "policy",
    }


def test_unsafe_output_path_is_rejected_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "unsafe.json"
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: False)

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert not output.exists()


def test_success_receipt_is_enriched_and_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(verifier, "require_live_execution", _settings)
    monkeypatch.setattr(verifier, "active_profile", lambda settings: settings._active_profile)
    monkeypatch.setattr(verifier, "_candidate", lambda: ("a" * 40, "dev-0.7"))
    monkeypatch.setattr(
        verifier,
        "_installed_versions",
        lambda _env: {"python": "3.13.13", "dspy": "3.3.0", "daytona": "0.199.0"},
    )
    monkeypatch.setattr(verifier, "_lockfile_sha256", lambda: "b" * 64)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 930
        evidence_path = Path(kwargs["env"][verifier.EVIDENCE_ENV])  # type: ignore[index]
        evidence_path.write_text(json.dumps(_test_receipt()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verifier.subprocess, "run", run)

    assert verifier.main(["--output", str(output)]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["candidate"]["sha"] == "a" * 40
    assert receipt["policy"] == {
        "profile": "daytona-recursive",
        "environment": "daytona",
        "root_model": verifier._LIVE_ROOT_MODEL,
        "sub_model": verifier._LIVE_SUB_MODEL,
    }
    assert all(receipt["assertions"].values())
    serialized = json.dumps(receipt).lower()
    for forbidden in ("prompt", "answer", "code", "sandbox_id", "volume_id", "broker", "trace"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "interruption",
    (subprocess.TimeoutExpired(["uv", "run", "pytest"], 930), KeyboardInterrupt()),
    ids=("timeout", "keyboard_interrupt"),
)
def test_scenario_interruption_writes_bounded_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: BaseException,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(verifier, "require_live_execution", _settings)
    monkeypatch.setattr(verifier, "active_profile", lambda settings: settings._active_profile)
    monkeypatch.setattr(verifier, "_candidate", lambda: ("a" * 40, "dev-0.7"))
    calls: dict[str, object] = {}

    def run(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        """
        Record subprocess invocation options and simulate an interruption.
        
        Parameters:
        	_command (list[str]): The command whose execution is being simulated
        	**kwargs (object): Subprocess invocation options to record
        
        Raises:
        		BaseException: The configured interruption.
        """
        calls.update(kwargs)
        raise interruption

    monkeypatch.setattr(verifier.subprocess, "run", run)

    assert verifier.main(["--output", str(output), "--timeout-seconds", "900"]) == verifier.EXIT_INTERRUPTED
    assert calls["timeout"] == 930
    assert json.loads(output.read_text(encoding="utf-8"))["failure"] == {
        "category": "interrupted",
        "phase": "scenario",
    }


@pytest.mark.parametrize(
    "receipt",
    (
        {**_test_receipt(), "timing": {"turn_duration_ms": -1, "child_duration_ms": 1}},
        {**_test_receipt(), "unexpected": True},
        {**_test_receipt(), "assertions": {**_test_receipt()["assertions"], "sandbox_id": True}},
    ),
    ids=("negative_duration", "extra_field", "raw_resource_identifier"),
)
def test_receipt_validation_rejects_invalid_or_unsafe_shape(receipt: dict[str, object]) -> None:
    with pytest.raises(verifier.ReceiptError):
        verifier.validate_test_receipt(receipt)


def test_inherited_environment_values_win_over_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "inherited")
    observed: dict[str, object] = {}

    def load_dotenv(path: Path, *, override: bool) -> None:
        """
        Record dotenv loading options and optionally set the test API key.
        
        Parameters:
            override (bool): Whether to replace the existing API key environment variable.
        """
        observed["path"] = path
        observed["override"] = override
        if override:
            os.environ["FLEET_DAYTONA_API_KEY"] = "dotenv"

    monkeypatch.setattr(verifier, "load_dotenv", load_dotenv)
    _LOAD_REPO_ENV()

    assert observed["override"] is False
    assert os.environ["FLEET_DAYTONA_API_KEY"] == "inherited"
