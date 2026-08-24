"""Shared P39c live-evidence helpers: observed-Sandbox ledger and lane receipts.

This module is the SINGLE owner of two pieces of p39c live-harness plumbing
(previously duplicated across the five lane files):

1. The observed-Sandbox ledger
   (``.fleet-evidence/receipts/p39c-observed-sandboxes.json``). Writes are
   read-modify-write MERGES and refuse to shrink lane coverage: any write
   whose ``lanes`` keys would drop an already-recorded lane raises
   ``LedgerCoverageError`` instead of writing. Every write is atomic
   (fsync'd temp file + rename, mirroring ``_p35d_evidence.write_receipt``).
2. The per-lane receipt file naming. The canonical default-name receipt is
   ALWAYS written under ``.fleet-evidence/receipts/``; when
   ``FLEET_LIVE_EVIDENCE_PATH`` is set that path only receives an ADDITIONAL
   env-stem copy -- never a replacement -- so the rigid aggregate zero-leak
   gate can always find the canonical receipts.

Archive rebuild: workers may move ``p39c-*`` receipts/ledger aside into
``.fleet-evidence/receipts-archive/p39c-<tag>/`` (move, never delete) when
re-certifying at a new HEAD. The next lane would otherwise recreate a
single-lane ledger. ``rebuild_ledger_from_archive`` restores missing ledger
lane keys (identity only) from the newest COMPLETE archived ledger, i.e. one
whose ``lanes`` mapping covers every expected lane name; the zero-leak
aggregate calls it as a pre-flight before gating. Archived receipts are never
moved back, archived files are never modified, and restoring a lane key does
NOT authenticate its archived receipt: the aggregate's same-SHA receipt gates
still decide whether certification is claimable.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPTS_DIR = _REPO_ROOT / ".fleet-evidence" / "receipts"
_ARCHIVE_DIR = _REPO_ROOT / ".fleet-evidence" / "receipts-archive"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
LEDGER_NAME = "p39c-observed-sandboxes.json"


class LedgerCoverageError(RuntimeError):
    """Raised when a ledger write would drop already-recorded lane coverage."""


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write ``payload`` as canonical JSON (temp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _id_list(value: Any) -> list[str]:
    """Normalize a stored id collection; anything malformed reads as empty."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return [str(item) for item in value if item]


def load_ledger(ledger_path: Path) -> dict[str, Any]:
    """Return the ledger payload; a missing or corrupt ledger reads as empty."""
    payload: dict[str, Any] = {}
    if not ledger_path.is_file():
        return payload
    try:
        loaded = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    if isinstance(loaded, dict):
        payload = loaded
    return payload


def _write_ledger_guarded(ledger_path: Path, payload: dict[str, Any]) -> None:
    """Atomically write the ledger unless it would shrink lane coverage.

    The guard compares the on-disk ledger's ``lanes`` keys against the new
    payload's: dropping an already-recorded lane (e.g. overwriting a full
    seven-lane ledger with a resurrected single-lane one) raises
    ``LedgerCoverageError`` and leaves the existing file untouched.
    """
    existing = load_ledger(ledger_path)
    existing_lanes = existing.get("lanes")
    new_lanes = payload.get("lanes")
    if isinstance(existing_lanes, dict) and isinstance(new_lanes, dict):
        dropped = sorted(set(existing_lanes) - set(new_lanes))
        if dropped:
            raise LedgerCoverageError(
                f"refusing to shrink p39c ledger lane coverage: dropping {dropped} recorded in {ledger_path}"
            )
    _atomic_write_json(ledger_path, payload)


