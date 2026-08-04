"""Unit contracts for the narrow Phase 1 Daytona stream verifier."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import live_phase1_stream_verify as verifier

_LOAD_REPO_ENV = verifier._load_repo_env


@pytest.fixture(autouse=True)
def _avoid_loading_repository_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests credential-free even when the checkout has a ``.env``."""
    monkeypatch.setattr(verifier, "_load_repo_env", lambda: None)


def _test_receipt() -> dict[str, object]:
    return {
        "schema": verifier.RECEIPT_SCHEMA,
        "timing": {"first_delta_ms": 42, "duration_ms": 800},
        "streaming": {"delta_count": 2, "fields": ["code", "reasoning"]},
        "assertions": {
            "typed_submit": True,
            "attachment_prepared": True,
            "attachment_accessed": True,
            "single_semantic_call": True,
            "batched_semantic_call": True,
            "no_recursive_child": True,
            "terminal_ordering": True,
            "broker_session_cleanup": True,
            "turn_resources_cleanup": True,
        },
        "resources": {"sandbox_count": 1, "broker_session_count": 1, "owned_volume_only": True},
        "failure": None,
        "passed": True,
    }


def _settings(*, profile: str = "daytona") -> object:
    class Settings:
        run_environment = "daytona"
        root_model = verifier._LIVE_ROOT_MODEL
        sub_model = verifier._LIVE_SUB_MODEL

    settings = Settings()
    settings._active_profile = profile
    return settings


def test_pytest_command_is_exact_and_has_no_retry() -> None:
    assert verifier.pytest_command(840) == [
        "uv",
        "run",
        "pytest",
        "tests/live/backend/test_phase1_daytona_stream.py::test_phase1_daytona_stream_through_fastapi",
        "-q",
        "-n",
        "0",
        "--timeout=840",
    ]


def test_candidate_requires_the_canary_sources_to_be_tracked(monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter(("a" * 40, "dev-0.7", ""))
    monkeypatch.setattr(verifier, "_git", lambda *_args: next(values))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1),
    )

    with pytest.raises(RuntimeError, match="canary files are not committed"):
        verifier._candidate()


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


def test_unsafe_output_path_is_rejected_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "tracked.json"
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: False)

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert not output.exists()


def test_success_receipt_is_enriched_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    test_receipt = tmp_path / "test-receipt.json"
    monkeypatch.setattr(verifier, "require_live_execution", lambda: _settings())
    monkeypatch.setattr(verifier, "active_profile", lambda settings: settings._active_profile)
    monkeypatch.setattr(verifier, "_candidate", lambda: ("a" * 40, "dev-0.7"))
    monkeypatch.setattr(
        verifier,
        "_installed_versions",
        lambda _env: {"python": "3.13.13", "dspy": "3.3.0b1", "daytona": "0.199.0"},
    )
    monkeypatch.setattr(verifier, "_lockfile_sha256", lambda: "b" * 64)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        evidence_path = Path(kwargs["env"][verifier.EVIDENCE_ENV])  # type: ignore[index]
        assert evidence_path == test_receipt
        evidence_path.write_text(json.dumps(_test_receipt()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        verifier.tempfile,
        "mkstemp",
        lambda **_: (os.open(test_receipt, os.O_CREAT | os.O_RDWR), str(test_receipt)),
    )
    monkeypatch.setattr(verifier.subprocess, "run", run)

    assert verifier.main(["--output", str(output)]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["candidate"]["sha"] == "a" * 40
    assert receipt["policy"] == {
        "profile": "daytona",
        "environment": "daytona",
        "root_model": verifier._LIVE_ROOT_MODEL,
        "sub_model": verifier._LIVE_SUB_MODEL,
    }
    assert receipt["assertions"]["typed_submit"] is True
    serialized = json.dumps(receipt)
    for forbidden in ("attachment_content", "generated_code", "provider_response", "trace_id", "broker_url"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "receipt",
    (
        {**_test_receipt(), "streaming": {"delta_count": 0, "fields": []}},
        {**_test_receipt(), "unexpected": True},
        {**_test_receipt(), "resources": {"sandbox_id": "leak"}},
    ),
    ids=("no_delta", "extra_field", "raw_resource_identifier"),
)
def test_receipt_validation_rejects_invalid_or_unsafe_shape(receipt: dict[str, object]) -> None:
    with pytest.raises(verifier.ReceiptError):
        verifier.validate_test_receipt(receipt)


@pytest.mark.parametrize("value", [1, "true"], ids=("integer", "string"))
def test_receipt_validation_requires_boolean_true_assertions(value: object) -> None:
    receipt = _test_receipt()
    receipt["assertions"] = {**receipt["assertions"], "typed_submit": value}  # type: ignore[dict-item]

    with pytest.raises(verifier.ReceiptError, match="assertions"):
        verifier.validate_test_receipt(receipt)


def test_inherited_environment_values_win_over_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "inherited")
    observed: dict[str, object] = {}

    def load_dotenv(path: Path, *, override: bool) -> None:
        observed["path"] = path
        observed["override"] = override
        if override:
            os.environ["FLEET_DAYTONA_API_KEY"] = "dotenv"

    monkeypatch.setattr(verifier, "load_dotenv", load_dotenv)
    _LOAD_REPO_ENV()

    assert observed["override"] is False
    assert os.environ["FLEET_DAYTONA_API_KEY"] == "inherited"
