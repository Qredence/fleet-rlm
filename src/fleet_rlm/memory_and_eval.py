"""Session VFS persistence, adaptive DSPy execution, and MLflow evaluation."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import logging
import os
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import dspy
import mlflow
import psycopg
from dspy.primitives.facade import SandboxLM
from dspy.signatures.signature import make_signature
from dspy.utils.exceptions import AdapterParseError
from mlflow.entities import Feedback, SpanStatusCode, SpanType
from mlflow.genai.scorers import Guidelines, RelevanceToQuery, RetrievalGroundedness, Safety, scorer
from psycopg.rows import dict_row

from fleet_rlm.dspy_support import FleetRLMChatAdapter, decision_evidence, make_choice_type
from fleet_rlm.dspy_support import without_mlflow_dspy_callback as _without_mlflow_dspy_callback
from fleet_rlm.interpreter import DaytonaInterpreter, DaytonaSandboxManager, validate_session_id
from fleet_rlm.observability import activity_text
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
_MAX_VFS_FILE_BYTES = 25_000_000
_MAX_VFS_SYNC_BYTES = 100_000_000
_MAX_VFS_FILES = 5_000
_MAX_TURN_RECORD_BYTES = 1_000_000
_SESSION_HISTORY_TURNS = 8
_SESSION_HISTORY_CHARS = 16_000
_MAX_STORED_TURNS = 200


def _safe_route_error(exc: Exception, route_lm: Any) -> str:
    """Keep useful route errors in logs without echoing the active credential."""
    detail = " ".join(str(exc).split())
    api_key = getattr(route_lm, "api_key", None)
    if isinstance(api_key, str) and api_key:
        detail = detail.replace(api_key, "[redacted]")
    return detail[:500] or "no exception message"


def _turn_is_over(exc: BaseException) -> bool:
    """Deadline, cancellation, and shared-budget exhaustion end the Turn; ordinary child failures do not."""
    return isinstance(exc, TimeoutError) or str(exc).startswith(("Turn cancelled", "Shared RLM budget exhausted"))


def _child_failure(exc: Exception) -> str:
    """Bounded description of a failed child agent, returned to its parent instead of raised."""
    return f"child agent failed ({type(exc).__name__}): {str(exc)[:500]}"


def _normalize_route(decision: Any) -> str:
    """Return one of the supported routes, falling back to the bounded path."""
    route = str(getattr(decision, "value", decision))
    return {"direct_cot": "direct_cot", "single_rlm": "flex_pipeline", "delegated_rlm": "deep_rlm"}.get(
        route, "deep_rlm"
    )


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_sessions (
        session_id TEXT PRIMARY KEY,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        memory_summary TEXT NOT NULL DEFAULT '',
        turns JSONB NOT NULL DEFAULT '[]'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_vfs_files (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        content BYTEA NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, rel_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_memories (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        memory_id TEXT NOT NULL,
        content TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'observation',
        sources JSONB NOT NULL DEFAULT '{}'::jsonb,
        reviewed BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, memory_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_turns (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        task_sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(session_id,turn_id),
        UNIQUE(session_id,idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_runtime_settings (
        session_id TEXT PRIMARY KEY REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        settings JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
)


def initialize_database(pg_dsn: str) -> None:
    """Create additive Lite persistence tables without a migration runner."""
    with psycopg.connect(pg_dsn) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


@dataclass(slots=True)
class VolumePostgresHarness:
    """Mirror per-Session workspace files and committed Turns to Postgres."""

    pg_dsn: str
    sandbox_mgr: DaytonaSandboxManager
    _hydrated: bool = field(default=False, init=False)

    def init_db(self) -> None:
        initialize_database(self.pg_dsn)

    def acquire_session_lease(self) -> psycopg.Connection:
        """Keep a PostgreSQL session lock until runtime close or process death."""
        connection = psycopg.connect(self.pg_dsn, autocommit=True)
        try:
            locked = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,17))", (self.sandbox_mgr.session_id,)
            ).fetchone()
            if not locked or not locked[0]:
                raise RuntimeError("Session is owned by another worker")
            return connection
        except BaseException:
            connection.close()
            raise

    def runtime_settings(self) -> dict[str, Any]:
        with psycopg.connect(self.pg_dsn) as connection:
            row = connection.execute(
                "SELECT settings FROM fleet_lite_runtime_settings WHERE session_id=%s", (self.sandbox_mgr.session_id,)
            ).fetchone()
        return row[0] if row else {}

    def save_runtime_settings(self, settings: dict[str, Any]) -> None:
        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "INSERT INTO fleet_lite_runtime_settings(session_id,settings) VALUES(%s,%s::jsonb) "
                "ON CONFLICT(session_id) DO UPDATE "
                "SET settings=fleet_lite_runtime_settings.settings || EXCLUDED.settings",
                (self.sandbox_mgr.session_id, json.dumps(settings)),
            )

    def ensure_session(self, session_id: str) -> None:
        validate_session_id(session_id)
        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )

    def list_sessions(self) -> list[dict[str, Any]]:
        with psycopg.Connection[dict[str, Any]].connect(self.pg_dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT session_id, updated_at, jsonb_array_length(turns) AS turn_count "
                "FROM fleet_lite_sessions ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

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
        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "INSERT INTO fleet_lite_memories(session_id,memory_id,content,kind,sources,reviewed) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT(session_id,memory_id) DO UPDATE "
                "SET content=EXCLUDED.content,kind=EXCLUDED.kind,sources=EXCLUDED.sources,"
                "reviewed=EXCLUDED.reviewed,updated_at=NOW()",
                (self.sandbox_mgr.session_id, memory_id, content, kind, json.dumps(sources), reviewed),
            )

    def memories(self, query: str = "", *, reviewed_only: bool = False) -> list[dict[str, Any]]:
        search = " OR ".join(query[:2000].split()[:32])
        with psycopg.Connection[dict[str, Any]].connect(self.pg_dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT memory_id,content,kind,sources,reviewed,updated_at FROM fleet_lite_memories "
                "WHERE session_id=%s AND (%s=FALSE OR reviewed=TRUE) "
                "AND (%s='' OR to_tsvector('english',content) @@ websearch_to_tsquery('english',%s)) "
                "ORDER BY ts_rank_cd(to_tsvector('english',content),websearch_to_tsquery('english',%s)) DESC, "
                "updated_at DESC LIMIT 20",
                (self.sandbox_mgr.session_id, reviewed_only, search, search, search),
            ).fetchall()
            hashes = {
                row["rel_path"]: row["sha256"]
                for row in connection.execute(
                    "SELECT rel_path,sha256 FROM fleet_lite_vfs_files WHERE session_id=%s",
                    (self.sandbox_mgr.session_id,),
                ).fetchall()
            }
        for row in rows:
            row["stale"] = any(hashes.get(path) != digest for path, digest in row["sources"].items())
            row["updated_at"] = row["updated_at"].isoformat()
        return rows

    def delete_memory(self, memory_id: str) -> None:
        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "DELETE FROM fleet_lite_memories WHERE session_id=%s AND memory_id=%s",
                (self.sandbox_mgr.session_id, validate_session_id(memory_id)),
            )

    def claim_turn(self, turn_id: str, key: str, task: str) -> dict[str, Any] | None:
        """Persist intent before side effects; an uncertain running turn is never replayed."""
        digest = hashlib.sha256(task.encode()).hexdigest()
        with psycopg.Connection[dict[str, Any]].connect(self.pg_dsn, row_factory=dict_row) as connection:
            inserted = connection.execute(
                "INSERT INTO fleet_lite_turns(session_id,turn_id,idempotency_key,task_sha256) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT(session_id,idempotency_key) DO NOTHING RETURNING turn_id",
                (self.sandbox_mgr.session_id, turn_id, key, digest),
            ).fetchone()
            if inserted:
                return None
            prior = connection.execute(
                "SELECT turn_id,task_sha256,status,result FROM fleet_lite_turns "
                "WHERE session_id=%s AND idempotency_key=%s",
                (self.sandbox_mgr.session_id, key),
            ).fetchone()
            if prior is None:
                raise RuntimeError("Turn intent disappeared during claim")
            if prior["task_sha256"] != digest:
                raise ValueError("Idempotency key already belongs to another task")
            return prior

    def finish_turn(self, turn_id: str, status: str, result: dict[str, Any]) -> None:
        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "UPDATE fleet_lite_turns SET status=%s,result=%s::jsonb,updated_at=NOW() "
                "WHERE session_id=%s AND turn_id=%s AND status <> 'completed'",
                (status, json.dumps(result), self.sandbox_mgr.session_id, turn_id),
            )

    def turn_status(self, turn_id: str) -> dict[str, Any] | None:
        with psycopg.Connection[dict[str, Any]].connect(self.pg_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT turn_id,status,result,updated_at FROM fleet_lite_turns WHERE session_id=%s AND turn_id=%s",
                (self.sandbox_mgr.session_id, validate_session_id(turn_id)),
            ).fetchone()
        if row:
            row["updated_at"] = row["updated_at"].isoformat()
        return row

    def hydrate_volume_from_postgres(self, session_id: str) -> tuple[str, str]:
        """Restore only the requested Session's files, summary, and bounded history."""
        session_id = validate_session_id(session_id)
        with psycopg.Connection[dict[str, Any]].connect(self.pg_dsn, row_factory=dict_row) as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            files = connection.execute(
                "SELECT rel_path, sha256, content FROM fleet_lite_vfs_files WHERE session_id = %s ORDER BY rel_path",
                (session_id,),
            ).fetchall()
            session = connection.execute(
                "SELECT memory_summary, turns FROM fleet_lite_sessions WHERE session_id = %s",
                (session_id,),
            ).fetchone()

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
        summary = str((session or {}).get("memory_summary") or "")
        turns = (session or {}).get("turns") or []
        history = self._render_history(turns)
        return summary, history

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
        artifacts: list[str] = []

        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            connection.execute(
                "SELECT session_id FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE", (session_id,)
            )
            for item in files:
                rel_path = str(item.get("rel", ""))
                if not self._valid_vfs_path(rel_path):
                    raise ValueError("Workspace scan returned an invalid relative path")
                content = base64.b64decode(item["b64"], validate=True)
                if len(content) > _MAX_VFS_FILE_BYTES or hashlib.sha256(content).hexdigest() != item.get("sha"):
                    raise ValueError("Workspace file hash or size validation failed")
                if rel_path.startswith("artifacts/"):
                    artifacts.append(rel_path)
                previous = connection.execute(
                    "SELECT sha256 FROM fleet_lite_vfs_files WHERE session_id = %s AND rel_path = %s",
                    (session_id, rel_path),
                ).fetchone()
                if previous and previous[0] == item["sha"]:
                    continue
                connection.execute(
                    """
                    INSERT INTO fleet_lite_vfs_files (session_id, rel_path, sha256, content, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (session_id, rel_path) DO UPDATE
                    SET sha256 = EXCLUDED.sha256, content = EXCLUDED.content, updated_at = NOW()
                    """,
                    (session_id, rel_path, item["sha"], content),
                )
            rel_paths = [str(item["rel"]) for item in files]
            connection.execute(
                "DELETE FROM fleet_lite_vfs_files WHERE session_id = %s AND NOT (rel_path = ANY(%s))",
                (session_id, rel_paths),
            )
            connection.execute(
                """
                UPDATE fleet_lite_sessions
                SET turns = (
                        SELECT COALESCE(jsonb_agg(entry ORDER BY ordinal ASC), '[]'::jsonb)
                        FROM (
                            SELECT entry, ordinal
                            FROM jsonb_array_elements(turns || %s::jsonb) WITH ORDINALITY AS history(entry, ordinal)
                            ORDER BY ordinal DESC
                            LIMIT %s
                        ) AS bounded_history
                    ),
                    memory_summary = CASE WHEN %s <> '' THEN
                        right(trim(both E'\\n' from (memory_summary || E'\\n' || %s)), 20000)
                        ELSE memory_summary END,
                    updated_at = NOW()
                WHERE session_id = %s
                """,
                (
                    record_json,
                    _MAX_STORED_TURNS,
                    new_memory_note[:10_000],
                    new_memory_note[:10_000],
                    session_id,
                ),
            )
            if turn_record.get("turn_id"):
                completion = {
                    "event": "turn_complete",
                    "answer": turn_record.get("answer", ""),
                    "route": turn_record.get("route", ""),
                    "learned_skill_note": turn_record.get("learned_skill_note", ""),
                    "artifacts": sorted(set(artifacts)),
                }
                if turn_record.get("trace_id"):
                    completion["trace_id"] = turn_record["trace_id"]
                connection.execute(
                    "UPDATE fleet_lite_turns SET status='completed',result=%s::jsonb,updated_at=NOW() "
                    "WHERE session_id=%s AND turn_id=%s",
                    (json.dumps(completion), session_id, turn_record["turn_id"]),
                )
        return sorted(set(artifacts))

    def persist_workspace_files(self, session_id: str) -> list[str]:
        """Mirror a Session's current files without recording a synthetic Turn."""
        session_id = validate_session_id(session_id)
        files = self._scan_volume_files()
        with psycopg.connect(self.pg_dsn) as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            connection.execute(
                "SELECT session_id FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE", (session_id,)
            )
            for item in files:
                rel_path = str(item.get("rel", ""))
                if not self._valid_vfs_path(rel_path):
                    raise ValueError("Workspace scan returned an invalid relative path")
                content = base64.b64decode(item["b64"], validate=True)
                if len(content) > _MAX_VFS_FILE_BYTES or hashlib.sha256(content).hexdigest() != item.get("sha"):
                    raise ValueError("Workspace file hash or size validation failed")
                connection.execute(
                    """
                    INSERT INTO fleet_lite_vfs_files (session_id, rel_path, sha256, content, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (session_id, rel_path) DO UPDATE
                    SET sha256 = EXCLUDED.sha256, content = EXCLUDED.content, updated_at = NOW()
                    WHERE fleet_lite_vfs_files.sha256 IS DISTINCT FROM EXCLUDED.sha256
                    """,
                    (session_id, rel_path, item["sha"], content),
                )
            rel_paths = [str(item["rel"]) for item in files]
            connection.execute(
                "DELETE FROM fleet_lite_vfs_files WHERE session_id = %s AND NOT (rel_path = ANY(%s))",
                (session_id, rel_paths),
            )
            connection.execute("UPDATE fleet_lite_sessions SET updated_at = NOW() WHERE session_id = %s", (session_id,))
        return [str(item["rel"]) for item in files]

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


