"""State persistence handler for bridge frontends (Ink TUI stateful support)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import logging


logger = logging.getLogger(__name__)

# Default state directory - uses Modal volume if available, else local temp
# Modal volumes are mounted at /data/memory in the sandbox
DEFAULT_STATE_DIR = (
    Path("/data/memory/state")
    if Path("/data/memory").exists()
    else Path.home() / ".fleet" / "state"
)


def _get_state_dir() -> Path:
    """Get the state directory, creating it if needed."""
    state_dir = DEFAULT_STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _get_state_file(namespace: str, key: str) -> Path:
    """Get the path to a state file for a given namespace and key."""
    state_dir = _get_state_dir()
    # Sanitize namespace and key to prevent path traversal
    safe_namespace = "".join(c for c in namespace if c.isalnum() or c in "-_")
    safe_key = "".join(c for c in key if c.isalnum() or c in "-_.")

    namespace_dir = state_dir / safe_namespace
    namespace_dir.mkdir(parents=True, exist_ok=True)

    return namespace_dir / f"{safe_key}.json"


def get_state(params: dict[str, Any]) -> dict[str, Any]:
    """Retrieve state value by namespace and key.

    Args:
        params: Dictionary containing:
            - namespace: State namespace (e.g., "ink", "session")
            - key: State key within namespace
            - default: Default value if not found (optional)

    Returns:
        Dictionary with "value" and "found" keys
    """
    namespace = str(params.get("namespace", "default")).strip()
    key = str(params.get("key", "")).strip()

    if not namespace or not key:
        return {
            "value": params.get("default"),
            "found": False,
            "error": "namespace and key are required",
        }

    state_file = _get_state_file(namespace, key)

    if not state_file.exists():
        return {
            "value": params.get("default"),
            "found": False,
        }

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "value": data.get("value"),
            "found": True,
            "timestamp": data.get("timestamp"),
        }
    except (json.JSONDecodeError, IOError) as e:
        return {
            "value": params.get("default"),
            "found": False,
            "error": str(e),
        }


def set_state(params: dict[str, Any]) -> dict[str, Any]:
    """Store state value by namespace and key.

    Args:
        params: Dictionary containing:
            - namespace: State namespace (e.g., "ink", "session")
            - key: State key within namespace
            - value: Value to store (JSON serializable)
            - ttl: Time-to-live in seconds (optional, not implemented)

    Returns:
        Dictionary with "ok" status
    """
    import time

    namespace = str(params.get("namespace", "default")).strip()
    key = str(params.get("key", "")).strip()
    value = params.get("value")

    if not namespace or not key:
        return {
            "ok": False,
            "error": "namespace and key are required",
        }

    state_file = _get_state_file(namespace, key)

    try:
        data = {
            "value": value,
            "timestamp": time.time(),
            "namespace": namespace,
            "key": key,
        }
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "path": str(state_file),
        }
    except (TypeError, IOError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


def delete_state(params: dict[str, Any]) -> dict[str, Any]:
    """Delete state value by namespace and key.

    Args:
        params: Dictionary containing:
            - namespace: State namespace
            - key: State key within namespace

    Returns:
        Dictionary with "ok" status and "existed" flag
    """
    namespace = str(params.get("namespace", "default")).strip()
    key = str(params.get("key", "")).strip()

    if not namespace or not key:
        return {
            "ok": False,
            "error": "namespace and key are required",
        }

    state_file = _get_state_file(namespace, key)

    existed = state_file.exists()
    if existed:
        try:
            state_file.unlink()
        except IOError as e:
            return {
                "ok": False,
                "existed": True,
                "error": str(e),
            }

    return {
        "ok": True,
        "existed": existed,
    }


def list_state(params: dict[str, Any]) -> dict[str, Any]:
    """List all state keys in a namespace.

    Args:
        params: Dictionary containing:
            - namespace: State namespace (default: "default")

    Returns:
        Dictionary with "keys" list and "count"
    """
    namespace = str(params.get("namespace", "default")).strip()

    state_dir = _get_state_dir()
    safe_namespace = "".join(c for c in namespace if c.isalnum() or c in "-_")
    namespace_dir = state_dir / safe_namespace

    if not namespace_dir.exists():
        return {
            "keys": [],
            "count": 0,
        }

    keys = []
    for f in namespace_dir.glob("*.json"):
        key = f.stem
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            keys.append(
                {
                    "key": key,
                    "timestamp": data.get("timestamp"),
                }
            )
        except (json.JSONDecodeError, IOError):
            keys.append(
                {
                    "key": key,
                    "timestamp": None,
                }
            )

    # Sort by timestamp descending
    keys.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)

    return {
        "keys": keys,
        "count": len(keys),
    }


def clear_namespace(params: dict[str, Any]) -> dict[str, Any]:
    """Clear all state in a namespace.

    Args:
        params: Dictionary containing:
            - namespace: State namespace to clear

    Returns:
        Dictionary with "ok" status and "deleted_count"
    """

    namespace = str(params.get("namespace", "default")).strip()

    state_dir = _get_state_dir()
    safe_namespace = "".join(c for c in namespace if c.isalnum() or c in "-_")
    namespace_dir = state_dir / safe_namespace

    deleted_count = 0
    if namespace_dir.exists():
        for f in namespace_dir.glob("*.json"):
            try:
                f.unlink()
                deleted_count += 1
            except IOError as exc:
                logger.warning("Failed to delete state file '%s': %s", f, exc)
        # Try to remove empty directory
        try:
            namespace_dir.rmdir()
        except OSError:
            pass

    return {
        "ok": True,
        "deleted_count": deleted_count,
    }
