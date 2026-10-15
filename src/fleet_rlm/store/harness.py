"""Mirror a Session's sandbox workspace and committed Turns to PostgreSQL through the Store."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import psycopg

from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.paths import validate_session_id
from fleet_rlm.store.db import Store

_MAX_VFS_FILE_BYTES = 25_000_000
_MAX_VFS_SYNC_BYTES = 100_000_000
_MAX_VFS_FILES = 5_000
_MAX_TURN_RECORD_BYTES = 1_000_000
_SESSION_HISTORY_TURNS = 8
_SESSION_HISTORY_CHARS = 16_000
_MAX_STORED_TURNS = 200


@dataclass(slots=True)
class VolumePostgresHarness:
    """Mirror per-Session workspace files and committed Turns to Postgres (SQL lives in the Store)."""

    store: Store
    sandbox_mgr: DaytonaSandboxManager
    _hydrated: bool = field(default=False, init=False)

    @property
    def session_id(self) -> str:
        return self.sandbox_mgr.session_id

    def acquire_session_lease(self) -> psycopg.Connection:
        """Keep a PostgreSQL session lock until runtime close or process death."""
        return self.store.acquire_session_lease(self.session_id)

    def runtime_settings(self) -> dict[str, Any]:
        return self.store.runtime_settings(self.session_id)

    def save_runtime_settings(self, settings: dict[str, Any]) -> None:
        self.store.save_runtime_settings(self.session_id, settings)

    def ensure_session(self, session_id: str) -> None:
        self.store.ensure_session(validate_session_id(session_id))

    def save_memory(
        self,
        memory_id: str,
        content: str,
        *,
        sources: dict[str, str] | None = None,
        kind: str = "observation",
        reviewed: bool = False,
    ) -> None:
        validate_session_id(memory_id)
        if (
            not content.strip()
            or len(content) > 10000
            or kind not in {"observation", "preference", "hypothesis", "procedure"}
        ):
            raise ValueError("Invalid memory content or kind")
        sources = sources or {}
        if len(sources) > 100 or any(not self._valid_vfs_path(p) for p in sources):
            raise ValueError("Invalid memory sources")
        self.store.save_memory(self.session_id, memory_id, content, kind, sources, reviewed)

    def memories(self, query: str = "", *, reviewed_only: bool = False) -> list[dict[str, Any]]:
        search = " OR ".join(query[:2000].split()[:32])
        return self.store.memories(self.session_id, search, reviewed_only=reviewed_only)

    def delete_memory(self, memory_id: str) -> None:
        self.store.delete_memory(self.session_id, validate_session_id(memory_id))

    def claim_turn(self, turn_id: str, key: str, task: str) -> dict[str, Any] | None:
        """Persist intent before side effects; an uncertain running turn is never replayed."""
        digest = hashlib.sha256(task.encode()).hexdigest()
        prior = self.store.claim_turn(self.session_id, turn_id, key, digest)
        if prior is not None and prior["task_sha256"] != digest:
            raise ValueError("Idempotency key already belongs to another task")
        return prior

    def finish_turn(self, turn_id: str, status: str, result: dict[str, Any]) -> None:
        self.store.finish_turn(self.session_id, turn_id, status, result)

    def turn_status(self, turn_id: str) -> dict[str, Any] | None:
        return self.store.turn_status(self.session_id, validate_session_id(turn_id))

    def hydrate_volume_from_postgres(self, session_id: str) -> tuple[str, str]:
        """Restore only the requested Session's files, summary, and bounded history."""
        session_id = validate_session_id(session_id)
        files, summary, turns = self.store.load_session(session_id)
        restored_bytes = 0
        for row in [] if self._hydrated else files:
            content = bytes(row["content"])
            restored_bytes += len(content)
            if (
                not self._valid_vfs_path(row["rel_path"])
                or len(content) > _MAX_VFS_FILE_BYTES
                or restored_bytes > _MAX_VFS_SYNC_BYTES
                or hashlib.sha256(content).hexdigest() != row["sha256"]
            ):
                raise ValueError("Persisted workspace file hash validation failed")
            self.sandbox_mgr.write_workspace_file(row["rel_path"], content)
        self._hydrated = True
        return summary, self._render_history(turns)

    @staticmethod
    def _render_history(turns: list[dict[str, Any]]) -> str:
        if not isinstance(turns, list):
            return ""
        entries: list[str] = []
        total_chars = 0
        for turn in reversed(turns[-_SESSION_HISTORY_TURNS:]):
            if not isinstance(turn, dict):
                continue
            task = str(turn.get("task") or "")
            answer = str(turn.get("answer") or "")
            rendered = f"User: {task}\nAssistant: {answer}"
            separator_chars = 2 if entries else 0
            remaining = _SESSION_HISTORY_CHARS - total_chars - separator_chars
            header = "User: \nAssistant: "
            if remaining < len(header) + 2:
                break
            if len(rendered) > remaining:
                task_budget = min(len(task), 4_000, (remaining - len(header)) // 2)
                task_excerpt = task[:task_budget]
                if len(task) > task_budget:
                    task_excerpt += "…"
                answer_budget = remaining - len(header) - len(task_excerpt)
                if len(answer) > answer_budget:
                    answer_excerpt = "…" + answer[-(answer_budget - 1) :] if answer_budget > 1 else "…"
                else:
                    answer_excerpt = answer
                rendered = f"User: {task_excerpt}\nAssistant: {answer_excerpt}"
            entries.append(rendered)
            total_chars += len(entries[-1])
        return "\n\n".join(reversed(entries))

    def sync_volume_to_postgres(
        self,
        session_id: str,
        turn_record: dict[str, Any],
        new_memory_note: str = "",
    ) -> list[str]:
        """Hash and upsert changed Session files in the same transaction as its Turn."""
        session_id = validate_session_id(session_id)
        files = self._scan_volume_files()
        record_json = json.dumps(turn_record, ensure_ascii=False, separators=(",", ":"))
        if len(record_json.encode("utf-8")) > _MAX_TURN_RECORD_BYTES:
            raise ValueError("Turn record exceeds the persistence size limit")
        verified = self._verified_files(files)
        artifacts = sorted({rel_path for rel_path, _, _ in verified if rel_path.startswith("artifacts/")})
        completion = None
        if turn_record.get("turn_id"):
            result = {
                "event": "turn_complete",
                "answer": turn_record.get("answer", ""),
                "route": turn_record.get("route", ""),
                "learned_skill_note": turn_record.get("learned_skill_note", ""),
                "artifacts": artifacts,
            }
            if turn_record.get("trace_id"):
                result["trace_id"] = turn_record["trace_id"]
            completion = (turn_record["turn_id"], result)
        self.store.mirror_files(
            session_id,
            verified,
            turn_record_json=record_json,
            max_turns=_MAX_STORED_TURNS,
            memory_note=new_memory_note,
            completion=completion,
        )
        return artifacts

    def persist_workspace_files(self, session_id: str) -> list[str]:
        """Mirror a Session's current files without recording a synthetic Turn."""
        session_id = validate_session_id(session_id)
        files = self._verified_files(self._scan_volume_files())
        self.store.mirror_files(session_id, files)
        return [rel_path for rel_path, _, _ in files]

    def _verified_files(self, files: list[dict[str, Any]]) -> list[tuple[str, str, bytes]]:
        """Decode a workspace scan into (rel_path, sha256, content), re-checking every path, size and digest."""
        verified = []
        for item in files:
            rel_path = str(item.get("rel", ""))
            if not self._valid_vfs_path(rel_path):
                raise ValueError("Workspace scan returned an invalid relative path")
            content = base64.b64decode(item["b64"], validate=True)
            if len(content) > _MAX_VFS_FILE_BYTES or hashlib.sha256(content).hexdigest() != item.get("sha"):
                raise ValueError("Workspace file hash or size validation failed")
            verified.append((rel_path, str(item["sha"]), content))
        return verified

    def _scan_volume_files(self) -> list[dict[str, Any]]:
        """Read a bounded, symlink-free manifest from the owning Session Volume."""
        paths = self.sandbox_mgr.list_workspace_files()
        if len(paths) > _MAX_VFS_FILES:
            raise ValueError("Workspace contains too many files")
        files = []
        total = 0
        for path in paths:
            if not self._valid_vfs_path(path):
                raise ValueError("Invalid workspace scan path")
            raw = self.sandbox_mgr.read_workspace_file(path, max_bytes=_MAX_VFS_FILE_BYTES)
            total += len(raw)
            if total > _MAX_VFS_SYNC_BYTES:
                raise ValueError("Workspace exceeds sync quota")
            files.append({"rel": path, "sha": hashlib.sha256(raw).hexdigest(), "b64": base64.b64encode(raw).decode()})
        return files

    @staticmethod
    def _valid_vfs_path(rel_path: str) -> bool:
        parts = rel_path.split("/")
        return (
            bool(parts)
            and parts[0] in {"files", "memory", "skills", "artifacts", "buffers", "sessions"}
            and len(rel_path) <= 1024
            and all(part not in {"", ".", ".."} for part in parts)
            and "\\" not in rel_path
            and "\x00" not in rel_path
            and not any(ord(character) < 32 for character in rel_path)
        )