TRIAGE_ROUTE = make_choice_type(
    (
        ("direct_cot", "Simple response requiring no code, files, or Skills"),
        ("single_rlm", "A task needing one bounded Python or Skill workflow"),
        ("delegated_rlm", "A multi-part task requiring an independent child investigation"),
    )
)
TRIAGE_CRITERIA = {
    "direct_cot": {
        "definition": "A self-contained answer needing no execution or workspace inspection",
        "examples": ["Greeting", "Explain a familiar concept"],
        "not": "Computed results, file analysis, Skills, or independent investigation",
    },
    "single_rlm": {
        "definition": "A programmable Flex workflow with known inputs and verifiable outputs",
        "examples": ["Compute a specified digit", "Summarize an attached table"],
        "not": "Open-ended exploration where the next step depends on discoveries",
    },
    "delegated_rlm": {
        "definition": "Primary RLM for iterative exploration or decomposition",
        "examples": ["Investigate conflicting evidence", "Explore unfamiliar files and test hypotheses"],
        "not": "A route that guarantees delegation; both execution routes can delegate",
    },
}
TurnTriageSignature = make_signature(
    {
        "task": (str, dspy.InputField(desc="User request to route.")),
        "workspace_manifest": (str, dspy.InputField(desc="Files available in the Session workspace.")),
        "skills_catalog": (str, dspy.InputField(desc="Skills available to this Session.")),
        "route": (
            TRIAGE_ROUTE,
            dspy.OutputField(
                desc=(
                    "Choose direct_cot for a simple response, single_rlm for one bounded Python or Skill workflow, "
                    "or delegated_rlm for a multi-part task needing independent investigation."
                )
            ),
        ),
    },
    "Choose the least expensive route that can satisfy the request.",
    signature_name="TurnTriageSignature",
)


