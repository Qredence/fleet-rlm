#!/usr/bin/env python3
"""Validate the P53.2 live Session-runtime evidence manifest.

P53 is an evidence consumer.  The serial live runner creates the manifest only
after the real Daytona provider, Turn lifecycle, Session registry, and native
child lanes have completed.  This module keeps the P53.2 claims separate from
the older P35-D transport matrix and fails closed on stale or incomplete
receipts.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
CERTIFIED_DSPY = "3.3.1"
CERTIFIED_DAYTONA_SNAPSHOT = "fleet-rlm-python313-v5"
CERTIFIED_DAYTONA_TARGET = "us"
MANIFEST_SCHEMA = "fleet.p53-live-session-certification/v1"
DEFAULT_OUTPUT = REPO_ROOT / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json"

REQUIRED_ROTATIONS = (
    "timeout",
    "cancellation",
    "provider_failure",
    "claim_loss",
    "commit_failure",
    "fingerprint_change",
    "idle_eviction",
)

# Each child receipt is checked for the invariant it claims.  A generic
# ``passed`` flag is not enough to turn a nearby child test into P53 evidence.
REQUIRED_CHILD_SCHEMAS: dict[str, str] = {
    "child_single": "fleet.p39c-root-flow/v1",
    "child_batch": "fleet.p39c-batch/v1",
    "child_failure": "fleet.p39c-batch/v1",
    "child_timeout": "fleet.p39c-cancel-deadline/v1",
    "child_claim_loss": "fleet.p39c-claim-loss/v1",
    "child_provider_absence": "fleet.p39a-child-cleanup-ownership-live/v1",
    "child_volume_preservation": "fleet.p39c-volume-preservation/v1",
}

REQUIRED_CHILD_RECEIPT_FILENAMES: dict[str, str] = {
    # These are the only child receipt names that the P53 manifest may bind.
    # Runner-specific copies remain untrusted staging files.
    "child_single": "p39c-root-flow.json",
    "child_batch": "p39c-batch-success.json",
    "child_failure": "p39c-batch-failure.json",
    "child_timeout": "p39c-cancel-deadline-deadline.json",
    "child_claim_loss": "p39c-claim-loss.json",
    "child_provider_absence": "p39a-child-cleanup-ownership-live-fault.json",
    "child_volume_preservation": "p39c-volume-preservation.json",
}

REQUIRED_ROTATION_ASSERTIONS = (
    "all_required_rotations",
    "history_rehydrated_after_every_rotation",
    "failed_python_state_absent_after_tainted_rotation",
    "native_rlm_identity_rotated",
    "fingerprint_rotation_handoff_preserved_provider_root",
)

REQUIRED_CHILD_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "child_single": ("one_native_child", "sandbox_absent", "admission_restored"),
    "child_batch": ("two_distinct_sibling_scopes", "ordered_host_captured_answers", "all_receipts_clean"),
    "child_failure": ("no_history_advance", "one_sanitized_terminal_failure", "both_children_deleted_with_ownership"),
    "child_timeout": ("only_timeout_semantics", "queued_sibling_never_acquired", "provider_delete_confirmed_absent"),
    "child_claim_loss": (
        "no_post_revocation_provider_creates",
        "strict_close_delete_absence_receipts",
        "no_history_advance",
    ),
    "child_provider_absence": (
        "explicit_failed_cleanup_classification",
        "sandbox_absent_after_forced_confirmation_failure",
    ),
    "child_volume_preservation": (
        "volume_id_unchanged",
        "volume_readable_after_all_sandboxes_absent",
        "markers_unchanged_all_outcomes",
    ),
}

REQUIRED_MANIFEST_ASSERTIONS = {
    "resident_continuity": True,
    "all_required_rotations": True,
    "history_rehydrated_after_every_rotation": True,
    "failed_python_state_absent_after_tainted_rotation": True,
    "native_child_outcomes_complete": True,
}


class P53CertificationError(ValueError):
    """Raised when P53 evidence is absent, stale, or incomplete."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    return _sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _is_hex(value: object, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length:
        return False
    return all(char in "0123456789abcdef" for char in value)


