from __future__ import annotations

from fleet_rlm.skills.quarantine import (
    list_quarantined,
    quarantine_install,
    read_quarantined_scan,
    read_stored_scan,
    store_scan_result,
)
from fleet_rlm.skills.schemas import SkillScope, SkillSecurityScanResult


def _scan(skill_name: str = "alpha") -> SkillSecurityScanResult:
    return SkillSecurityScanResult(
        scan_id="scan-123",
        skill_name=skill_name,
        scope=SkillScope.USER,
        blocked=True,
        force_allowed=False,
        scanned_at="2026-01-01T00:00:00+00:00",
    )


def test_quarantine_install_round_trip(tmp_path) -> None:
    volume = tmp_path / "memory"
    scan = _scan()
    quarantine_id = quarantine_install(volume_mount_path=str(volume), scan=scan, payload={"url": "https://example.com"})
    assert quarantine_id == "scan-123"
    loaded = read_quarantined_scan(str(volume), "scan-123")
    assert loaded is not None
    assert loaded.skill_name == "alpha"
    assert list_quarantined(str(volume))


def test_store_and_read_scan_result(tmp_path) -> None:
    volume = tmp_path / "memory"
    scan = _scan(skill_name="beta")
    store_scan_result(str(volume), scan)
    loaded = read_stored_scan(str(volume), "scan-123")
    assert loaded is not None
    assert loaded.skill_name == "beta"