class FleetTaskSignature(dspy.Signature):
    """Solve a task with Session-scoped workspace files and progressive Skills.

    Delegate independent subtasks together with spawn_agents (they run concurrently) instead of calling
    spawn_agent one at a time. Pass child inputs as `files/...` paths. Finish with keyword arguments only:
    SUBMIT(answer=..., learned_skill_note=...).

    Once the answer and one supporting check agree, SUBMIT on the next action. Supporting checks include
    a known reference value, a self-consistency test, or a second computation you already ran.
    Reuse computed variables instead of recomputing results. Run another method only if a check failed,
    results disagree, or the Task asks for verification. Copy figures and strings from sandbox output.
    """

    workspace_manifest: str = dspy.InputField(desc="Files available in this Session's /workspace")
    skills_catalog: str = dspy.InputField(desc="Stage-1 available Skills catalog")
    memory_summary: str = dspy.InputField(
        desc="Bounded learned notes for this Session; shared notes are unverified hints to confirm in the sandbox"
    )
    session_history: str = dspy.InputField(desc="Recent committed Turns from this Session")
    execution_route: str = dspy.InputField(desc="The triage-selected route: single_rlm or delegated_rlm")
    task: str = dspy.InputField(desc="User request")
    context: Any = dspy.InputField(desc="Dataset handle or bounded context; inspect data in Python, not by guessing")
    answer: str = dspy.OutputField(desc="Answer grounded in verified sandbox work")
    learned_skill_note: str = dspy.OutputField(desc="Short note about reusable learning or Skills created")


