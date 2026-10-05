"""Unit tests for the unauthenticated non-loopback bind safety gate."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from fleet_rlm.cli.bind_safety import UnsafeBindError, is_loopback_bind_host, require_safe_bind_host
from fleet_rlm.cli.main import fleet_main, fleet_rlm_main


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "LOCALHOST", "::1", " 127.0.0.1 "],
)
def test_loopback_hosts_are_allowed(host: str) -> None:
    assert is_loopback_bind_host(host)
    require_safe_bind_host(host, allow_non_loopback=False)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "10.0.0.1", "example.local", "hostname"],
)
def test_non_loopback_hosts_require_explicit_opt_in(host: str) -> None:
    assert not is_loopback_bind_host(host)
    with pytest.raises(UnsafeBindError, match="--allow-non-loopback-bind"):
        require_safe_bind_host(host, allow_non_loopback=False)
    require_safe_bind_host(host, allow_non_loopback=True)


@pytest.mark.parametrize(
    ("entrypoint", "argv"),
    [
        (fleet_main, ["web", "--host", "0.0.0.0"]),
        (fleet_rlm_main, ["serve-api", "--host", "0.0.0.0"]),
        (fleet_main, ["cli", "--host", "0.0.0.0"]),
    ],
)
def test_launchers_reject_non_loopback_without_opt_in(
    entrypoint: object,
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        entrypoint(argv)  # type: ignore[operator]

    assert error.value.code == 1
    assert "--allow-non-loopback-bind" in capsys.readouterr().err


def test_module_launcher_rejects_non_loopback_without_opt_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``python -m fleet_rlm.cli.server`` must enforce the same policy."""
    from fleet_rlm.cli import server

    with pytest.raises(SystemExit) as error:
        server.main(["--host", "0.0.0.0"])

    assert error.value.code == 1
    assert "--allow-non-loopback-bind" in capsys.readouterr().err


def test_module_launcher_accepts_the_explicit_non_loopback_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli import server

    calls: list[object] = []
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda target, **kwargs: calls.append((target, kwargs))),
    )

    server.main(["--host", "0.0.0.0", "--allow-non-loopback-bind", "--port", "8126"])

    assert calls == [("fleet_rlm.main:app", {"host": "0.0.0.0", "port": 8126, "reload": False})]


def test_shared_launcher_gate_rejects_non_loopback_direct_calls() -> None:
    """The gate lives in ``serve_api`` so no caller can bypass it."""
    from fleet_rlm.cli import server

    with pytest.raises(UnsafeBindError, match="--allow-non-loopback-bind"):
        server.serve_api(host="0.0.0.0", port=8126, reload=False)
