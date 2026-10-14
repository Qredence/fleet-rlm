"""Session VFS persistence, adaptive DSPy execution, and MLflow evaluation."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import dspy
import mlflow
import psycopg
from dspy.signatures.signature import make_signature
from mlflow.entities import Feedback, SpanType
from mlflow.genai.scorers import Guidelines, RelevanceToQuery, RetrievalGroundedness, Safety, scorer
from psycopg.rows import dict_row

from fleet_rlm.dspy_support import FleetRLMChatAdapter, make_choice_type
from fleet_rlm.interpreter import DaytonaSandboxManager, validate_session_id

_LOGGER = logging.getLogger(__name__)
_MAX_VFS_FILE_BYTES = 10_000_000
_MAX_VFS_SYNC_BYTES = 20_000_000
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


def _without_mlflow_dspy_callback(callbacks: list[Any]) -> list[Any]:
    """Keep MLflow's DSPy callback off the TypeSafe LM; trace that call manually."""
    return [
        callback
        for callback in callbacks
        if not (
            callback.__class__.__module__ == "mlflow.dspy.callback" and callback.__class__.__name__ == "MlflowCallback"
        )
    ]


def _normalize_route(decision: Any) -> str:
    """Return one of the supported routes, falling back to the bounded path."""
    route = str(getattr(decision, "value", decision))
    return route if route in {"direct_cot", "single_rlm", "delegated_rlm"} else "single_rlm"


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
)


def initialize_database(pg_dsn: str) -> None:
    """Create the two Lite persistence tables without a migration runner."""
    with psycopg.connect(pg_dsn) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


@dataclass(slots=True)
class VolumePostgresHarness:
    """Mirror per-Session workspace files and committed Turns to Postgres."""

    pg_dsn: str
    sandbox_mgr: DaytonaSandboxManager

    def init_db(self) -> None:
        initialize_database(self.pg_dsn)

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
        for row in files:
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
        scan_script = (
            "import base64,hashlib,json,os\n"
            "root='/workspace'\n"
            "out=[]\n"
            "total=0\n"
            "count=0\n"
            "for sub in ('files','memory','skills','artifacts'):\n"
            " for base,dirs,names in os.walk(os.path.join(root,sub),followlinks=False):\n"
            "  dirs[:]=[name for name in dirs if not os.path.islink(os.path.join(base,name))]\n"
            "  for name in names:\n"
            "   path=os.path.join(base,name)\n"
            "   if os.path.islink(path) or not os.path.isfile(path): continue\n"
            "   size=os.path.getsize(path)\n"
            f"   if size>{_MAX_VFS_FILE_BYTES}: raise ValueError('workspace file exceeds per-file size limit')\n"
            "   with open(path,'rb') as handle: raw=handle.read(size+1)\n"
            "   if len(raw)!=size: raise ValueError('workspace changed during sync')\n"
            "   total+=len(raw)\n"
            f"   if total>{_MAX_VFS_SYNC_BYTES}: raise ValueError('workspace sync exceeds size limit')\n"
            "   count+=1\n"
            f"   if count>{_MAX_VFS_FILES}: raise ValueError('workspace contains too many files to sync')\n"
            "   rel=os.path.relpath(path,root)\n"
            "   out.append({'rel':rel,'sha':hashlib.sha256(raw).hexdigest(),'b64':base64.b64encode(raw).decode()})\n"
            "print(json.dumps(out,separators=(',',':')))"
        )
        files = json.loads(self.sandbox_mgr.exec_remote_python(scan_script) or "[]")
        if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
            raise RuntimeError("Invalid workspace scan result")
        return files

    @staticmethod
    def _valid_vfs_path(rel_path: str) -> bool:
        parts = rel_path.split("/")
        return (
            bool(parts)
            and parts[0] in {"files", "memory", "skills", "artifacts"}
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
    """Solve a task with Session-scoped workspace files and progressive Skills."""

    workspace_manifest: str = dspy.InputField(desc="Files available in this Session's /workspace")
    skills_catalog: str = dspy.InputField(desc="Stage-1 available Skills catalog")
    memory_summary: str = dspy.InputField(desc="Bounded learned notes for this Session")
    session_history: str = dspy.InputField(desc="Recent committed Turns from this Session")
    execution_route: str = dspy.InputField(desc="The triage-selected route: single_rlm or delegated_rlm")
    task: str = dspy.InputField(desc="User request")
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
    ) -> None:
        super().__init__()
        self.sandbox_mgr = sandbox_mgr
        self.root_lm = root_lm
        self.sub_lm = sub_lm
        self.jev_lm = jev_lm
        self.compiled_path = compiled_path
        self._compiled_digest = ""
        self._compiled_signature: tuple[int, int] | None = None
        self.triage = dspy.Predict(TurnTriageSignature)
        self.direct_cot = dspy.ChainOfThought("memory_summary, session_history, task -> answer")

        def sub_rlm(subtask: str, context_hint: str = "") -> str:
            """Investigate one independent subtask with no recursive child tools."""
            child = dspy.RLM(
                signature="context_hint: str, subtask: str -> findings: str",
                interpreter_factory=self.sandbox_mgr.interpreter_factory(),
                sub_lm=self.sub_lm,
                max_iters=8,
                max_llm_calls=25,
            )
            with dspy.context(lm=self.sub_lm, disable_history=True, adapter=FleetRLMChatAdapter()):
                return child(context_hint=context_hint, subtask=subtask).findings

        toolset = {"sub_rlm": sub_rlm}
        self.flex_agent = dspy.Flex(
            FleetTaskSignature,
            tools=[sub_rlm],
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
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
    ) -> dspy.Prediction:
        self._reload_changed_local_program()
        self.sandbox_mgr.reset_turn_budget()
        route = "single_rlm"
        route_lm = self.jev_lm or self.sub_lm
        triage_inputs = {
            "task": task,
            "workspace_manifest": workspace_manifest,
            "skills_catalog": skills_catalog,
        }
        try:
            if self.jev_lm is None:
                with dspy.context(lm=route_lm):
                    decision = self.triage(**triage_inputs).route
                route = _normalize_route(decision)
            else:
                callbacks = _without_mlflow_dspy_callback(list(dspy.settings.callbacks))
                with mlflow.start_span(name="typesafe_route_selection", span_type=SpanType.LLM) as span:
                    span.set_inputs({"task": task})
                    with dspy.context(lm=route_lm, callbacks=callbacks):
                        decision = self.triage(**triage_inputs).route
                    route = _normalize_route(decision)
                    span.set_outputs({"route": route})
        except Exception as exc:
            _LOGGER.warning(
                "Optional route selection failed (%s: %s); continuing with bounded RLM execution",
                type(exc).__name__,
                _safe_route_error(exc, route_lm),
            )
            route = "single_rlm"

        if route == "direct_cot":
            with dspy.context(lm=self.root_lm):
                answer = self.direct_cot(
                    memory_summary=memory_summary,
                    session_history=session_history,
                    task=task,
                ).answer
            return dspy.Prediction(answer=answer, learned_skill_note="", route=route)

        with dspy.context(lm=self.root_lm, adapter=FleetRLMChatAdapter()):
            prediction = self.flex_agent(
                workspace_manifest=workspace_manifest,
                skills_catalog=skills_catalog,
                memory_summary=memory_summary,
                session_history=session_history,
                execution_route=route,
                task=task,
            )
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