class AdaptiveFleetEngine(dspy.Module):
    """Optional Jev triage with a GEPA-optimizable Flex/RLM execution core."""

    def __init__(
        self,
        sandbox_mgr: DaytonaSandboxManager,
        root_lm: dspy.LM,
        sub_lm: dspy.LM,
        jev_lm: Any | None = None,
        compiled_path: str | None = None,
        execution_mode: str = "hybrid",
    ) -> None:
        super().__init__()
        self.sandbox_mgr = sandbox_mgr
        self.root_lm = root_lm
        self.sub_lm = sub_lm
        self.jev_lm = jev_lm
        self.compiled_path = compiled_path
        if execution_mode not in {"rlm", "flex", "hybrid"}:
            raise ValueError("Invalid execution mode")
        self.execution_mode = execution_mode
        self._metered_root = SandboxLM(root_lm, sandbox_mgr.consume_budget)
        self._metered_sub = SandboxLM(sub_lm, sandbox_mgr.consume_budget)
        self._compiled_digest = ""
        self._compiled_signature: tuple[int, int] | None = None
        self.triage = dspy.Predict(TurnTriageSignature)
        self.triage.set_criteria("route", TRIAGE_CRITERIA)
        self.direct_cot = dspy.ChainOfThought("memory_summary, session_history, task -> answer")

        def spawn_agent(
            subtask: str,
            input_paths: list[str] | None = None,
            allowed_tools: list[str] | None = None,
            max_iters: int = 8,
            max_llm_calls: int = 25,
        ) -> dict[str, Any]:
            """Run a child with selected files; return findings and digest-verified promoted artifacts.

            No inputs are copied unless listed. The only optional Fleet tool is
            spawn_agent, allowing bounded further delegation. max_llm_calls is
            DSPy's query/facade limit; the shared turn ledger also meters actions.
            """
            if (
                not subtask.strip()
                or len(subtask) > 20_000
                or type(max_iters) is not int
                or not 1 <= max_iters <= 8
                or type(max_llm_calls) is not int
                or not 1 <= max_llm_calls <= 25
            ):
                raise ValueError("Invalid child task or resource slice")
            names = ["spawn_agent"] if allowed_tools is None else allowed_tools
            if not isinstance(names, list) or any(name != "spawn_agent" for name in names):
                raise ValueError("Only the approved spawn_agent Fleet tool may be delegated")
            tools = {"spawn_agent": spawn_agent} if names else {}
            factory = self.sandbox_mgr.interpreter_factory(tools=tools, input_paths=input_paths or [])
            interpreters: list[DaytonaInterpreter] = []

            def create() -> DaytonaInterpreter:
                interpreter = factory()
                if not interpreters:
                    # The explicit task owns promotion: failure must not publish
                    # the child's unverified staging files into its parent.
                    interpreter.owns_manager = False
                interpreters.append(interpreter)
                return interpreter

            create.execution_instructions = getattr(factory, "execution_instructions", "")  # ty: ignore[unresolved-attribute]
            child = dspy.RLM(
                "subtask: str -> findings: str",
                tools=list(tools.values()),
                interpreter_factory=create,
                sub_lm=self._metered_sub,
                max_iters=max_iters,
                max_llm_calls=max_llm_calls,
            )
            try:
                with dspy.context(lm=self._metered_sub, disable_history=True, adapter=FleetRLMChatAdapter()):
                    result = child(subtask=subtask)
            except AdapterParseError as exc:
                # A child whose reply cannot be parsed must not abort the parent's Turn or promote its
                # unverified files; the parent sees the failure and can retry or use other children.
                return {"findings": "", "error": _child_failure(exc), "artifacts": []}
            else:
                return {
                    "findings": str(result.findings)[:20_000],
                    "artifacts": interpreters[0].manager.collect_child_artifacts() if interpreters else [],
                }
            finally:
                if interpreters:
                    interpreters[0].manager.shutdown()

        def spawn_agents(
            subtasks: list[str],
            input_paths: list[list[str]] | None = None,
            max_iters: int = 8,
            max_llm_calls: int = 25,
        ) -> list[dict[str, Any]]:
            """Run several independent children concurrently; return one result per subtask, in order.

            Prefer this over calling spawn_agent one after another when the subtasks do not depend on each
            other. input_paths, when given, holds one list of `files/` paths per subtask. Each result is
            spawn_agent's {findings, artifacts}, or {findings: "", error, artifacts: []} if that child failed.
            At most the active-child capacity run at once; the shared Turn budget and deadline still apply.
            """
            if (
                not isinstance(subtasks, list)
                or not 1 <= len(subtasks) <= 8
                or not all(isinstance(t, str) for t in subtasks)
            ):
                raise ValueError("subtasks must be a list of 1-8 strings")
            if input_paths is not None and (not isinstance(input_paths, list) or len(input_paths) != len(subtasks)):
                raise ValueError("input_paths must be omitted or hold one path list per subtask")

            def run_child(index: int) -> dict[str, Any]:
                try:
                    return spawn_agent(
                        subtask=subtasks[index],
                        input_paths=input_paths[index] if input_paths else None,
                        max_iters=max_iters,
                        max_llm_calls=max_llm_calls,
                    )
                except Exception as exc:
                    if _turn_is_over(exc):
                        raise
                    return {"findings": "", "error": _child_failure(exc), "artifacts": []}

            workers = min(len(subtasks), sandbox_mgr.root.max_active_children)

            def submit(pool: ThreadPoolExecutor, index: int) -> Future[dict[str, Any]]:
                # One context copy per child keeps the DSPy callbacks, active manager, and MLflow parent span.
                context = contextvars.copy_context()
                return pool.submit(lambda: context.run(run_child, index))

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [submit(pool, index) for index in range(len(subtasks))]
                try:
                    return [future.result() for future in futures]
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise

        def sub_rlm(subtask: str, context_hint: str = "") -> str:
            """Spawn an isolated recursive investigation; input files and Skills are copied, not parent memory."""
            child = dspy.RLM(
                signature="context_hint: str, subtask: str -> findings: str",
                tools=[sub_rlm],
                interpreter_factory=self.sandbox_mgr.interpreter_factory(tools={"sub_rlm": sub_rlm}),
                sub_lm=self._metered_sub,
                max_iters=8,
                max_llm_calls=25,
            )
            try:
                with dspy.context(lm=self._metered_sub, disable_history=True, adapter=FleetRLMChatAdapter()):
                    return str(child(context_hint=context_hint[:10000], subtask=subtask[:20000]).findings)[:20000]
            except AdapterParseError as exc:
                return f"ERROR: {_child_failure(exc)}"

        toolset = {"sub_rlm": sub_rlm, "spawn_agent": spawn_agent, "spawn_agents": spawn_agents}
        self.flex_agent = dspy.Flex(
            FleetTaskSignature,
            tools=[sub_rlm, spawn_agent, spawn_agents],
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
        )
        self.primary_rlm = dspy.RLM(
            FleetTaskSignature,
            tools=[sub_rlm, spawn_agent, spawn_agents],
            sub_lm=self._metered_sub,
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
            max_iters=16,
            max_llm_calls=sandbox_mgr.max_llm_calls,
        )
        if compiled_path and os.path.lexists(compiled_path):
            if os.path.islink(compiled_path) or not os.path.isfile(compiled_path):
                raise ValueError("Compiled Flex program path must be a regular file")
            if os.path.getsize(compiled_path) > 10_000_000:
                raise ValueError("Compiled Flex program exceeds the 10 MB size limit")
            with open(compiled_path, "rb") as handle:
                self._load_compiled_bytes(handle.read(), persist=False)

    def load_compiled_program(self, content: bytes) -> bool:
        """Load one bounded, session-owned GEPA artifact, skipping an unchanged copy."""
        if len(content) > 10_000_000:
            raise ValueError("Compiled Flex program exceeds the 10 MB size limit")
        digest = hashlib.sha256(content).hexdigest()
        if digest == self._compiled_digest:
            return False
        self._load_compiled_bytes(content, persist=True)
        return True

    def _load_compiled_bytes(self, content: bytes, *, persist: bool) -> None:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("Compiled Flex program must be a JSON object")
        digest = hashlib.sha256(content).hexdigest()
        target = os.path.abspath(self.compiled_path) if persist and self.compiled_path else None
        temporary_directory = os.path.dirname(target) if target else None
        if target is not None:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.islink(target):
                raise ValueError("Compiled Flex program path may not be a symbolic link")

        descriptor, candidate_path = tempfile.mkstemp(
            prefix=".fleet-flex-candidate-",
            suffix=".json",
            dir=temporary_directory,
        )
        os.close(descriptor)
        backup_descriptor, backup_path = tempfile.mkstemp(
            prefix=".fleet-flex-backup-",
            suffix=".json",
            dir=temporary_directory,
        )
        os.close(backup_descriptor)
        try:
            self.save(backup_path)
            with open(candidate_path, "wb") as handle:
                handle.write(content)
            try:
                self.load(candidate_path)
                candidate_stat = os.stat(candidate_path)
                if target:
                    os.replace(candidate_path, target)
            except BaseException:
                try:
                    self.load(backup_path)
                except Exception as restore_error:
                    raise RuntimeError("Could not restore the previous compiled Flex program") from restore_error
                raise
            if target:
                self._compiled_signature = (candidate_stat.st_mtime_ns, candidate_stat.st_size)
            elif not persist and self.compiled_path:
                if os.path.islink(self.compiled_path):
                    raise ValueError("Compiled Flex program path may not be a symbolic link")
                stat = os.stat(self.compiled_path)
                self._compiled_signature = (stat.st_mtime_ns, stat.st_size)
        finally:
            for temporary_path in (candidate_path, backup_path):
                with suppress(FileNotFoundError):
                    os.unlink(temporary_path)
        self._compiled_digest = digest

    def _reload_changed_local_program(self) -> None:
        if not self.compiled_path or not os.path.lexists(self.compiled_path):
            return
        if os.path.islink(self.compiled_path) or not os.path.isfile(self.compiled_path):
            raise ValueError("Compiled Flex program path must be a regular file")
        stat = os.stat(self.compiled_path)
        if stat.st_size > 10_000_000:
            raise ValueError("Compiled Flex program exceeds the 10 MB size limit")
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._compiled_signature:
            return
        with open(self.compiled_path, "rb") as handle:
            self._load_compiled_bytes(handle.read(), persist=False)

    def forward(
        self,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str = "<available_skills></available_skills>",
        session_history: str = "",
        context: Any = None,
        execution_mode: str | None = None,
    ) -> dspy.Prediction:
        owns_turn = not self.sandbox_mgr.root._deadline
        try:
            return self._run_turn(
                workspace_manifest, memory_summary, task, skills_catalog, session_history, context, execution_mode
            )
        finally:
            if owns_turn:
                self.sandbox_mgr.end_turn()

    def _run_turn(
        self,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str,
        session_history: str,
        context: Any,
        execution_mode: str | None,
    ) -> dspy.Prediction:
        self._reload_changed_local_program()
        if not self.sandbox_mgr.root._deadline:
            self.sandbox_mgr.reset_turn_budget()
        self.sandbox_mgr.check_turn()
        mode = execution_mode or self.execution_mode
        if mode not in {"rlm", "flex", "hybrid"}:
            raise ValueError("Invalid execution mode")
        if isinstance(context, WorkspaceDataset):
            raw = self.sandbox_mgr.read_workspace_file(context.workspace_path)
            if context.sha256 and hashlib.sha256(raw).hexdigest() != context.sha256:
                raise ValueError("Dataset source changed; select the new revision")
        route = "single_rlm"
        route_lm = self.jev_lm or self._metered_sub
        triage_inputs = {
            "task": task,
            "workspace_manifest": workspace_manifest,
            "skills_catalog": skills_catalog,
        }
        summary = "Hybrid router selected an execution route."
        evidence = decision_evidence(None, route_lm)
        reason = None
        try:
            if mode != "hybrid":
                reason = "session_mode"
                route = "deep_rlm" if mode == "rlm" else "flex_pipeline"
                summary = f"Session mode {mode} selects this route without hybrid triage."
            else:
                if self.jev_lm is not None:
                    self.sandbox_mgr.consume_budget()
                callbacks = (
                    _without_mlflow_dspy_callback(list(dspy.settings.callbacks))
                    if self.jev_lm is not None
                    else list(dspy.settings.callbacks)
                )
                # A generative backend has its own LM child span; do not count the wrapper twice.
                with mlflow.start_span(
                    name="typesafe_route_selection",
                    span_type=SpanType.LLM if self.jev_lm is not None else SpanType.CHAIN,
                ) as span:
                    span.set_inputs({name: activity_text(value) for name, value in triage_inputs.items()})
                    with dspy.context(lm=route_lm, callbacks=callbacks):
                        decision = self.triage(**triage_inputs).route
                    evidence = decision_evidence(decision, route_lm)
                    route = _normalize_route(decision)
                    span.set_outputs({"route": route, **evidence})
        except Exception as exc:
            if _turn_is_over(exc):
                raise
            _LOGGER.warning(
                "Optional route selection failed (%s: %s); continuing with bounded RLM execution",
                type(exc).__name__,
                _safe_route_error(exc, self.jev_lm or self.sub_lm),
            )
            route = "deep_rlm"
            reason = "routing_error"
            summary = f"Route selection failed ({type(exc).__name__}); falling back to bounded RLM execution."

        if context is not None and route == "direct_cot":
            reason = "selected_context"
            route = "deep_rlm"
            summary = "Context is selected; overriding the context-free direct route with bounded RLM execution."
        summary += {
            "direct_cot": " Answer directly without code execution.",
            "flex_pipeline": " Execute a DSPy Flex pipeline with Daytona Python and bounded delegation.",
            "deep_rlm": " Execute a bounded RLM with Daytona Python and optional child agents.",
        }.get(route, "")
        routing = {**evidence, "effective_route": route, "override_reason": reason}
        active_span = mlflow.get_current_active_span()
        if active_span is not None:
            active_span.set_attribute("fleet.routing", routing)
        self.sandbox_mgr.emit("triage_decision", mode=mode, route=route, summary=summary, evidence=routing)

        if route == "direct_cot":
            with dspy.context(lm=self._metered_root):
                answer = self.direct_cot(
                    memory_summary=memory_summary,
                    session_history=session_history,
                    task=task,
                ).answer
            return dspy.Prediction(answer=answer, learned_skill_note="", route=route)

        agent = (
            self.primary_rlm
            if mode == "rlm" or route in {"deep_rlm", "single_rlm", "delegated_rlm"}
            else self.flex_agent
        )
        with dspy.context(lm=self._metered_root, adapter=FleetRLMChatAdapter()):
            prediction = agent(
                workspace_manifest=workspace_manifest,
                skills_catalog=skills_catalog,
                memory_summary=memory_summary,
                session_history=session_history,
                execution_route=route,
                task=task,
                context=context if context is not None else "",
            )
        self.sandbox_mgr.check_turn()
        return dspy.Prediction(
            answer=prediction.answer,
            learned_skill_note=getattr(prediction, "learned_skill_note", ""),
            route=route,
        )


