"""Contracts for the bounded live Daytona verification CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts import live_daytona_verify as verifier


def _success_receipt(sha: str) -> dict[str, object]:
    return {
        "schema": verifier.RECEIPT_SCHEMA,
        "candidate": {
            "sha": sha,
            "branch": "dev-0.7",
            "tracked_tree_clean": True,
            "versions": {
                "python": "3.13.13",
                "dspy": "3.3.0b1",
                "daytona": "0.197.0",
            },
            "lockfile_sha256": "d" * 64,
        },
        "timing": {
            "started_at": "2026-07-17T08:00:00+00:00",
            "finished_at": "2026-07-17T08:00:01+00:00",
            "duration_ms": 1000,
        },
        "models": {"root": "openai/root", "sub": "openai/sub"},
        "resources": {
            "session_id": "00000000-0000-0000-0000-000000000001",
            "run_ids": ["00000000-0000-0000-0000-000000000002"],
            "sandbox_ids": ["sandbox-1", "sandbox-2"],
            "volume_id": "volume-1",
        },
        "counts": {
            "iterations": 5,
            "single_lm_calls": 1,
            "batched_lm_calls": 3,
            "host_tool_calls": 5,
            "sse_start": 2,
            "sse_finish": 2,
            "sse_done": 2,
        },
        "checksums": {
            "snapshot_sha256": "a" * 64,
            "workspace_sha256": "b" * 64,
            "typed_result_sha256": "c" * 64,
        },
        "assertions": {
            "typed_submit": True,
            "stateful_iterations": True,
            "fresh_replacement_context": True,
            "workspace_survived_replacement": True,
            "history_reload_identical": True,
            "secret_audit_passed": True,
            "cleanup_passed": True,
        },
        "failure": None,
        "passed": True,
    }


def test_help_is_credential_free(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        verifier.build_parser().parse_args(["--help"])

    help_text = capsys.readouterr().out
    assert "--output" in help_text
    assert "--root-model" in help_text
    assert "--sub-model" in help_text


def test_model_overrides_must_be_paired(tmp_path: Path) -> None:
    assert (
        verifier.main(
            [
                "--output",
                str(tmp_path / "receipt.json"),
                "--root-model",
                "openai/root",
            ]
        )
        == verifier.EXIT_PRECONDITION
    )


def test_empty_model_override_is_rejected() -> None:
    with pytest.raises(SystemExit, match="2"):
        verifier.build_parser().parse_args(["--output", "receipt.json", "--root-model", " "])


def test_pytest_command_is_exact_and_has_no_retry() -> None:
    assert verifier.pytest_command(840) == [
        "uv",
        "run",
        "pytest",
        "tests/live/backend/test_fleet_rlm_daytona_mvp.py::test_complete_daytona_mvp_through_fastapi",
        "-q",
        "-n",
        "0",
        "--timeout=840",
    ]


def test_main_records_missing_live_precondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.delenv("FLEET_LIVE", raising=False)
    monkeypatch.delenv("FLEET_DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("FLEET_LLM_API_KEY", raising=False)

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["schema"] == verifier.RECEIPT_SCHEMA
    assert receipt["failure"] == {
        "category": "precondition_failed",
        "phase": "environment",
    }
    assert receipt["passed"] is False
    assert "FLEET_DAYTONA_API_KEY" not in output.read_text(encoding="utf-8")


def test_main_rejects_tracked_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "tracked.json"
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: False)

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert not output.exists()


def test_main_invokes_pytest_once_and_accepts_valid_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    sha = "1" * 40
    calls: list[tuple[list[str], dict[str, str], int]] = []
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: (sha, "dev-0.7"))

    def run_once(
        command: list[str],
        *,
        env: dict[str, str],
        timeout: int,
        check: bool,
        stdout: int,
        stderr: int,
    ) -> subprocess.CompletedProcess[str]:
        del check, stdout, stderr
        calls.append((command, env, timeout))
        output.write_text(json.dumps(_success_receipt(sha)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verifier.subprocess, "run", run_once)

    assert (
        verifier.main(
            [
                "--output",
                str(output),
                "--timeout-seconds",
                "840",
                "--root-model",
                "openai/root-override",
                "--sub-model",
                "openai/sub-override",
            ]
        )
        == 0
    )
    assert len(calls) == 1
    command, child_env, timeout = calls[0]
    assert command == verifier.pytest_command(840)
    assert child_env[verifier.EVIDENCE_ENV] == str(output.resolve())
    assert child_env["FLEET_ROOT_MODEL"] == "openai/root-override"
    assert child_env["FLEET_SUB_MODEL"] == "openai/sub-override"
    assert timeout == 900
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


@pytest.mark.parametrize(
    ("mutation", "expected_phase"),
    [
        (lambda receipt: receipt.update(schema="unexpected"), "receipt_schema"),
        (lambda receipt: receipt["candidate"].update(sha="2" * 40), "candidate_fingerprint"),
        (lambda receipt: receipt["candidate"].update(unbounded="value"), "candidate_fields"),
        (lambda receipt: receipt["assertions"].update(cleanup_passed=False), "receipt_assertions"),
        (lambda receipt: receipt.update(extra="not-allowlisted"), "receipt_fields"),
    ],
)
def test_main_replaces_invalid_receipt_with_bounded_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, Any]], None],
    expected_phase: str,
) -> None:
    output = tmp_path / "receipt.json"
    sha = "3" * 40
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: (sha, "dev-0.7"))

    def run_once(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        receipt = _success_receipt(sha)
        mutation(receipt)
        output.write_text(json.dumps(receipt), encoding="utf-8")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(verifier.subprocess, "run", run_once)

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_RECEIPT
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["failure"] == {
        "category": "receipt_invalid",
        "phase": expected_phase,
    }
    assert failure["passed"] is False
    assert "secret-daytona" not in output.read_text(encoding="utf-8")


def test_main_records_pytest_failure_without_subprocess_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    sha = "4" * 40
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: (sha, "dev-0.7"))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PROOF
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["failure"] == {"category": "proof_failed", "phase": "pytest"}
    assert failure["passed"] is False


def test_main_rejects_success_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    sha = "5" * 40
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: (sha, "dev-0.7"))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_RECEIPT
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["failure"] == {"category": "receipt_invalid", "phase": "receipt_json"}
