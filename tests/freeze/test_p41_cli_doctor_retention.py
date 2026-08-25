"""P41 deterministic retention for ``fleet doctor daytona`` and ``fleet cli``.

Covers the deterministic halves of VAL-CROSS-024 and VAL-CROSS-025: the
doctor keeps its step/category/action output shape, fails closed and
sanitized when settings are missing, and always passes through cleanup; the
CLI keeps loopback-only defaults, the --allow-non-loopback-bind guard,
sanitized supervisor error rendering, and the stop-both-process-groups port
release contract. The interactive (tuistory) and live Daytona halves run in
dedicated serial lanes and record receipts under ``.fleet-evidence/``.
"""

from __future__ import annotations

import inspect
import typing

import pytest

import fleet_rlm.config
from fleet_rlm.cli import supervisor
from fleet_rlm.cli.bind_safety import UnsafeBindError, require_safe_bind_host
from fleet_rlm.cli.main import _DOCTOR_ACTIONS, _fleet_parser, fleet_main
from fleet_rlm.daytona import diagnostics

_EXPECTED_STEP_NAMES = {"settings", "database", "provider", "rlm", "sandbox", "interpreter", "cleanup"}
_EXPECTED_PROTOCOL_METHODS = {
    "check_database",
    "resolve_volume",
    "check_rlm_readiness",
    "create_sandbox",
    "verify_mount",
    "execute",
    "delete_sandbox",
    "close",
}

_CANARY = "FAKE-CANARY-secret-0000"


@pytest.fixture(autouse=True)
def _select_runtime_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "daytona")
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "daytona")


def test_doctor_result_contract_shape_is_retained() -> None:
    step_fields = tuple(diagnostics.DaytonaDoctorStep.__dataclass_fields__)
    assert step_fields == ("name", "ok", "message", "category")
    result_fields = tuple(diagnostics.DaytonaDoctorResult.__dataclass_fields__)
    assert result_fields == ("ok", "steps", "failure_category")

    categories = set(typing.get_args(diagnostics.DoctorFailureCategory))
    assert categories == {
        "settings",
        "database",
        "auth",
        "quota",
        "network_timeout",
        "provider_5xx",
        "request_validation",
        "mount_mismatch",
        "rlm_provider",
        "interpreter",
        "cleanup",
        "unknown",
    }
    assert set(typing.get_args(diagnostics.DoctorStepName)) == _EXPECTED_STEP_NAMES
    # Every public failure category keeps an actionable (still sanitized)
    # follow-up line in the CLI renderer.
    assert categories <= set(_DOCTOR_ACTIONS)

    members = {name for name in vars(diagnostics.DaytonaDoctorDependencies) if not name.startswith("_")}
    assert members == _EXPECTED_PROTOCOL_METHODS


def test_doctor_failure_messages_are_static_and_sanitized() -> None:
    messages = diagnostics._FAILURE_MESSAGES
    assert messages, "doctor failure messages must stay a closed static table"
    for key, message in messages.items():
        assert "{" not in message and "}" not in message, f"message {key!r} interpolates raw data"
        lowered = message.lower()
        assert "traceback" not in lowered
        assert "exception" not in lowered


def test_doctor_missing_settings_fails_closed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom() -> object:
        raise RuntimeError(f"provider replied with bearer {_CANARY}")

    monkeypatch.setattr(fleet_rlm.config, "load_runtime_settings", boom)

    with pytest.raises(SystemExit) as error:
        fleet_main(["doctor", "daytona"])

    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "[failed] settings: Required Fleet Daytona settings are missing or invalid." in captured.out
    assert f"action: {_DOCTOR_ACTIONS['settings']}" in captured.out
    combined = captured.out + captured.err
    assert _CANARY not in combined
    assert "Traceback" not in combined


def test_doctor_output_shape_and_cleanup_step_are_pinned(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    steps = (
        diagnostics.DaytonaDoctorStep("settings", True, "Settings valid."),
        diagnostics.DaytonaDoctorStep("database", True, "Database at Alembic head."),
        diagnostics.DaytonaDoctorStep("sandbox", False, "Sandbox mount probe failed.", "mount_mismatch"),
        diagnostics.DaytonaDoctorStep("cleanup", True, "Sandbox deleted."),
    )
    result = diagnostics.DaytonaDoctorResult(ok=False, steps=steps, failure_category="mount_mismatch")

    async def run(_settings: object) -> diagnostics.DaytonaDoctorResult:
        return result

    monkeypatch.setattr(diagnostics, "run_daytona_doctor", run)

    with pytest.raises(SystemExit) as error:
        fleet_main(["doctor", "daytona"])

    assert error.value.code == 1
    output = capsys.readouterr().out
    lines = output.rstrip("\n").splitlines()
    assert lines[0].startswith("[ok] policy: profile=daytona")
    assert lines[1:-1] == [
        "[ok] settings: Settings valid.",
        "[ok] database: Database at Alembic head.",
        "[failed] sandbox: Sandbox mount probe failed.",
        "[ok] cleanup: Sandbox deleted.",
    ]
    assert lines[-1] == f"action: {_DOCTOR_ACTIONS['mount_mismatch']}"


def test_cli_parser_defaults_are_loopback_only_and_guarded() -> None:
    args = _fleet_parser().parse_args(["cli"])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.allow_non_loopback_bind is False
    assert args.run_environment == "daytona"
    assert args.supervise_tui is True

    with pytest.raises(UnsafeBindError) as error:
        require_safe_bind_host("0.0.0.0", allow_non_loopback=False)
    assert str(error.value) == (
        "refusing to bind unauthenticated Fleet API to non-loopback host '0.0.0.0'; "
        "pass --allow-non-loopback-bind to opt in deliberately"
    )

    with pytest.raises(SystemExit) as exited:
        fleet_main(["cli", "--host", "0.0.0.0", "--port", "8020"])
    assert exited.value.code == 1


def test_supervisor_errors_render_sanitized_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise supervisor.SupervisorError(f"refused by sanitized preflight ({_CANARY})")

    monkeypatch.setattr(supervisor, "supervise", fail)

    with pytest.raises(SystemExit) as error:
        fleet_main(["cli"])

    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("fleet: error: refused by sanitized preflight ")
    assert "Traceback" not in captured.err


def test_supervisor_stop_order_and_escalation_are_pinned() -> None:
    """Port release: both process groups stop on every path; SIGTERM then SIGKILL."""
    run_source = inspect.getsource(supervisor._run_backend_and_tui)
    tui_stop = run_source.index("_stop_process_group(tui)")
    backend_stop = run_source.index("_stop_process_group(backend)")
    assert tui_stop < backend_stop, "the backend group must stop after the TUI group"

    stop_source = inspect.getsource(supervisor._stop_process_group)
    assert "SIGTERM" in stop_source
    assert "SIGKILL" in stop_source
    assert stop_source.index("SIGTERM") < stop_source.index("SIGKILL")
