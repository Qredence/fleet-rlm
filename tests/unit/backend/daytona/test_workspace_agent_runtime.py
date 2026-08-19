"""Direct contract tests for the packaged Workspace Agent handler."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from fleet_rlm.daytona import workspace_agent_runtime as runtime


def _request(volume: Path, root: Path, operation: str, relative: str, **overrides: object) -> dict[str, object]:
    """
    Build a workspace agent request with standard defaults and optional overrides.

    Parameters:
        volume (Path): Workspace volume path.
        root (Path): Workspace root path.
        operation (str): Workspace operation to request.
        relative (str): Path relative to the workspace root.
        **overrides (object): Request fields that replace their default values.

    Returns:
        dict[str, object]: The assembled workspace agent request.
    """
    request: dict[str, object] = {
        "protocol_version": runtime.AGENT_PROTOCOL_VERSION,
        "volume_root": str(volume),
        "root": str(root),
        "operation": operation,
        "relative": relative,
        "allow_missing": True,
        "max_bytes": 4096,
        "limit": 100,
        "overwrite": False,
        "content_b64": "",
        "total_file_bytes": 4096,
    }
    request.update(overrides)
    return request


def test_metadata_and_validation_are_available_without_dispatch() -> None:
    metadata = runtime.get_metadata()
    artifact = Path(runtime.__file__).read_bytes()

    assert runtime.AGENT_METADATA["protocol_version"] == runtime.AGENT_PROTOCOL_VERSION
    assert metadata["source_checksum"] == hashlib.sha256(artifact).hexdigest()
    assert (
        runtime.handle({"protocol_version": runtime.AGENT_PROTOCOL_VERSION, "operation": "__handshake__"})[
            "source_checksum"
        ]
        == hashlib.sha256(artifact).hexdigest()
    )
    assert runtime.handle(None) == {"ok": False, "error": "request_invalid"}
    assert runtime.handle({"protocol_version": "wrong", "operation": "stat"}) == {
        "ok": False,
        "error": "protocol_mismatch",
    }
    assert runtime.handle(
        {
            "protocol_version": runtime.AGENT_PROTOCOL_VERSION,
            "operation": "unsupported",
            "volume_root": "/tmp",
            "root": "/tmp/root",
            "relative": "note.txt",
        }
    ) == {
        "ok": False,
        "error": "unsupported",
    }
    assert runtime.handle({"protocol_version": runtime.AGENT_PROTOCOL_VERSION, "operation": "stat"}) == {
        "ok": False,
        "error": "request_invalid",
    }


def test_direct_handler_dispatches_every_workspace_operation(tmp_path: Path) -> None:
    volume = tmp_path / "volume"
    root = volume / "workspace"
    root.mkdir(parents=True)
    legacy = b"- [2026-08-17T00:00:00Z] **General**: legacy\n"
    (volume / "MEMORIES.md").write_bytes(legacy)

    migrated_probe = runtime.handle(_request(volume, volume, "stat", "MEMORIES.md"))
    assert migrated_probe["ok"] is True
    migrated = runtime.handle(_request(volume, volume, "memory_migrate", "MEMORIES.md"))
    assert migrated["ok"] is True

    written = runtime.handle(
        _request(
            volume,
            root,
            "write",
            "note.txt",
            content_b64=base64.b64encode(b"hello world\n").decode("ascii"),
            overwrite=False,
        )
    )
    assert written["ok"] is True
    assert runtime.handle(_request(volume, root, "stat", "note.txt"))["ok"] is True
    assert runtime.handle(_request(volume, root, "read", "note.txt"))["content"] == "hello world\n"
    assert runtime.handle(_request(volume, root, "read_page", "note.txt", max_chars=5))["content"] == "hello"
    assert runtime.handle(_request(volume, root, "tail_read", "note.txt"))["content"] == "hello world\n"
    assert runtime.handle(_request(volume, root, "list", "."))["ok"] is True

    appended = runtime.handle(
        _request(
            volume,
            root,
            "append",
            "note.txt",
            content_b64=base64.b64encode(b"!").decode("ascii"),
        )
    )
    assert appended["ok"] is True

    patch_body = base64.b64encode(json.dumps({"old": "!", "new": "?"}).encode("utf-8")).decode("ascii")
    patched = runtime.handle(_request(volume, root, "patch", "note.txt", content_b64=patch_body))
    assert patched["ok"] is True

    memory_root = volume / "memory_ops"
    record = b"- [2026-08-19T00:00:00Z] **General**: direct\n"
    memory_append = runtime.handle(
        _request(
            volume,
            memory_root,
            "memory_append",
            "MEMORIES.md",
            content_b64=base64.b64encode(record).decode("ascii"),
        )
    )
    assert memory_append["ok"] is True
    memory_id = str(memory_append["memory_id"])
    memory_text = (memory_root / "MEMORIES.md").read_text(encoding="utf-8")
    assert "direct" in memory_text, (memory_id, memory_text)
    edit_body = base64.b64encode(
        b'{"learning": "direct edit", "category": null, "updated_at": "2026-08-19T00:00:01Z"}'
    ).decode("ascii")
    edited = runtime.handle(
        _request(volume, memory_root, "memory_edit", "MEMORIES.md", memory_id=memory_id, content_b64=edit_body)
    )
    assert edited["ok"] is True, edited
    deleted = runtime.handle(_request(volume, memory_root, "memory_delete", "MEMORIES.md", memory_id=memory_id))
    assert deleted["ok"] is True

    unlink_path = root / "unlink.txt"
    unlink_path.write_text("unlink", encoding="utf-8")
    assert runtime.handle(_request(volume, root, "unlink", "unlink.txt"))["ok"] is True
    delete_path = root / "delete.txt"
    delete_path.write_text("delete", encoding="utf-8")
    assert runtime.handle(_request(volume, root, "delete", "delete.txt"))["ok"] is True


def test_direct_handler_preserves_security_and_cas_fail_closed(tmp_path: Path) -> None:
    volume = tmp_path / "volume"
    root = volume / "workspace"
    outside = tmp_path / "outside.txt"
    root.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)

    symlink_result = runtime.handle(_request(volume, root, "read", "link.txt"))
    assert symlink_result == {"ok": False, "error": "unsafe"}
    traversal_result = runtime.handle(_request(volume, root, "read", "../outside.txt"))
    assert traversal_result == {"ok": False, "error": "request_invalid"}

    payload = base64.b64encode(b"cas").decode("ascii")
    created = runtime.handle(_request(volume, root, "write", "cas.txt", content_b64=payload))
    assert created["ok"] is True
    mismatch = runtime.handle(_request(volume, root, "delete", "cas.txt", expected_sha256="0" * 64))
    assert mismatch == {"ok": False, "error": "conflict", "detail": "checksum_mismatch"}
    assert (root / "cas.txt").exists()
