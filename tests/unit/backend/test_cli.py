from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from fleet_rlm.cli import supervisor
from fleet_rlm.cli.main import fleet_main, fleet_rlm_main


def test_fleet_doctor_daytona_prints_safe_steps_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fleet_rlm.daytona import diagnostics

    result = diagnostics.DaytonaDoctorResult(
        ok=True,
        steps=(
            diagnostics.DaytonaDoctorStep("settings", True, "Settings valid."),
            diagnostics.DaytonaDoctorStep("cleanup", True, "Sandbox deleted."),
        ),
    )

    async def run(_settings: object) -> diagnostics.DaytonaDoctorResult:
        return result

    monkeypatch.setattr(diagnostics, "run_daytona_doctor", run)

    fleet_main(["doctor", "daytona"])

    assert capsys.readouterr().out == "[ok] settings: Settings valid.\n[ok] cleanup: Sandbox deleted.\n"


def test_fleet_doctor_daytona_returns_nonzero_with_provider_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fleet_rlm.daytona import diagnostics

    result = diagnostics.DaytonaDoctorResult(
        ok=False,
        steps=(
            diagnostics.DaytonaDoctorStep(
                "provider",
                False,
                "Daytona authentication was rejected.",
                "auth",
            ),
        ),
        failure_category="auth",
    )

    async def run(_settings: object) -> diagnostics.DaytonaDoctorResult:
        return result

    monkeypatch.setattr(diagnostics, "run_daytona_doctor", run)

    with pytest.raises(SystemExit) as error:
        fleet_main(["doctor", "daytona"])

    assert error.value.code == 1
    assert capsys.readouterr().out == (
        "[failed] provider: Daytona authentication was rejected.\n"
        "action: verify FLEET_DAYTONA_API_KEY and Daytona account access.\n"
    )


def test_fleet_doctor_reports_invalid_environment_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "invalid")

    with pytest.raises(SystemExit) as error:
        fleet_main(["doctor", "daytona"])

    assert error.value.code == 1
    assert capsys.readouterr().out == (
        "[failed] settings: Required Fleet Daytona settings are missing or invalid.\n"
        "action: configure the required FLEET_DAYTONA_API_KEY and FLEET_DATABASE_URL settings.\n"
    )


def test_fleet_doctor_prints_cleanup_action_after_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fleet_rlm.daytona import diagnostics

    result = diagnostics.DaytonaDoctorResult(
        ok=False,
        steps=(
            diagnostics.DaytonaDoctorStep("interpreter", False, "Interpreter failed.", "interpreter"),
            diagnostics.DaytonaDoctorStep("cleanup", False, "Cleanup failed.", "cleanup"),
        ),
        failure_category="interpreter",
    )

    async def run(_settings: object) -> diagnostics.DaytonaDoctorResult:
        return result

    monkeypatch.setattr(diagnostics, "run_daytona_doctor", run)

    with pytest.raises(SystemExit):
        fleet_main(["doctor", "daytona"])

    output = capsys.readouterr().out
    assert "action: inspect the disposable Sandbox interpreter capability." in output
    assert "action: check Daytona for a labelled fleet-daytona-doctor Sandbox and delete it if present." in output


@pytest.mark.parametrize(
    ("command", "expected_environment"),
    [("cli", "daytona"), ("deno", "deno")],
)
def test_fleet_runtime_command_selects_environment_and_supervises_pi_tui(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    expected_environment: str,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "deno")
    monkeypatch.setattr(
        supervisor,
        "supervise",
        lambda **kwargs: calls.append(kwargs),
    )

    fleet_main(
        [
            command,
            "--host",
            "0.0.0.0",
            "--allow-non-loopback-bind",
            "--port",
            "8123",
            "--",
            "--session",
            "session-id",
        ]
    )

    assert os.environ["FLEET_RUN_ENVIRONMENT"] == "deno"
    assert calls == [
        {
            "host": "0.0.0.0",
            "port": 8123,
            "reload": False,
            "run_environment": expected_environment,
            "tui_args": ("--session", "session-id"),
        }
    ]


def test_fleet_web_preserves_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "deno")
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *_args, **_kwargs: None))

    fleet_main(["web"])

    assert os.environ["FLEET_RUN_ENVIRONMENT"] == "deno"


@pytest.mark.parametrize(
    ("entrypoint", "argv"),
    [(fleet_main, ["web"]), (fleet_rlm_main, ["serve-api"])],
)
def test_backend_only_commands_run_uvicorn_without_supervisor(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: object,
    argv: list[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda application, **_kwargs: calls.append(application)),
    )
    monkeypatch.setattr(supervisor, "supervise", lambda **_kwargs: pytest.fail("must remain backend-only"))

    entrypoint(argv)  # type: ignore[operator]

    assert calls == ["fleet_rlm.main:app"]


def test_supervision_failure_is_reported_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_kwargs: object) -> None:
        raise supervisor.SupervisorError("pnpm is required")

    monkeypatch.setattr(supervisor, "supervise", fail)

    with pytest.raises(SystemExit) as error:
        fleet_main(["cli"])

    assert error.value.code == 1
    assert capsys.readouterr().err == "fleet: error: pnpm is required\n"