_MAX_JSON_BYTES = 8 * 1024 * 1024


def _read_json_bytes(path: Path, description: str) -> tuple[dict[str, Any], bytes]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_JSON_BYTES:
            raise P53CertificationError(f"{description} is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(_MAX_JSON_BYTES + 1)
        if len(data) > _MAX_JSON_BYTES:
            raise P53CertificationError(f"{description} is too large")
        payload = json.loads(data)
    except P53CertificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise P53CertificationError(f"{description} is unreadable") from exc
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
    if not isinstance(payload, dict):
        raise P53CertificationError(f"{description} must be an object")
    return payload, data


def _read_json(path: Path, description: str) -> dict[str, Any]:
    return _read_json_bytes(path, description)[0]


def _candidate(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("candidate")
    if not isinstance(value, dict):
        raise P53CertificationError("P53 receipt has no candidate identity")
    return value


def _check_candidate(
    candidate: dict[str, Any],
    *,
    sha: str,
    lockfile_sha256: str,
    require_clean: bool = False,
    require_provider_identity: bool = False,
) -> None:
    if (
        candidate.get("sha") != sha
        or candidate.get("lockfile_sha256") != lockfile_sha256
        or candidate.get("dspy") != CERTIFIED_DSPY
        or (require_clean and candidate.get("tracked_tree_clean") is not True)
        or (
            require_provider_identity
            and (
                candidate.get("daytona_snapshot") != CERTIFIED_DAYTONA_SNAPSHOT
                or candidate.get("daytona_target") != CERTIFIED_DAYTONA_TARGET
            )
        )
    ):
        raise P53CertificationError("P53 receipt candidate identity is stale")


def _p53_receipts_root() -> Path:
    return (REPO_ROOT / ".fleet-evidence" / "receipts" / "p53").resolve()


def _safe_current_receipt_path(path: Path, *, expected_name: str) -> Path:
    lexical = Path(os.path.abspath(path.expanduser()))
    root = _p53_receipts_root()
    resolved = lexical.resolve()
    if lexical.parent != root or lexical.name != expected_name or resolved != lexical:
        raise P53CertificationError("P53 receipt path is not a current canonical lane receipt")
    if not lexical.is_file():
        raise P53CertificationError("P53 receipt path is not a regular canonical lane receipt")
    return resolved


def _safe_manifest_path(path: Path) -> Path:
    lexical = Path(os.path.abspath(path.expanduser()))
    expected = (REPO_ROOT / ".fleet-evidence" / "receipts" / "p53-live-session-certification.json").resolve()
    if lexical != expected or (lexical.exists() and (lexical.is_symlink() or not lexical.is_file())):
        raise P53CertificationError("P53 certification manifest path is not canonical")
    return expected


def validate_manifest_path(path: Path) -> Path:
    """Validate and return the canonical output path used by the live runner."""
    return _safe_manifest_path(path)


def _receipt_reference(path: Path, *, expected_name: str) -> dict[str, str]:
    safe = _safe_current_receipt_path(path, expected_name=expected_name)
    try:
        relative = safe.relative_to(REPO_ROOT)
        _payload, data = _read_json_bytes(safe, "P53 receipt")
    except P53CertificationError as exc:
        raise P53CertificationError(f"P53 receipt is unreadable: {path}") from exc
    return {"path": str(relative), "sha256": _sha256(data)}


def _strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_continuity(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("passed") is not True:
        raise P53CertificationError("P53 resident continuity evidence did not pass")
    required = (
        "same_rlm",
        "same_interpreter",
        "same_sandbox",
        "python_variable_continuity",
        "complete_history_continuity",
    )
    if value.get("turns") != 2 or any(value.get(key) is not True for key in required):
        raise P53CertificationError("P53 resident continuity evidence is incomplete")
    observed = value.get("history_message_count")
    after = value.get("history_after_count")
    if (
        not isinstance(observed, int)
        or isinstance(observed, bool)
        or not isinstance(after, int)
        or isinstance(after, bool)
    ):
        raise P53CertificationError("P53 resident continuity has no complete History evidence")
    if observed < 1 or after != observed + 1:
        raise P53CertificationError("P53 resident continuity has no complete History evidence")
    return {
        "passed": True,
        "turns": 2,
        **{key: True for key in required},
        "history_message_count": observed,
        "history_after_count": after,
    }


_EXPECTED_TERMINALS: dict[str, tuple[str | None, str | None, bool]] = {
    "timeout": ("RunTimedOut", None, False),
    "cancellation": ("RunCancelled", None, False),
    "provider_failure": (None, None, True),
    "claim_loss": ("RunFailed", "unavailable", False),
    "commit_failure": ("RunFailed", "commit_failed", False),
    "fingerprint_change": ("RunCompleted", None, False),
    "idle_eviction": ("RunCompleted", None, False),
}
_PROVIDER_STATES = frozenset(
    {"running", "stopped", "paused", "archived", "unrecoverable", "missing", "fencing", "quarantined", "deleted"}
)


def _validate_rotation(name: str, value: object) -> dict[str, Any]:
    if name not in _EXPECTED_TERMINALS:
        raise P53CertificationError(f"P53 rotation name is unknown: {name}")
    if not isinstance(value, dict) or value.get("passed") is not True or value.get("trigger") != name:
        raise P53CertificationError(f"P53 rotation did not pass: {name}")
    old_runtime = value.get("old_runtime")
    new_runtime = value.get("new_runtime")
    continuation = value.get("continuation")
    provider = value.get("provider")
    terminal = value.get("terminal")
    if not isinstance(terminal, dict):
        raise P53CertificationError(f"P53 rotation terminal evidence is missing: {name}")
    expected_type, expected_code, requires_error = _EXPECTED_TERMINALS[name]
    error_type = terminal.get("error_type")
    if (
        terminal.get("type") != expected_type
        or terminal.get("code") != expected_code
        or (requires_error and (not isinstance(error_type, str) or not error_type))
        or (not requires_error and error_type is not None)
    ):
        raise P53CertificationError(f"P53 rotation terminal semantics are invalid: {name}")
    if not isinstance(old_runtime, dict) or not isinstance(new_runtime, dict):
        raise P53CertificationError(f"P53 rotation runtime evidence is incomplete: {name}")
    old_generation = old_runtime.get("generation")
    new_generation = new_runtime.get("generation")
    old_rlm = old_runtime.get("rlm_id")
    new_rlm = new_runtime.get("rlm_id")
    old_interpreter = old_runtime.get("interpreter_id")
    new_interpreter = new_runtime.get("interpreter_id")
    if (
        old_runtime.get("closed") is not True
        or not _strict_int(old_generation)
        or old_generation < 1
        or not _strict_int(new_generation)
        or new_generation <= old_generation
        or not isinstance(old_rlm, str)
        or not old_rlm
        or not isinstance(new_rlm, str)
        or not new_rlm
        or old_rlm == new_rlm
        or not isinstance(old_interpreter, str)
        or not old_interpreter
        or not isinstance(new_interpreter, str)
        or not new_interpreter
    ):
        raise P53CertificationError(f"P53 rotation runtime invariants are incomplete: {name}")
    if name == "fingerprint_change":
        if new_interpreter != old_interpreter:
            raise P53CertificationError("P53 fingerprint rotation did not preserve its root interpreter")
    elif new_interpreter == old_interpreter:
        raise P53CertificationError(f"P53 rotation reused a failed interpreter: {name}")
    if not isinstance(continuation, dict):
        raise P53CertificationError(f"P53 rotation continuation is incomplete: {name}")
    history_before = continuation.get("history_before_count")
    history_after = continuation.get("history_after_count")
    history_observed = continuation.get("history_message_count")
    if (
        continuation.get("admission_restored") is not True
        or continuation.get("failed_python_markers_absent") is not True
        or not _strict_int(history_before)
        or history_before < 1
        or not _strict_int(history_after)
        or history_after != history_before + 1
        or history_observed != history_after
    ):
        raise P53CertificationError(f"P53 rotation History continuation is incomplete: {name}")
    if not isinstance(provider, dict):
        raise P53CertificationError(f"P53 rotation provider evidence is incomplete: {name}")
    before_sandbox = provider.get("before_sandbox_id")
    after_sandbox = provider.get("after_sandbox_id")
    before_state = provider.get("before_state")
    after_state = provider.get("after_state")
    if (
        not isinstance(before_sandbox, str)
        or not before_sandbox
        or not isinstance(after_sandbox, str)
        or not after_sandbox
        or not isinstance(before_state, str)
        or before_state.lower() != "running"
        or not isinstance(after_state, str)
        or after_state.lower() != "running"
    ):
        raise P53CertificationError(f"P53 rotation provider probe is incomplete: {name}")
    if name == "fingerprint_change" and before_sandbox != after_sandbox:
        raise P53CertificationError("P53 fingerprint rotation did not preserve its provider root")
    if name != "fingerprint_change" and before_sandbox == after_sandbox:
        raise P53CertificationError(f"P53 tainted rotation did not replace its provider Sandbox: {name}")
    handoff = value.get("handoff")
    if not isinstance(handoff, dict) or handoff.get("interpreter_preserved") is not (name == "fingerprint_change"):
        raise P53CertificationError(f"P53 rotation interpreter handoff is invalid: {name}")
    return {
        "passed": True,
        "trigger": name,
        "terminal": {"type": expected_type, "code": expected_code, "error_type": error_type},
        "old_runtime": {
            "generation": old_generation,
            "closed": True,
            "rlm_id": old_rlm,
            "interpreter_id": old_interpreter,
        },
        "new_runtime": {
            "generation": new_generation,
            "rlm_id": new_rlm,
            "interpreter_id": new_interpreter,
        },
        "provider": {
            "before_sandbox_id": before_sandbox,
            "after_sandbox_id": after_sandbox,
            "before_state": before_state.lower(),
            "after_state": after_state.lower(),
        },
        "continuation": {
            "history_before_count": history_before,
            "history_message_count": history_observed,
            "history_after_count": history_after,
            "failed_python_markers_absent": True,
            "admission_restored": True,
        },
        "handoff": {"interpreter_preserved": name == "fingerprint_change"},
    }


def _validate_child(
    name: str,
    value: object,
    *,
    sha: str,
    lockfile_sha256: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("passed") is not True:
        raise P53CertificationError(f"P53 child lane did not pass: {name}")
    _check_candidate(
        _candidate(value),
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        require_clean=True,
        require_provider_identity=True,
    )
    if value.get("schema") != REQUIRED_CHILD_SCHEMAS[name]:
        raise P53CertificationError(f"P53 child lane schema is invalid: {name}")
    child_run_id = value.get("run_id")
    if not _is_hex(child_run_id, 32) or (run_id is not None and child_run_id != run_id):
        raise P53CertificationError(f"P53 child lane invocation id is invalid: {name}")
    cleanup = value.get("cleanup")
    assertions = value.get("assertions")
    required = REQUIRED_CHILD_ASSERTIONS[name]
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("confirmed_absent") is not True
        or cleanup.get("admission_restored") is not True
        or not isinstance(assertions, dict)
        or any(assertions.get(key) is not True for key in required)
    ):
        raise P53CertificationError(f"P53 child lane invariants are incomplete: {name}")
    return {
        "passed": True,
        "schema": value.get("schema"),
        "assertions": {key: True for key in required},
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "run_id": child_run_id,
    }


def build_manifest(
    *,
    sha: str,
    lockfile_sha256: str,
    rotation_receipt_path: Path,
    rotation_receipt: dict[str, Any],
    child_receipts: dict[str, tuple[Path, dict[str, Any]]],
) -> dict[str, Any]:
    """Validate current receipts and build one content-addressed P53 manifest."""
    if not _is_hex(sha, 40) or not _is_hex(lockfile_sha256, 64):
        raise P53CertificationError("P53 candidate identity is invalid")
    if rotation_receipt.get("schema") != MANIFEST_SCHEMA or rotation_receipt.get("passed") is not True:
        raise P53CertificationError("P53 rotation receipt schema or status is invalid")
    rotations = rotation_receipt.get("rotations")
    if not isinstance(rotations, dict) or set(rotations) != set(REQUIRED_ROTATIONS):
        raise P53CertificationError("P53 rotation coverage is incomplete")
    if rotation_receipt.get("manifest_sha256") != _digest(rotation_receipt):
        raise P53CertificationError("P53 rotation receipt self-hash is invalid")
    _check_candidate(
        _candidate(rotation_receipt),
        sha=sha,
        lockfile_sha256=lockfile_sha256,
        require_clean=True,
        require_provider_identity=True,
    )
    rotation_assertions = rotation_receipt.get("assertions")
    if not isinstance(rotation_assertions, dict) or any(
        rotation_assertions.get(key) is not True for key in REQUIRED_ROTATION_ASSERTIONS
    ):
        raise P53CertificationError("P53 rotation assertions are incomplete")
    cleanup = rotation_receipt.get("cleanup")
    if cleanup != {"confirmed_absent": True, "admission_restored": True}:
        raise P53CertificationError("P53 rotation cleanup is not confirmed")
    run_id = rotation_receipt.get("run_id")
    if not _is_hex(run_id, 32):
        raise P53CertificationError("P53 rotation receipt invocation id is invalid")
    continuity = _validate_continuity(rotation_receipt.get("continuity"))
    normalized_rotations = {name: _validate_rotation(name, rotations[name]) for name in REQUIRED_ROTATIONS}
    if set(child_receipts) != set(REQUIRED_CHILD_ASSERTIONS):
        raise P53CertificationError("P53 native-child coverage is incomplete")
    normalized_children = {
        name: _validate_child(name, receipt, sha=sha, lockfile_sha256=lockfile_sha256, run_id=run_id)
        for name, (_path, receipt) in child_receipts.items()
    }
    safe_rotation_path = _safe_current_receipt_path(rotation_receipt_path, expected_name="rotations.json")
    current_rotation, rotation_bytes = _read_json_bytes(safe_rotation_path, "P53 rotation receipt")
    if current_rotation != rotation_receipt:
        raise P53CertificationError("P53 rotation receipt changed during manifest build")
    child_paths = {
        name: _safe_current_receipt_path(path, expected_name=REQUIRED_CHILD_RECEIPT_FILENAMES[name])
        for name, (path, _receipt) in child_receipts.items()
    }
    child_bytes: dict[str, bytes] = {}
    for name, path in child_paths.items():
        current_child, data = _read_json_bytes(path, f"P53 child receipt {name}")
        if current_child != child_receipts[name][1]:
            raise P53CertificationError(f"P53 child receipt changed during manifest build: {name}")
        child_bytes[name] = data
    if len(set(child_paths.values())) != len(child_paths):
        raise P53CertificationError("P53 child receipt references must be distinct")
    receipt_refs = {
        "rotation": {"path": str(safe_rotation_path.relative_to(REPO_ROOT)), "sha256": _sha256(rotation_bytes)},
        "children": {
            name: {"path": str(path.relative_to(REPO_ROOT)), "sha256": _sha256(child_bytes[name])}
            for name, path in child_paths.items()
        },
    }
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "candidate": {
            "sha": sha,
            "lockfile_sha256": lockfile_sha256,
            "dspy": CERTIFIED_DSPY,
            "daytona_snapshot": CERTIFIED_DAYTONA_SNAPSHOT,
            "daytona_target": CERTIFIED_DAYTONA_TARGET,
            "tracked_tree_clean": True,
        },
        "continuity": continuity,
        "rotations": normalized_rotations,
        "children": normalized_children,
        "receipts": receipt_refs,
        "cleanup": {"confirmed_absent": True, "admission_restored": True},
        "assertions": dict(REQUIRED_MANIFEST_ASSERTIONS),
        "passed": True,
    }
    manifest["manifest_sha256"] = _digest(manifest)
    return manifest


def verify_manifest(path: Path, *, expected_sha: str, expected_lockfile_sha256: str) -> dict[str, Any]:
    """Verify a sealed P53 manifest and every referenced current receipt."""
    try:
        installed_dspy = importlib.metadata.version("dspy")
        import dspy

        module_dspy = getattr(dspy, "__version__", None)
    except (importlib.metadata.PackageNotFoundError, ImportError) as exc:
        raise P53CertificationError("certified DSPy is not installed") from exc
    if installed_dspy != CERTIFIED_DSPY or module_dspy != CERTIFIED_DSPY:
        raise P53CertificationError("installed DSPy is not the certified 3.3.1 release")
    path = _safe_manifest_path(path)
    manifest = _read_json(path, "P53 certification manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("passed") is not True:
        raise P53CertificationError("P53 certification manifest schema or status is invalid")
    if manifest.get("manifest_sha256") != _digest(manifest):
        raise P53CertificationError("P53 certification manifest self-hash is invalid")
    _check_candidate(
        _candidate(manifest),
        sha=expected_sha,
        lockfile_sha256=expected_lockfile_sha256,
        require_clean=True,
        require_provider_identity=True,
    )
    manifest_assertions = manifest.get("assertions")
    if manifest_assertions != REQUIRED_MANIFEST_ASSERTIONS:
        raise P53CertificationError("P53 certification assertions are incomplete")
    if manifest.get("cleanup") != {"confirmed_absent": True, "admission_restored": True}:
        raise P53CertificationError("P53 certification cleanup is not confirmed")
    manifest_run_id = manifest.get("run_id")
    if not _is_hex(manifest_run_id, 32):
        raise P53CertificationError("P53 certification invocation id is invalid")
    _validate_continuity(manifest.get("continuity"))
    rotations = manifest.get("rotations")
    if not isinstance(rotations, dict) or set(rotations) != set(REQUIRED_ROTATIONS):
        raise P53CertificationError("P53 certification manifest omits required rotations")
    for name in REQUIRED_ROTATIONS:
        _validate_rotation(name, rotations[name])
    children = manifest.get("children")
    if not isinstance(children, dict) or set(children) != set(REQUIRED_CHILD_ASSERTIONS):
        raise P53CertificationError("P53 certification manifest omits required child lanes")
    for name in REQUIRED_CHILD_ASSERTIONS:
        value = children[name]
        if (
            not isinstance(value, dict)
            or value.get("passed") is not True
            or value.get("cleanup") != {"confirmed_absent": True, "admission_restored": True}
            or value.get("assertions") != {key: True for key in REQUIRED_CHILD_ASSERTIONS[name]}
        ):
            raise P53CertificationError(f"P53 certification child evidence is invalid: {name}")
    refs = manifest.get("receipts")
    if (
        not isinstance(refs, dict)
        or not isinstance(refs.get("rotation"), dict)
        or not isinstance(refs.get("children"), dict)
        or set(refs["children"]) != set(REQUIRED_CHILD_ASSERTIONS)
    ):
        raise P53CertificationError("P53 certification receipt references are missing")
    rotation_ref = refs["rotation"]
    if not isinstance(rotation_ref.get("path"), str) or not _is_hex(rotation_ref.get("sha256"), 64):
        raise P53CertificationError("P53 rotation receipt reference is invalid")
    rotation_path = _safe_current_receipt_path(REPO_ROOT / rotation_ref["path"], expected_name="rotations.json")
    rotation, rotation_bytes = _read_json_bytes(rotation_path, "P53 rotation receipt")
    if _sha256(rotation_bytes) != rotation_ref["sha256"]:
        raise P53CertificationError("P53 rotation receipt reference is stale")
    if (
        rotation.get("schema") != MANIFEST_SCHEMA
        or rotation.get("passed") is not True
        or rotation.get("manifest_sha256") != _digest(rotation)
        or rotation.get("cleanup") != {"confirmed_absent": True, "admission_restored": True}
        or not isinstance(rotation.get("assertions"), dict)
        or any(rotation["assertions"].get(key) is not True for key in REQUIRED_ROTATION_ASSERTIONS)
    ):
        raise P53CertificationError("P53 rotation receipt is no longer valid")
    _check_candidate(
        _candidate(rotation),
        sha=expected_sha,
        lockfile_sha256=expected_lockfile_sha256,
        require_clean=True,
        require_provider_identity=True,
    )
    rotation_run_id = rotation.get("run_id")
    if not _is_hex(rotation_run_id, 32) or rotation_run_id != manifest_run_id:
        raise P53CertificationError("P53 rotation invocation id does not match the manifest")
    raw_continuity = _validate_continuity(rotation.get("continuity"))
    raw_rotations = rotation.get("rotations")
    if not isinstance(raw_rotations, dict) or set(raw_rotations) != set(REQUIRED_ROTATIONS):
        raise P53CertificationError("P53 rotation receipt coverage is stale")
    normalized_raw_rotations = {name: _validate_rotation(name, raw_rotations[name]) for name in REQUIRED_ROTATIONS}
    if manifest.get("continuity") != raw_continuity or manifest.get("rotations") != normalized_raw_rotations:
        raise P53CertificationError("P53 rotation claims do not match the referenced receipt")
    normalized_raw_children: dict[str, dict[str, Any]] = {}
    for name, ref in refs["children"].items():
        if name not in REQUIRED_CHILD_ASSERTIONS or not isinstance(ref, dict):
            raise P53CertificationError(f"P53 child receipt reference is invalid: {name}")
        if not isinstance(ref.get("path"), str) or not _is_hex(ref.get("sha256"), 64):
            raise P53CertificationError(f"P53 child receipt reference is malformed: {name}")
        child_path = _safe_current_receipt_path(
            REPO_ROOT / ref["path"], expected_name=REQUIRED_CHILD_RECEIPT_FILENAMES[name]
        )
        child_value, child_bytes = _read_json_bytes(child_path, f"P53 child receipt {name}")
        if _sha256(child_bytes) != ref["sha256"]:
            raise P53CertificationError(f"P53 child receipt reference is stale: {name}")
        normalized_raw_children[name] = _validate_child(
            name,
            child_value,
            sha=expected_sha,
            lockfile_sha256=expected_lockfile_sha256,
            run_id=manifest_run_id,
        )
    if manifest.get("children") != normalized_raw_children:
        raise P53CertificationError("P53 child claims do not match the referenced receipts")
    return manifest


def _current_identity() -> tuple[str, str]:
    try:
        installed_dspy = importlib.metadata.version("dspy")
        sha = subprocess.run(
            ("git", "rev-parse", "HEAD"), cwd=REPO_ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        status = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        lockfile_sha256 = _sha256((REPO_ROOT / "uv.lock").read_bytes())
    except (OSError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as exc:
        raise P53CertificationError("could not determine P53 candidate identity") from exc
    unexpected = [line for line in status.splitlines() if line and not line.startswith("?? .factory/")]
    if unexpected:
        raise P53CertificationError("tracked worktree is not clean")
    if not _is_hex(sha, 40):
        raise P53CertificationError("candidate SHA is invalid")
    if installed_dspy != CERTIFIED_DSPY:
        raise P53CertificationError("installed DSPy is not the certified 3.3.1 release")
    return sha, lockfile_sha256


def verify_command(args: argparse.Namespace) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    if os.environ.get("FLEET_DAYTONA_SNAPSHOT") != CERTIFIED_DAYTONA_SNAPSHOT:
        raise P53CertificationError("P53 verification requires the authoritative Daytona v5 snapshot")
    if os.environ.get("DAYTONA_TARGET") != CERTIFIED_DAYTONA_TARGET:
        raise P53CertificationError("P53 verification requires unquoted DAYTONA_TARGET=us")
    sha, lockfile_sha256 = _current_identity()
    verify_manifest(args.manifest, expected_sha=sha, expected_lockfile_sha256=lockfile_sha256)
    print(f"P53 live Session certification verified: {args.manifest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT)
    verify.set_defaults(func=verify_command)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (P53CertificationError, OSError, subprocess.SubprocessError) as exc:
        print(f"P53 certification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