def configure_mlflow_observability(
    experiment_name: str = "fleet-rlm-production",
    tracking_uri: str = "sqlite:///mlflow.db",
    *,
    trace_catalog: str = "",
    trace_schema: str = "",
    trace_table_prefix: str = "",
    tracing_sql_warehouse_id: str = "",
) -> str | None:
    """Enable DSPy tracing in MLflow without making tracking a serving dependency."""
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    if tracking_uri.startswith("databricks"):
        from mlflow.entities.trace_location import UnityCatalog

        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = tracing_sql_warehouse_id
        if trace_table_prefix:
            trace_location = UnityCatalog(
                catalog_name=trace_catalog,
                schema_name=trace_schema,
                table_prefix=trace_table_prefix,
            )
        else:
            trace_location = UnityCatalog(catalog_name=trace_catalog, schema_name=trace_schema)
        mlflow.set_experiment(experiment_name, trace_location=trace_location)
    else:
        mlflow.set_experiment(experiment_name)
    mlflow.dspy.autolog(log_traces=True, log_traces_from_compile=False, log_traces_from_eval=False)
    experiment = mlflow.get_experiment_by_name(experiment_name)
    return experiment.experiment_id if experiment is not None else None


def _lm_call_count(spans: list[Any]) -> int:
    """Count model calls by MLflow span type, not by span-name substrings."""
    return sum(1 for span in spans if getattr(span, "span_type", None) == SpanType.LLM)


