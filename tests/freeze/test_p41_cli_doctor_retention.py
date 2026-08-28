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

import signal
import subprocess
import typing

import pytest

import fleet_rlm.config.loader
from fleet_rlm.cli import supervisor
from fleet_rlm.cli.bind_safety import UnsafeBindError, require_safe_bind_host
from fleet_rlm.cli.main import _DOCTOR_ACTIONS, _fleet_parser, fleet_main
from fleet_rlm.daytona import diagnostics

_EXPECTED_STEP_NAMES = {"settings", "database", "provider", "rlm", "sandbox", "interpreter", "cleanup"}
_CANARY = "FAKE-CANARY-secret-0000"


@pytest.fixture(autouse=True)
def _select_runtime_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "daytona")
    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "daytona")


def test_doctor_result_contract_shape_is_retained() -> None:
    # Operator-visible contract: the closed failure-category vocabulary drives
    # the step names and every category keeps an actionable (still sanitized)
    # follow-up line in the CLI renderer.  Internal dataclass layout and the
    # dependency protocol's member names are not frozen.
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
    assert categories <= set(_DOCTOR_ACTIONS)


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

    monkeypatch.setattr(fleet_rlm.config.loader, "load_runtime_settings", boom)

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


def test_supervisor_stop_order_and_escalation_are_pinned(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Port release: both process groups stop on every path; SIGTERM then SIGKILL."""
    real_stop = supervisor._stop_process_group
    stops: list[object] = []
    monkeypatch.setattr(supervisor, "_stop_process_group", stops.append)
    monkeypatch.setattr(supervisor, "_wait_until_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: None)

    class _FakeProcess:
        def __init__(self, returncode: int | None) -> None:
            self._returncode = returncode
            self.pid = 4242

        def poll(self) -> int | None:
            return self._returncode

    launched: list[str] = []

    def _fake_popen(command: list[str], **_kwargs: object) -> _FakeProcess:
        launched.append(command[0])
        return _FakeProcess(None if len(launched) == 1 else 0)

    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)
    supervisor._run_backend_and_tui(
        root=tmp_path,
        workspace=tmp_path,
        pnpm="pnpm",
        api_url="http://127.0.0.1:1",
        backend_command=["fleet-backend"],
        backend_env={},
        log_path=tmp_path / "backend.log",
        latest_log_path=tmp_path / "latest.log",
        run_environment="daytona",
        tui_args=(),
    )
    assert [process.poll() for process in stops] == [0, None]
    assert stops[0] is not stops[1]

    signals: list[int] = []
    waits: list[float | None] = []

    class _DyingProcess:
        pid = 4242

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired(cmd="fleet-tui", timeout=timeout)
            return 0

    monkeypatch.setattr(supervisor.os, "killpg", lambda _pid, sig: signals.append(sig))
    real_stop(_DyingProcess())
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert waits == [5.0, None]