@scorer
def rlm_trajectory_efficiency(trace: Any) -> Feedback:
    """Penalize unnecessary LM calls and visible sandbox exceptions."""
    if trace is None:
        return Feedback(value=1.0, rationale="No trace captured; no trajectory penalty applied.")
    spans = trace.search_spans()
    lm_spans = [span for span in spans if "LM" in (span.name or "") or "Predict" in (span.name or "")]
    error_spans = [span for span in spans if "Traceback" in str(getattr(span, "outputs", ""))]
    score = max(0.0, 1.0 - 0.015 * max(0, len(lm_spans) - 3) - 0.15 * len(error_spans))
    return Feedback(
        value=round(score, 3),
        rationale=f"Observed {len(lm_spans)} LM calls and {len(error_spans)} sandbox exceptions.",
    )


@scorer
def jev_routing_efficiency(trace: Any) -> Feedback:
    """Score simple requests routed without starting a Flex/RLM execution."""
    if trace is None:
        return Feedback(value=1.0, rationale="No trace available.")
    names = [span.name or "" for span in trace.search_spans()]
    used_rlm = any("RLM" in name or "Flex" in name for name in names)
    lm_calls = sum(1 for name in names if "LM" in name or "Predict" in name)
    if not used_rlm and lm_calls <= 2:
        return Feedback(value=1.0, rationale=f"Fast path completed in {lm_calls} LM calls.")
    if used_rlm and lm_calls <= 15:
        return Feedback(value=0.95, rationale=f"Bounded RLM/Flex execution used {lm_calls} LM calls.")
    score = max(0.2, 1.0 - 0.02 * max(0, lm_calls - 15))
    return Feedback(value=round(score, 3), rationale=f"Heavy route used {lm_calls} LM calls.")


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
    ]


def gepa_mlflow_metric(
    gold: dspy.Example,
    pred: dspy.Prediction,
    trace: Any = None,  # noqa: ARG001
    pred_name: str | None = None,  # noqa: ARG001
    pred_trace: Any = None,  # noqa: ARG001
    program_trace: Any = None,
    llm_call_penalty: float = 0.02,
) -> dspy.Prediction:
    """Return correctness plus concise reflective guidance for GEPA."""
    expected = str(getattr(gold, "answer", ""))
    actual = str(getattr(pred, "answer", ""))
    call_count = len(program_trace) if isinstance(program_trace, (list, tuple)) else 0
    judge = dspy.ChainOfThought("task, expected_answer, candidate_answer -> is_correct: bool, critique: str")
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
) -> dspy.Module:
    """Compile the Flex program and log the result as a deliberate MLflow run."""
    if budget not in {"light", "medium", "heavy"} or not 1 <= num_threads <= 32:
        raise ValueError("Invalid GEPA budget or thread count")
    with mlflow.start_run(run_name="fleet-flex-gepa-compile"):
        optimizer = dspy.GEPA(
            metric=gepa_mlflow_metric,
            reflection_lm=reflection_lm,
            auto=budget,
            num_threads=num_threads,
        )
        optimized = optimizer.compile(engine, trainset=trainset, valset=valset)
        optimized.save(save_path)
        mlflow.log_artifact(save_path)
        return optimized
