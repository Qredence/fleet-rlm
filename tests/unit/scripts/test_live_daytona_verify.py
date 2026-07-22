"""Contracts for the bounded live Daytona verification CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

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
                "daytona": "0.199.0",
            },
            "lockfile_sha256": "d" * 64,
        },
        "timing": {
            "started_at": "2026-07-17T08:00:00+00:00",
            "finished_at": "2026-07-17T08:00:01+00:00",
            "duration_ms": 1000,
        },
        "models": {
            "root": verifier._LIVE_MODEL,
            "sub": verifier._LIVE_MODEL,
        },
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
        "lanes": {
            "attachment_artifact_durability": {
                "order": 1,
                "passed": True,
                "evidence": {
                    "attachment_readable": True,
                    "artifact_survived_replacement": True,
                    "artifact_id": "artifact-1",
                    "artifact_checksum": "a" * 64,
                    "sandbox_ids": ["sandbox-1", "sandbox-2"],
                    "volume_id": "volume-1",
                },
            },
            "fastapi_dspy_daytona_mvp": {"order": 2, "passed": True},
        },
        "external_promotion": {
            "candidate_sha": sha,
            "ci": "pending",
            "human_approval": "pending",
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


def test_lane_commands_are_ordered_and_exact() -> None:
    assert verifier.lane_command("attachment_artifact_durability", 840) == [
        "uv",
        "run",
        "pytest",
        "tests/live/backend/test_b5_attachment_artifact_durability.py",
        "-q",
        "-n",
        "0",
        "--timeout=840",
    ]
    assert verifier.lane_command("fastapi_dspy_daytona_mvp", 840) == verifier.pytest_command(840)


def test_installed_versions_are_read_from_the_detached_candidate_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        calls.append((command, cwd, env))
        return subprocess.CompletedProcess(
            command, 0, stdout='{"python":"3.13.13","dspy":"3.3.0b1","daytona":"0.199.0"}'
        )

    monkeypatch.setattr(verifier.subprocess, "run", run)
    environment = {"FLEET_LIVE": "1"}

    assert verifier._installed_versions(tmp_path, environment) == {
        "python": "3.13.13",
        "dspy": "3.3.0b1",
        "daytona": "0.199.0",
    }
    assert calls[0][1] == tmp_path
    assert calls[0][2] is environment
    assert calls[0][0][:3] == ["uv", "run", "python"]


def test_main_records_missing_live_precondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(verifier, "_load_repo_env", lambda: None)
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


def test_main_rejects_unapproved_effective_models_before_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setenv("FLEET_ROOT_MODEL", "provider/private-model")
    monkeypatch.setenv("FLEET_SUB_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: ("a" * 40, "dev-0.7"))
    monkeypatch.setattr(
        verifier,
        "_create_detached_worktree",
        lambda *_args, **_kwargs: pytest.fail("worktree must not be created"),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PRECONDITION
    assert json.loads(output.read_text(encoding="utf-8"))["failure"] == {
        "category": "precondition_failed",
        "phase": "models",
    }


def test_first_lane_failure_skips_second_and_preserves_untracked_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    sentinel = tmp_path / "untracked-sentinel.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    worktree = tmp_path / "detached"
    worktree.mkdir()
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setenv("FLEET_ROOT_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setenv("FLEET_SUB_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: ("b" * 40, "dev-0.7"))
    monkeypatch.setattr(verifier, "_git", lambda *_args, **_kwargs: str(tmp_path))
    monkeypatch.setattr(verifier, "_create_detached_worktree", lambda *_args, **_kwargs: worktree)
    removed: list[Path] = []
    monkeypatch.setattr(verifier, "_remove_detached_worktree", lambda path, _root: removed.append(path))
    commands: list[list[str]] = []
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or subprocess.CompletedProcess(command, 1),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PROOF
    assert len(commands) == 1
    assert "test_b5_attachment_artifact_durability.py" in commands[0][3]
    assert removed == [worktree]
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_lane_timeout_cleans_owned_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    worktree = tmp_path / "detached"
    worktree.mkdir()
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setenv("FLEET_ROOT_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setenv("FLEET_SUB_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: ("c" * 40, "dev-0.7"))
    monkeypatch.setattr(verifier, "_git", lambda *_args, **_kwargs: str(tmp_path))
    monkeypatch.setattr(verifier, "_create_detached_worktree", lambda *_args, **_kwargs: worktree)
    removed: list[Path] = []
    monkeypatch.setattr(verifier, "_remove_detached_worktree", lambda path, _root: removed.append(path))
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("pytest", 1)),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_INTERRUPTED
    assert removed == [worktree]
    assert json.loads(output.read_text(encoding="utf-8"))["failure"] == {
        "category": "interrupted",
        "phase": "lane",
    }


def test_main_invokes_pytest_once_and_accepts_valid_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    sha = "1" * 40
    worktree = tmp_path / "detached"
    worktree.mkdir()
    (worktree / "uv.lock").write_bytes(b"test lock")
    calls: list[tuple[list[str], Path, dict[str, str], int]] = []
    monkeypatch.setenv("FLEET_LIVE", "1")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "secret-daytona")
    monkeypatch.setenv("FLEET_LLM_API_KEY", "secret-llm")
    monkeypatch.setenv("FLEET_ROOT_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setenv("FLEET_SUB_MODEL", verifier._LIVE_MODEL)
    monkeypatch.setattr(verifier, "_path_is_allowed", lambda _path: True)
    monkeypatch.setattr(verifier, "_candidate", lambda: (sha, "dev-0.7"))
    monkeypatch.setattr(verifier, "_git", lambda *_args, **_kwargs: str(tmp_path))
    monkeypatch.setattr(verifier, "_create_detached_worktree", lambda *_args, **_kwargs: worktree)
    removed: list[Path] = []
    monkeypatch.setattr(verifier, "_remove_detached_worktree", lambda path, _root: removed.append(path))
    monkeypatch.setattr(
        verifier,
        "_installed_versions",
        lambda *_args: {
            "python": "3.13.13",
            "dspy": "3.3.0b1",
            "daytona": "0.199.0",
        },
    )

    def run_once(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
        check: bool,
        stdout: int,
        stderr: int,
    ) -> subprocess.CompletedProcess[str]:
        del check, stdout, stderr
        calls.append((command, cwd, env, timeout))
        if "test_b5_attachment" in command[3]:
            evidence_path = worktree / ".scratch/clean-backend-refoundation/assets"
            evidence_path.mkdir(parents=True)
            (evidence_path / "live-b5-attachment-artifact-durability-evidence.json").write_text(
                json.dumps(
                    {
                        "gate": "B5",
                        "staged_readable": True,
                        "artifact_id": "artifact-1",
                        "artifact_checksum": "a" * 64,
                        "artifact_survived_replace": True,
                        "sandbox_ids": ["sandbox-1", "sandbox-2"],
                        "volume_id": "volume-1",
                    }
                ),
                encoding="utf-8",
            )
        else:
            output_path = Path(env[verifier.EVIDENCE_ENV])
            output_path.write_text(json.dumps(_success_receipt(sha)), encoding="utf-8")
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
                verifier._LIVE_MODEL,
                "--sub-model",
                verifier._LIVE_MODEL,
            ]
        )
        == 0
    )
    lane_calls = [call for call in calls if call[0][1:3] == ["run", "pytest"]]
    assert [call[0] for call in lane_calls] == [
        verifier.lane_command("attachment_artifact_durability", 840),
        verifier.pytest_command(840),
    ]
    assert all(call[1] == worktree for call in calls)
    command, _, child_env, timeout = lane_calls[1]
    assert command == verifier.pytest_command(840)
    assert child_env[verifier.EVIDENCE_ENV] == str(worktree / ".fleet-live-proof-receipt.json")
    assert child_env["FLEET_ROOT_MODEL"] == verifier._LIVE_MODEL
    assert child_env["FLEET_SUB_MODEL"] == verifier._LIVE_MODEL
    assert timeout == 900
    assert removed == [worktree]
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["candidate"]["sha"] == sha
    assert receipt["lanes"]["attachment_artifact_durability"]["order"] == 1
    assert receipt["lanes"]["fastapi_dspy_daytona_mvp"]["order"] == 2
    assert receipt["external_promotion"] == {
        "candidate_sha": sha,
        "ci": "pending",
        "human_approval": "pending",
    }
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
def test_success_receipt_allowlist_rejects_mutations(
    mutation: Any,
    expected_phase: str,
) -> None:
    sha = "3" * 40
    receipt = _success_receipt(sha)
    mutation(receipt)
    with pytest.raises(verifier.ReceiptError) as error:
        verifier._validate_success_receipt(receipt, sha=sha)
    assert error.value.phase == expected_phase


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
    monkeypatch.setattr(verifier, "_git", lambda *_args, **_kwargs: str(tmp_path))
    worktree = tmp_path / "detached"
    worktree.mkdir()
    monkeypatch.setattr(verifier, "_create_detached_worktree", lambda *_args, **_kwargs: worktree)
    monkeypatch.setattr(verifier, "_remove_detached_worktree", lambda *_args: None)
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_PROOF
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["failure"] == {"category": "proof_failed", "phase": "attachment_artifact_durability"}
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
    monkeypatch.setattr(verifier, "_git", lambda *_args, **_kwargs: str(tmp_path))
    worktree = tmp_path / "detached"
    worktree.mkdir()
    monkeypatch.setattr(verifier, "_create_detached_worktree", lambda *_args, **_kwargs: worktree)
    monkeypatch.setattr(verifier, "_remove_detached_worktree", lambda *_args: None)
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    assert verifier.main(["--output", str(output)]) == verifier.EXIT_RECEIPT
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["failure"] == {"category": "receipt_invalid", "phase": "durability_receipt"}
