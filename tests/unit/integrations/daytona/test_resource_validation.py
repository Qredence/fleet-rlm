"""M12: SandboxSpec resource validation against Daytona Cloud maxima."""

from __future__ import annotations

import pytest

from fleet_rlm.integrations.daytona.models import SandboxSpec


def _spec(**kwargs: object) -> SandboxSpec:
    return SandboxSpec(**kwargs)  # type: ignore[arg-type]


def test_cpu_within_limit_accepted() -> None:
    s = _spec(cpu=4)
    assert s.cpu == 4


def test_cpu_over_limit_rejected() -> None:
    with pytest.raises(ValueError, match="cpu=8 .*max 4"):
        _spec(cpu=8)


def test_memory_within_limit_accepted() -> None:
    s = _spec(memory=8)
    assert s.memory == 8


def test_memory_over_limit_rejected() -> None:
    with pytest.raises(ValueError, match="memory=16 GB .*max 8 GB"):
        _spec(memory=16)


def test_disk_within_limit_accepted() -> None:
    s = _spec(disk=10)
    assert s.disk == 10


def test_disk_over_limit_rejected() -> None:
    with pytest.raises(ValueError, match="disk=20 GB .*max 10 GB"):
        _spec(disk=20)


def test_none_resources_accepted() -> None:
    s = SandboxSpec()
    assert s.cpu is None
    assert s.memory is None
    assert s.disk is None


def test_multiple_violations_all_reported() -> None:
    with pytest.raises(ValueError) as exc_info:
        _spec(cpu=8, memory=16, disk=20)
    msg = str(exc_info.value)
    assert "cpu=8" in msg
    assert "memory=16 GB" in msg
    assert "disk=20 GB" in msg
