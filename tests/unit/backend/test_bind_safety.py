"""Unit tests for the unauthenticated non-loopback bind safety gate."""

from __future__ import annotations

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
