"""Encoding and integrity checks for explicitly selected, bounded replay snapshots."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import re
from typing import Any

from fleet_rlm.workspace import _workspace_relative_path, validate_session_id

MAX_REPLAY_FILE_BYTES = 8_000_000
MAX_REPLAY_SNAPSHOT_BYTES = 16_000_000
MAX_REPLAY_JSON_BYTES = 16_000_000
REVIEW_STATUSES = ("approved", "benchmark", "synthetic", "rejected")


def validate_review_labels(labels: dict[str, Any], *, require_gold: bool = False) -> None:
    """Validate the resolved review before either persistence boundary writes it."""
    if labels.get("review_status") not in REVIEW_STATUSES:
        raise ValueError(f"review_status must be one of {REVIEW_STATUSES}")
    from fleet_rlm.optimize.evidence import validate_structured

    validate_structured(labels.get("structured_expectations", {}))
    if not isinstance(labels.get("source_group"), str) or not labels["source_group"].strip():
        raise ValueError("source_group must not be empty")
    answer = labels.get("expected_answer")
    if answer is not None and not isinstance(answer, str):
        raise ValueError("expected answer must be a string")
    values = labels.get("expected_values", [])
    if (
        not isinstance(values, list)
        or len(values) > 100
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError("expected values must be nonempty strings with a maximum of 100 entries")
    if type(labels.get("critical", False)) is not bool:
        raise ValueError("critical must be a boolean")
    if labels.get("critical") and not values:
        raise ValueError("critical examples require at least one deterministic expected value")
    if require_gold and labels["review_status"] == "approved" and not (answer or "").strip() and not values:
        raise ValueError("review labels require an expected answer or expected values")


def selected_snapshot(store: Any, session_id: str, turn_id: str, digest: str) -> dict[str, Any]:
    """Resolve an immutable replay reference without trusting trace previews."""
    validate_session_id(session_id)
    validate_session_id(turn_id)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("replay snapshot digest is invalid")
    stored = store.replay_snapshot(session_id, turn_id)
    if stored is None or stored[0] != digest:
        raise ValueError("Selected replay snapshot is missing or has changed")
    snapshot = decode_snapshot(*stored)
    if snapshot.get("session_id") != session_id or snapshot.get("turn_id") != turn_id:
        raise ValueError("Replay snapshot ownership does not match its reference")
    return snapshot


def encode_snapshot(snapshot: dict[str, Any]) -> tuple[str, bytes]:
    """Return a deterministic digest and compressed representation of a replay snapshot."""
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_REPLAY_JSON_BYTES:
        raise ValueError("Selected replay snapshot exceeds the expanded size limit")
    compressed = gzip.compress(raw, mtime=0)
    if len(compressed) > MAX_REPLAY_SNAPSHOT_BYTES:
        raise ValueError("Selected replay snapshot exceeds the storage limit")
    return hashlib.sha256(compressed).hexdigest(), compressed


def decode_snapshot(digest: str, compressed: bytes) -> dict[str, Any]:
    """Verify stored bytes, then validate the versioned replay file manifest."""
    if len(compressed) > MAX_REPLAY_SNAPSHOT_BYTES or hashlib.sha256(compressed).hexdigest() != digest:
        raise ValueError("Replay snapshot digest or size validation failed")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            raw = stream.read(MAX_REPLAY_JSON_BYTES + 1)
        if len(raw) > MAX_REPLAY_JSON_BYTES:
            raise ValueError("Replay snapshot expands beyond its size limit")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Replay snapshot is corrupt") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Replay snapshot version is unsupported")
    contexts = value.get("context_files")
    files = value.get("replay_files")
    if not isinstance(contexts, dict) or not isinstance(files, dict) or set(contexts) != set(files):
        raise ValueError("Replay snapshot does not contain every immutable workspace file")
    if len(files) > 5000:
        raise ValueError("Replay snapshot contains too many files")
    total = 0
    for path, encoded in files.items():
        if not isinstance(path, str) or not isinstance(encoded, str):
            raise ValueError("Replay snapshot file entry is invalid")
        _workspace_relative_path(path)
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("Replay snapshot file encoding is invalid") from exc
        total += len(content)
        if total > MAX_REPLAY_FILE_BYTES or hashlib.sha256(content).hexdigest() != contexts.get(path):
            raise ValueError("Replay snapshot file digest or size validation failed")
    expected_manifest = ", ".join(f"/workspace/{path}" for path in sorted(files)) or "/workspace/files (empty)"
    if value.get("workspace_manifest") != expected_manifest:
        raise ValueError("Replay snapshot manifest does not match its immutable files")
    skill_revision = hashlib.sha256(
        json.dumps(
            {path: digest for path, digest in sorted(contexts.items()) if path.startswith("skills/")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if value.get("skill_revision_sha256") != skill_revision:
        raise ValueError("Replay snapshot Skills revision digest is invalid")
    dataset_context = value.get("dataset_context")
    if dataset_context is not None:
        if not isinstance(dataset_context, dict):
            raise ValueError("Replay snapshot dataset context is invalid")
        path = dataset_context.get("workspace_path")
        if not isinstance(path, str) or contexts.get(path) != dataset_context.get("sha256"):
            raise ValueError("Replay snapshot dataset context does not match its immutable file")
    return value
