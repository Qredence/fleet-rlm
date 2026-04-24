"""DSPy History persistence for session continuity.

Provides helpers to:
- Serialize/deserialize ``dspy.History`` to/from a JSON schema.
- Persist serialized history to a Daytona volume at the canonical path.
- Restore history from a Daytona volume on session resume.
- Upsert session metadata to the database via ``FleetRepository``.
- Export/import full session state for round-trip continuity.
"""

from __future__ import annotations

import json
import time
from pathlib import PurePosixPath
from typing import Any

import dspy

# JSON schema version — bump when the schema changes incompatibly.
HISTORY_SCHEMA_VERSION = "1"

# Required top-level keys in the persisted JSON payload.
REQUIRED_SCHEMA_KEYS = frozenset({"schema_version", "session_id", "timestamp", "turns"})


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def history_volume_path(
    meta_root: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> str:
    """Return the canonical Daytona volume path for a session history file.

    Args:
        meta_root: Absolute path to the ``meta`` root on the volume
            (e.g. ``/home/daytona/memory/meta``).
        workspace_id: Workspace identifier string.
        user_id: User identifier string.
        session_id: Session identifier string.

    Returns:
        Absolute path string for the session history JSON file.
    """
    return str(
        PurePosixPath(meta_root)
        / "workspaces"
        / workspace_id
        / "users"
        / user_id
        / f"react-session-{session_id}.json"
    )


# ---------------------------------------------------------------------------
# Serialization / deserialization
# ---------------------------------------------------------------------------


def serialize_history(
    history: dspy.History,
    session_id: str,
    *,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Serialize a ``dspy.History`` to a JSON-compatible dict.

    Schema (``schema_version`` = ``"1"``):

    .. code-block:: json

        {
          "schema_version": "1",
          "session_id": "<session_id>",
          "timestamp": 1234567890.123,
          "turns": [
            {"user_message": "...", "response": "..."}
          ]
        }

    Args:
        history: The conversation history to serialize.
        session_id: Session identifier to embed in the payload.
        timestamp: Unix timestamp.  Defaults to ``time.time()``.

    Returns:
        JSON-compatible dict ready for ``json.dumps``.
    """
    messages = list(getattr(history, "messages", []) or [])
    turns: list[dict[str, str]] = []
    for msg in messages:
        if isinstance(msg, dict):
            turns.append(
                {
                    "user_message": str(msg.get("user_message", "") or ""),
                    "response": str(msg.get("response", "") or ""),
                }
            )
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "session_id": session_id,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "turns": turns,
    }


def deserialize_history(data: dict[str, Any]) -> dspy.History:
    """Deserialize a JSON payload back to a ``dspy.History``.

    Args:
        data: Dict as produced by :func:`serialize_history`.

    Returns:
        A :class:`dspy.History` instance with the restored messages.
    """
    turns = data.get("turns", [])
    if not isinstance(turns, list):
        turns = []
    messages: list[dict[str, str]] = []
    for turn in turns:
        if isinstance(turn, dict):
            messages.append(
                {
                    "user_message": str(turn.get("user_message", "") or ""),
                    "response": str(turn.get("response", "") or ""),
                }
            )
    return dspy.History(messages=messages)


# ---------------------------------------------------------------------------
# Volume persistence
# ---------------------------------------------------------------------------


async def persist_history_to_volume(
    interpreter: Any,
    workspace_id: str,
    user_id: str,
    session_id: str,
    history: dspy.History,
) -> str:
    """Serialize and write history JSON to the Daytona volume.

    Attempts ``awrite_file`` first (async); falls back to ``write_file``
    (sync) if the interpreter does not expose the async variant.

    Args:
        interpreter: A Daytona interpreter instance.
        workspace_id: Workspace identifier.
        user_id: User identifier.
        session_id: Session identifier.
        history: Conversation history to persist.

    Returns:
        The absolute path that was written on the volume.
    """
    from fleet_rlm.runtime.execution.storage_paths import runtime_storage_roots

    roots = runtime_storage_roots(interpreter)
    path = history_volume_path(roots.meta_root, workspace_id, user_id, session_id)
    payload = serialize_history(history, session_id)
    content = json.dumps(payload, ensure_ascii=False, indent=2)

    awrite = getattr(interpreter, "awrite_file", None)
    if callable(awrite):
        await awrite(path, content)
    else:
        write = getattr(interpreter, "write_file", None)
        if callable(write):
            write(path, content)
        else:
            raise RuntimeError(
                "Interpreter has no write method (awrite_file or write_file)"
            )

    return path


async def restore_history_from_volume(
    interpreter: Any,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> dspy.History | None:
    """Read and deserialize history JSON from the Daytona volume.

    Returns ``None`` when the file does not exist or is not parseable.

    Args:
        interpreter: A Daytona interpreter instance.
        workspace_id: Workspace identifier.
        user_id: User identifier.
        session_id: Session identifier.

    Returns:
        Restored :class:`dspy.History`, or ``None`` if not found.
    """
    from fleet_rlm.runtime.execution.storage_paths import runtime_storage_roots

    roots = runtime_storage_roots(interpreter)
    path = history_volume_path(roots.meta_root, workspace_id, user_id, session_id)

    content: str | None = None
    aread = getattr(interpreter, "aread_file", None)
    if callable(aread):
        try:
            content = await aread(path)
        except Exception:
            return None
    else:
        read = getattr(interpreter, "read_file", None)
        if callable(read):
            try:
                content = read(path)
            except Exception:
                return None

    if not content:
        return None

    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    return deserialize_history(data)


# ---------------------------------------------------------------------------
# DB session metadata persistence
# ---------------------------------------------------------------------------


async def persist_session_metadata(
    repository: Any,
    *,
    workspace_id: str,
    user_id: str | None,
    session_id: str,
    tenant_id: str,
    title: str = "Chat session",
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Upsert session metadata to the database via ``FleetRepository``.

    A no-op (returns ``None``) when ``repository`` is ``None`` or when
    any required UUID cannot be parsed.

    Args:
        repository: A :class:`~fleet_rlm.integrations.database.FleetRepository`
            instance, or ``None`` to skip persistence.
        workspace_id: Workspace UUID string.
        user_id: Optional user UUID string.
        session_id: Session UUID string.
        tenant_id: Tenant UUID string.
        title: Human-readable session title.
        metadata: Additional metadata key-value pairs.

    Returns:
        The upserted ``ChatSession`` row, or ``None`` if skipped.
    """
    if repository is None:
        return None

    import uuid as _uuid

    from fleet_rlm.integrations.database.types import ChatSessionUpsertRequest

    def _to_uuid(val: str | None) -> _uuid.UUID | None:
        if val is None:
            return None
        try:
            return _uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return None

    tid = _to_uuid(tenant_id)
    wid = _to_uuid(workspace_id)
    if tid is None or wid is None:
        return None

    request = ChatSessionUpsertRequest(
        tenant_id=tid,
        workspace_id=wid,
        user_id=_to_uuid(user_id),
        title=title,
        session_id=_to_uuid(session_id),
        metadata_json=dict(metadata or {}),
    )
    return await repository.upsert_chat_session(request)


# ---------------------------------------------------------------------------
# Session export / import (round-trip helpers)
# ---------------------------------------------------------------------------


def export_session(
    runtime: Any,
    session_id: str,
) -> dict[str, Any]:
    """Export the full session state from an ``AgentRuntime``.

    The returned dict can be serialized to JSON and later restored with
    :func:`import_session`.

    Args:
        runtime: An :class:`~fleet_rlm.runtime.agent.runtime.AgentRuntime`
            instance.
        session_id: Session identifier to embed in the payload.

    Returns:
        JSON-compatible dict with ``schema_version``, ``session_id``,
        ``timestamp``, ``turns``, and ``core_memory``.
    """
    history: dspy.History = getattr(runtime, "history", dspy.History(messages=[]))
    payload = serialize_history(history, session_id)
    core_memory = getattr(runtime, "core_memory", {})
    payload["core_memory"] = dict(core_memory) if isinstance(core_memory, dict) else {}
    return payload


def import_session(
    runtime: Any,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Restore session state into an ``AgentRuntime`` from an exported dict.

    Restores conversation history and merges core memory entries.

    Args:
        runtime: An :class:`~fleet_rlm.runtime.agent.runtime.AgentRuntime`
            instance to update in-place.
        data: Dict previously produced by :func:`export_session`.

    Returns:
        Summary dict with ``status``, ``session_id``, and ``history_turns``.
    """
    restored = deserialize_history(data)
    runtime.history = restored

    core_memory = data.get("core_memory")
    if isinstance(core_memory, dict):
        existing_cm = getattr(runtime, "core_memory", None)
        if isinstance(existing_cm, dict):
            existing_cm.update(core_memory)

    turns_count = len(list(getattr(restored, "messages", []) or []))
    return {
        "status": "ok",
        "session_id": str(data.get("session_id", "")),
        "history_turns": turns_count,
    }