def _used_rlm_or_flex(spans: list[Any]) -> bool:
    return any((span.name or "").startswith(("RLM", "Flex")) for span in spans)


@scorer
def rlm_trajectory_efficiency(trace: Any) -> Feedback:
    """Penalize unnecessary LM calls and visible sandbox exceptions."""
    if trace is None:
        return Feedback(value=None, rationale="No trace captured; trajectory evidence is unavailable.")
    spans = trace.search_spans()
    lm_calls = _lm_call_count(spans)
    error_spans = []
    for span in spans:
        outputs = getattr(span, "outputs", None)
        attributes = getattr(span, "attributes", {}) or {}
        remote_error = span.name == "sandbox.exec" and (
            attributes.get("outcome") == "remote_code_error"
            or getattr(getattr(span, "status", None), "status_code", None) == SpanStatusCode.ERROR
            or (isinstance(outputs, dict) and bool(outputs.get("error")))
        )
        if remote_error or "Traceback" in str(outputs):
            error_spans.append(span)
    score = max(0.0, 1.0 - 0.015 * max(0, lm_calls - 3) - 0.15 * len(error_spans))
    return Feedback(
        value=round(score, 3),
        rationale=f"Observed {lm_calls} LM calls and {len(error_spans)} sandbox exceptions.",
    )


