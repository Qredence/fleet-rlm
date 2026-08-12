"""Golden wire-protocol lock for the packaged Workspace Agent runtime.

Locks operation markers, error vocabulary, and host loading so Mission 10's
extract cannot drift provider payload shape or security fail-closed paths.
"""

from __future__ import annotations

from importlib.resources import files


def _base(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "volume_root": "/home/daytona/fleet",
        "root": "/home/daytona/fleet/sessions/s/workspace",
        "operation": "stat",
        "relative": "note.txt",
        "allow_missing": True,
        "max_bytes": 1024,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
    }
    payload.update(overrides)
    return payload


def test_runtime_source_is_packaged_and_loaded_via_importlib_resources() -> None:
    from fleet_rlm.daytona import workspace_agent as host

    packaged = files("fleet_rlm.daytona").joinpath("workspace_agent_runtime.py").read_text(encoding="utf-8")
    assert "def respond(payload):" in packaged
    assert "class UnsafePath(Exception):" in packaged
    assert host._workspace_agent_runtime_source() == packaged
    # Host must never treat the runtime module as callable agent behavior.
    assert not hasattr(host, "respond")


def test_build_embeds_operation_params_and_runtime_source() -> None:
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    code = build_workspace_agent_code(**_base(checksum=True, expected_sha256="abc"))
    assert code.startswith("volume_root = '/home/daytona/fleet'")
    assert "checksum = True" in code
    assert "expected_sha256 = 'abc'" in code
    assert "import base64, datetime, errno, fcntl, hashlib, json, os, re, stat, time" in code
    assert "def respond(payload):" in code


def test_runtime_retains_every_operation_branch() -> None:
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    code = build_workspace_agent_code(**_base())
    for marker in (
        "if operation == 'list':",
        "if operation == 'stat':",
        "if operation == 'tail_read':",
        "if operation in ('read', 'read_page'):",
        "if operation == 'append':",
        "if operation == 'memory_migrate':",
        "if operation == 'memory_append':",
        "if operation in ('memory_edit', 'memory_delete'):",
        "if operation == 'unlink':",
        "if operation == 'delete':",
        "if operation == 'patch':",
        "if operation == 'write':",
    ):
        assert marker in code, marker


def test_error_vocabulary_and_security_markers_are_stable() -> None:
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    code = build_workspace_agent_code(**_base())
    for marker in (
        "fail('not_found')",
        "fail('conflict'",
        "fail('is_directory')",
        "fail('not_directory')",
        "fail('unsafe')",
        "fail('unsupported_storage'",
        "fail('read_bound')",
        "fail('too_large')",
        "O_NOFOLLOW",
        "follow_symlinks=False",
        "non_atomic_overwrite",
        "checksum_mismatch",
        "not_empty",
        "ambiguous",
        "missing",
        "_UNSUPPORTED_REPLACE_ERRNOS",
        "_WORM_RECREATE_ERRNOS",
        "fcntl.flock",
        "os.replace(",
    ):
        assert marker in code, marker


def test_replace_errno_set_keeps_portable_numeric_literals() -> None:
    """Volume backends may surface ENOSYS/EOPNOTSUPP as bare 38/95."""
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    code = build_workspace_agent_code(**_base())
    assert "38," in code or "38\n" in code
    assert "95," in code or "95\n" in code
    # Keep them inside the replace unsupported set, not only as stray literals.
    start = code.index("_UNSUPPORTED_REPLACE_ERRNOS")
    block = code[start : start + 220]
    assert "38" in block and "95" in block
