from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from fleet_rlm.cli import app


runner = CliRunner()


def test_cli_help_lists_daytona_rlm_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "daytona-rlm" in result.stdout
    assert "daytona-smoke" in result.stdout


def test_daytona_rlm_cli_invokes_runner(monkeypatch, tmp_path: Path):
    class _FakeArtifact:
        def to_dict(self):
            return {"kind": "markdown", "value": "done"}

    class _FakeResult:
        run_id = "run-123"
        result_path = str(tmp_path / "run-123.json")
        final_artifact = _FakeArtifact()

    called: dict[str, object] = {}

    def _fake_run_daytona_rlm_pilot(**kwargs):
        called.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.run_daytona_rlm_pilot",
        _fake_run_daytona_rlm_pilot,
    )

    result = runner.invoke(
        app,
        [
            "daytona-rlm",
            "--repo",
            "https://github.com/example/repo.git",
            "--task",
            "inspect repo",
            "--max-depth",
            "3",
            "--batch-concurrency",
            "7",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert called["repo"] == "https://github.com/example/repo.git"
    assert called["task"] == "inspect repo"
    assert called["output_dir"] == tmp_path
    assert called["budget"].max_depth == 3
    assert called["budget"].batch_concurrency == 7
    assert "run_id: run-123" in result.stdout


def test_daytona_smoke_cli_invokes_runner(monkeypatch):
    class _FakeSmokeResult:
        def to_dict(self):
            return {
                "sandbox_id": "sbx-123",
                "persisted_state_value": 5,
                "driver_started": True,
                "termination_phase": "completed",
                "error_category": None,
                "phase_timings_ms": {"config": 1, "cleanup": 1},
            }

        error_category = None

    called: dict[str, object] = {}

    def _fake_run_daytona_smoke(**kwargs):
        called.update(kwargs)
        return _FakeSmokeResult()

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.run_daytona_smoke",
        _fake_run_daytona_smoke,
    )
    result = runner.invoke(
        app,
        [
            "daytona-smoke",
            "--repo",
            "https://github.com/example/repo.git",
            "--ref",
            "main",
        ],
    )

    assert result.exit_code == 0
    assert called == {
        "repo": "https://github.com/example/repo.git",
        "ref": "main",
    }
    assert '"sandbox_id": "sbx-123"' in result.stdout
    assert '"termination_phase": "completed"' in result.stdout


def test_daytona_smoke_cli_surfaces_preflight_errors_cleanly(monkeypatch):
    class _FailedSmokeResult:
        error_category = "config_error"

        def to_dict(self):
            return {
                "termination_phase": "config",
                "error_category": "config_error",
                "error_message": "Missing DAYTONA_API_URL.",
            }

    def _fake_run_daytona_smoke(**kwargs):
        del kwargs
        return _FailedSmokeResult()

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.run_daytona_smoke",
        _fake_run_daytona_smoke,
    )

    result = runner.invoke(
        app,
        [
            "daytona-smoke",
            "--repo",
            "https://github.com/example/repo.git",
        ],
    )

    assert result.exit_code == 1
    assert '"error_category": "config_error"' in result.stderr
    assert '"error_message": "Missing DAYTONA_API_URL."' in result.stderr