@scorer
def jev_routing_efficiency(trace: Any) -> Feedback:
    """Score simple requests routed without starting a Flex/RLM execution."""
    if trace is None:
        return Feedback(value=None, rationale="No trace available; routing evidence is unavailable.")
    spans = trace.search_spans()
    used_rlm = _used_rlm_or_flex(spans)
    lm_calls = _lm_call_count(spans)
    if not used_rlm and lm_calls <= 2:
        return Feedback(value=1.0, rationale=f"Fast path completed in {lm_calls} LM calls.")
    if used_rlm and lm_calls <= 15:
        return Feedback(value=0.95, rationale=f"Bounded RLM/Flex execution used {lm_calls} LM calls.")
    score = max(0.2, 1.0 - 0.02 * max(0, lm_calls - 15))
    return Feedback(value=round(score, 3), rationale=f"Heavy route used {lm_calls} LM calls.")


@scorer
def route_correctness(outputs: Any, expectations: dict[str, Any]) -> Feedback:
    """Check that the turn took the labeled gold route (direct_cot, flex_pipeline, or deep_rlm)."""
    expected = (expectations or {}).get("route")
    if not expected:
        return Feedback(value=None, rationale="No gold route labeled for this case.")
    actual = outputs.get("route") if isinstance(outputs, dict) else None
    if actual is None:
        return Feedback(value=None, rationale="Turn output did not report a route.")
    correct = str(actual) == str(expected)
    return Feedback(value=correct, rationale=f"Expected route {expected!r}, observed {actual!r}.")


