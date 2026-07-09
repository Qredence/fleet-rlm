"""Quarantine storage for blocked remote skill installs."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fleet_rlm.skills.schemas import SkillSecurityScanResult

QUARANTINE_DIR_NAME = ".quarantine"
SCAN_FILENAME = "scan.json"
MANIFEST_FILENAME = "manifest.json"


def quarantine_root(volume_mount_path: str) -> Path:
    return Path(volume_mount_path) / "skills" / QUARANTINE_DIR_NAME


def quarantine_install(
    *,
    volume_mount_path: str,
    scan: SkillSecurityScanResult,
    payload: dict[str, object] | None = None,
) -> str:
    quarantine_id = scan.scan_id or uuid.uuid4().hex
    target = quarantine_root(volume_mount_path) / quarantine_id
    target.mkdir(parents=True, exist_ok=False)
    (target / SCAN_FILENAME).write_text(scan.model_dump_json(indent=2), encoding="utf-8")
    if payload is not None:
        (target / MANIFEST_FILENAME).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return quarantine_id


def read_quarantined_scan(volume_mount_path: str, scan_id: str) -> SkillSecurityScanResult | None:
    scan_path = quarantine_root(volume_mount_path) / scan_id / SCAN_FILENAME
    if not scan_path.is_file():
        return None
    return SkillSecurityScanResult.model_validate_json(scan_path.read_text(encoding="utf-8"))


def list_quarantined(volume_mount_path: str) -> list[SkillSecurityScanResult]:
    root = quarantine_root(volume_mount_path)
    if not root.is_dir():
        return []
    results: list[SkillSecurityScanResult] = []
    for entry in sorted(root.iterdir()):
        scan_path = entry / SCAN_FILENAME
        if not scan_path.is_file():
            continue
        results.append(SkillSecurityScanResult.model_validate_json(scan_path.read_text(encoding="utf-8")))
    return results


def store_scan_result(volume_mount_path: str, scan: SkillSecurityScanResult) -> str:
    """Persist a scan result for later review (install-staging / scan lookup)."""
    staging_root = Path(volume_mount_path) / "skills" / ".install-staging" / scan.scan_id
    staging_root.mkdir(parents=True, exist_ok=True)
    (staging_root / SCAN_FILENAME).write_text(scan.model_dump_json(indent=2), encoding="utf-8")
    return scan.scan_id


def read_stored_scan(volume_mount_path: str, scan_id: str) -> SkillSecurityScanResult | None:
    scan_path = Path(volume_mount_path) / "skills" / ".install-staging" / scan_id / SCAN_FILENAME
    if scan_path.is_file():
        return SkillSecurityScanResult.model_validate_json(scan_path.read_text(encoding="utf-8"))
    return read_quarantined_scan(volume_mount_path, scan_id)


__all__ = [
    "MANIFEST_FILENAME",
    "QUARANTINE_DIR_NAME",
    "SCAN_FILENAME",
    "list_quarantined",
    "quarantine_install",
    "read_quarantined_scan",
    "read_stored_scan",
    "store_scan_result",
]
