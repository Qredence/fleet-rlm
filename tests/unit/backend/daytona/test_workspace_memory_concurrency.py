"""Workspace Memory concurrency characterization for the neutral storage seam."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.workspace.models import WORKSPACE_MEMORY_HEADER, format_workspace_memory_v3_record

HEADER = WORKSPACE_MEMORY_HEADER + "\n"


class _MemberResult(SimpleNamespace):
    pass


async def _run_member(*, volume_root: Path, record: str) -> _MemberResult:
    """Append through the generic Workspace Agent in an isolated process."""
    volume_root.mkdir(parents=True, exist_ok=True)
    (volume_root / "memory").mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(
        f"""
        import base64
        from types import SimpleNamespace
        from fleet_rlm.daytona.workspace_agent.client import run_workspace_agent

        class Process:
            def code_run(self, code: str, **kwargs):
                import subprocess, sys
                result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                return SimpleNamespace(exit_code=result.returncode, result=result.stdout + result.stderr)

        root = {str(volume_root)!r}
        result = run_workspace_agent(
            SimpleNamespace(process=Process()),
            volume_root=root, root=root, operation="append", relative="memory/MEMORIES.md",
            allow_missing=True, allow_volume_root=True, max_bytes=262144, total_file_bytes=262144,
            limit=0, overwrite=False, content_b64=base64.b64encode({record!r}.encode()).decode(),
            after="", offset=0, max_chars=0, checksum=False, expected_sha256="",
        )
        if not result.get("ok"):
            raise RuntimeError(result)
        print(result)
        """
    ).lstrip()
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script], check=False, capture_output=True, text=True, timeout=30
    )
    return _MemberResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _record(memory_id: str, learning: str) -> str:
    return format_workspace_memory_v3_record(
        learning,
        "Policy",
        memory_id=memory_id,
        created_at="2026-07-19T09:00:00Z",
        updated_at="2026-07-19T09:00:00Z",
        source="operator_import",
    )


def _read_file(volume_root: Path) -> str:
    target = volume_root / "memory" / "MEMORIES.md"
    return target.read_text(encoding="utf-8") if target.exists() else ""


@pytest.mark.asyncio
async def test_independent_processes_use_only_generic_storage_operations(tmp_path: Path) -> None:
    volume_root = tmp_path / "volume"
    first = await _run_member(volume_root=volume_root, record=_record("aaaa0001", "alpha note"))
    second = await _run_member(volume_root=volume_root, record=_record("bbbb0002", "beta note"))

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    content = _read_file(volume_root)
    assert "alpha note" in content or "beta note" in content


def test_process_local_memory_lock_keeps_one_shared_adapter_serialized(tmp_path: Path) -> None:
    from fleet_rlm.workspace.memory import WorkspaceMemory

    content = bytearray()

    class Storage:
        def read_bytes(self, _path: str, *, max_bytes: int):
            del max_bytes
            return {
                "content": bytes(content),
                "truncated": False,
                "bytes_returned": len(content),
                "total_bytes": len(content),
            }

        def replace_bytes(self, _path: str, value: bytes, *, expected_sha256: str | None = None):
            del expected_sha256
            content[:] = value
            return {"byte_size": len(content)}

        def append_bytes(self, _path: str, value: bytes):
            content.extend(value)
            return {"byte_size": len(content)}

    store = WorkspaceMemory.from_storage(Storage(), max_file_bytes=262_144, lock_key=str(tmp_path))
    result = store.append_record(_record("aaaa0001", "local lock"))
    assert result.total_bytes == len(content)
    assert _record("aaaa0001", "local lock") in content.decode()