def record_observed_sandbox_ids(
    name: str,
    sandbox_ids: Iterable[str],
    session_ids: Iterable[str] = (),
    *,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Union this lane's observed Sandbox/session ids into the shared ledger.

    The merge only ever ADDS lane keys and ids; the guarded write below
    additionally proves the merged payload cannot shrink existing lane
    coverage before it lands.
    """
    ledger = ledger_path if ledger_path is not None else (_RECEIPTS_DIR / LEDGER_NAME)
    payload = load_ledger(ledger)
    lanes = payload.get("lanes")
    lanes = dict(lanes) if isinstance(lanes, dict) else {}
    lanes[name] = sorted(set(_id_list(lanes.get(name))) | {str(item) for item in sandbox_ids if item})
    payload["lanes"] = lanes
    sessions = payload.get("sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    sessions[name] = sorted(set(_id_list(sessions.get(name))) | {str(item) for item in session_ids if item})
    payload["sessions"] = sessions
    _write_ledger_guarded(ledger, payload)
    return payload


def write_lane_receipt(
    default_name: str,
    env_stem_suffix: str,
    payload: dict[str, Any],
    *,
    receipts_dir: Path | None = None,
) -> list[Path]:
    """Write the canonical receipt AND, when configured, an env-stem copy.

    The canonical ``default_name`` receipt always lands under
    ``.fleet-evidence/receipts/`` (or ``receipts_dir`` when explicitly
    overridden by tests). When ``FLEET_LIVE_EVIDENCE_PATH`` is set, the file
    ``{base.stem}{env_stem_suffix}{base.suffix or '.json'}`` next to it
    receives an additional byte-identical copy -- never a replacement for the
    canonical name. Returns the written paths (canonical first).
    """
    canonical_dir = receipts_dir if receipts_dir is not None else _RECEIPTS_DIR
    paths = [canonical_dir / default_name]
    configured = os.environ.get(_EVIDENCE_ENV)
    if configured:
        base = Path(configured).expanduser().resolve()
        additional = base.with_name(f"{base.stem}{env_stem_suffix}{base.suffix or '.json'}")
        if additional != paths[0]:
            paths.append(additional)
    for path in paths:
        _atomic_write_json(path, payload)
    return paths


def _complete_archive_ledgers(
    archive_root: Path, expected_lane_names: Collection[str]
) -> list[tuple[float, str, Path, dict[str, Any]]]:
    """Candidate archived ledgers that cover every expected lane, newest first."""
    candidates: list[tuple[float, str, Path, dict[str, Any]]] = []
    if not archive_root.is_dir():
        return candidates
    expected = set(expected_lane_names)
    for directory in archive_root.iterdir():
        if not directory.is_dir() or not directory.name.startswith("p39c-"):
            continue
        archived_ledger = directory / LEDGER_NAME
        payload = load_ledger(archived_ledger)
        lanes = payload.get("lanes")
        if not isinstance(lanes, dict) or not expected.issubset(set(lanes)):
            continue
        candidates.append((directory.stat().st_mtime, directory.name, archived_ledger, payload))
    # Newest directory wins; the name tie-break keeps the choice deterministic.
    candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return candidates


def rebuild_ledger_from_archive(
    ledger_path: Path | None = None,
    *,
    expected_lane_names: Collection[str],
    archive_root: Path | None = None,
) -> list[str]:
    """Restore missing ledger lane keys from the newest complete archive.

    When the canonical ledger was archived away (or a lane key is otherwise
    absent), merge the missing ``lanes`` (and matching ``sessions``) id lists
    from the newest complete ``receipts-archive/p39c-*/`` ledger. Existing
    keys are never overwritten -- this restores ledger IDENTITY only:
    archived receipts are never moved back, and the aggregate's same-SHA
    receipt gates still apply to the restored lanes.

    Returns the sorted list of lane keys that were restored (empty when the
    ledger already covers every expected lane or no complete archive exists).
    """
    ledger = ledger_path if ledger_path is not None else (_RECEIPTS_DIR / LEDGER_NAME)
    root = archive_root if archive_root is not None else _ARCHIVE_DIR
    expected = set(expected_lane_names)
    payload = load_ledger(ledger)
    lanes = payload.get("lanes")
    lanes = dict(lanes) if isinstance(lanes, dict) else {}
    sessions = payload.get("sessions")
    sessions = dict(sessions) if isinstance(sessions, dict) else {}
    missing = sorted(expected - set(lanes))
    if not missing:
        return []
    for _mtime, _name, _path, archived in _complete_archive_ledgers(root, expected):
        archived_lanes = archived.get("lanes")
        archived_sessions = archived.get("sessions")
        if not isinstance(archived_lanes, dict):
            continue
        for lane in missing:
            ids = _id_list(archived_lanes.get(lane))
            if not ids:
                continue
            lanes[lane] = ids
            if isinstance(archived_sessions, dict) and lane not in sessions:
                session_ids = _id_list(archived_sessions.get(lane))
                if session_ids:
                    sessions[lane] = session_ids
        payload["lanes"] = lanes
        payload["sessions"] = sessions
        _write_ledger_guarded(ledger, payload)
        return missing
    return []