def build_mlflow_scorer_suite(judge_model: str = "databricks:/databricks-claude-sonnet-4") -> list[Any]:
    """Construct the GenAI judge suite for explicit offline/online evaluation runs."""
    return [
        RelevanceToQuery(model=judge_model),
        RetrievalGroundedness(model=judge_model),
        Safety(model=judge_model),
        Guidelines(
            name="sandbox_verification_discipline",
            guidelines=(
                "For quantitative analysis, file inspection, or code logic, the answer must reflect results "
                "verified in the sandbox, not unverified mental arithmetic or assumed file contents."
            ),
            model=judge_model,
        ),
        Guidelines(
            name="agent_skills_progressive_disclosure",
            guidelines=(
                "Activate a matching Skill before using its workflow. Use Skill scripts when appropriate. "
                "Created Skills must use a valid lowercase hyphenated name and describe their trigger conditions."
            ),
            model=judge_model,
        ),
        rlm_trajectory_efficiency,
        jev_routing_efficiency,
        route_correctness,
    ]


def gepa_mlflow_metric(
    gold: dspy.Example,
    pred: dspy.Prediction,
    trace: Any = None,  # noqa: ARG001
    pred_name: str | None = None,  # noqa: ARG001
    pred_trace: Any = None,  # noqa: ARG001
    program_trace: Any = None,
    llm_call_penalty: float = 0.02,
    *,
    judge_lm: dspy.LM,
) -> dspy.Prediction:
    """Return correctness plus concise reflective guidance for GEPA."""
    expected = str(getattr(gold, "answer", ""))
    actual = str(getattr(pred, "answer", ""))
    call_count = len(program_trace) if isinstance(program_trace, (list, tuple)) else 0
    judge = dspy.ChainOfThought("task, expected_answer, candidate_answer -> is_correct: bool, critique: str")
    judge.set_lm(judge_lm)
    assessment = judge(
        task=str(getattr(gold, "task", "")),
        expected_answer=expected,
        candidate_answer=actual,
    )
    correct = bool(getattr(assessment, "is_correct", False))
    score = max(0.0, (1.0 if correct else 0.0) - llm_call_penalty * max(0, call_count - 1))
    critique = str(getattr(assessment, "critique", ""))[:1000]
    feedback = (
        f"Semantic correctness={correct}: {critique}. Execution used {call_count} predictor calls. "
        "Prefer deterministic Python and existing Skill scripts for arithmetic and parsing."
    )
    return dspy.Prediction(score=score, feedback=feedback)


def compile_and_evaluate_fleet(
    engine: AdaptiveFleetEngine,
    trainset: list[dspy.Example],
    valset: list[dspy.Example],
    reflection_lm: dspy.LM,
    save_path: str = "compiled_flex_fleet.json",
    *,
    budget: str = "medium",
    num_threads: int = 4,
    engine_factory: Any = None,
) -> dspy.Module:
    """Compile the Flex program and log the result as a deliberate MLflow run."""
    if budget not in {"light", "medium", "heavy"} or not 1 <= num_threads <= 32:
        raise ValueError("Invalid GEPA budget or thread count")
    if engine_factory is None:
        raise ValueError("GEPA requires an isolated per-example engine_factory; use the optimization runner")
    from functools import partial

    from dspy.teleprompt.gepa import InstructionProposer

    from fleet_rlm.optimization import IsolatedFleetProgram

    with mlflow.start_run(run_name="fleet-flex-gepa-compile"):
        optimizer = dspy.GEPA(
            metric=partial(gepa_mlflow_metric, judge_lm=reflection_lm),
            reflection_lm=reflection_lm,
            instruction_proposer=InstructionProposer(adapter=dspy.ChatAdapter(use_json_adapter_fallback=False)),
            auto=budget,
            num_threads=num_threads,
        )
        isolated = IsolatedFleetProgram(engine, engine_factory)
        optimized = optimizer.compile(isolated, trainset=trainset, valset=valset).program
        optimized.save(save_path)
        mlflow.log_artifact(save_path)
        return optimized
