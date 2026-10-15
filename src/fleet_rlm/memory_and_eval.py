"""Session VFS persistence, adaptive DSPy execution, and MLflow evaluation."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import dspy
import mlflow
import psycopg
from dspy.primitives.facade import SandboxLM
from dspy.utils.exceptions import AdapterParseError
from mlflow.entities import SpanType

from fleet_rlm.dspy_support import FleetRLMChatAdapter, decision_evidence
from fleet_rlm.dspy_support import without_mlflow_dspy_callback as _without_mlflow_dspy_callback
from fleet_rlm.engine.routes import Route, parse_route
from fleet_rlm.engine.signatures import (
    TRIAGE_CRITERIA,
    ChildTask,
    ContextualChildTask,
    DirectAnswer,
    FleetTask,
    TurnTriage,
)
from fleet_rlm.interpreter import DaytonaInterpreter, DaytonaSandboxManager, validate_session_id
from fleet_rlm.observability import activity_text
from fleet_rlm.optimize.artifacts import CompiledProgramFile
from fleet_rlm.store import Store
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
        self.triage = dspy.Predict(TurnTriage)
        self.triage.set_criteria("route", TRIAGE_CRITERIA)
        self.direct_cot = dspy.ChainOfThought(DirectAnswer)

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
                ChildTask,
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
                signature=ContextualChildTask,
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
            FleetTask,
            tools=[sub_rlm, spawn_agent, spawn_agents],
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
        )
        self.primary_rlm = dspy.RLM(
            FleetTask,
            tools=[sub_rlm, spawn_agent, spawn_agents],
            sub_lm=self._metered_sub,
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
            max_iters=16,
            max_llm_calls=sandbox_mgr.max_llm_calls,
        )
        self.compiled = CompiledProgramFile(compiled_path)
        # Set by the optimizer's per-example factory; IsolatedFleetProgram deletes it after the rollout.
        self.optimization_artifact_path: str | None = None
        self.compiled.load_initial(self)

    def load_compiled_program(self, content: bytes) -> bool:
        """Install one session-owned GEPA artifact; False when it is already active."""
        return self.compiled.install(self, content)

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
        self.compiled.reload_if_changed(self)
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
        route = Route.FLEX
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
                route = Route.RLM if mode == "rlm" else Route.FLEX
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
                    # An unknown or missing label takes the bounded RLM path.
                    route = parse_route(decision) or Route.RLM
                    span.set_outputs({"route": route.value, **evidence})
        except Exception as exc:
            if _turn_is_over(exc):
                raise
            _LOGGER.warning(
                "Optional route selection failed (%s: %s); continuing with bounded RLM execution",
                type(exc).__name__,
                _safe_route_error(exc, self.jev_lm or self.sub_lm),
            )
            route = Route.RLM
            reason = "routing_error"
            summary = f"Route selection failed ({type(exc).__name__}); falling back to bounded RLM execution."

        if context is not None and route is Route.DIRECT:
            reason = "selected_context"
            route = Route.RLM
            summary = "Context is selected; overriding the context-free direct route with bounded RLM execution."
        summary += {
            Route.DIRECT: " Answer directly without code execution.",
            Route.FLEX: " Execute a DSPy Flex pipeline with Daytona Python and bounded delegation.",
            Route.RLM: " Execute a bounded RLM with Daytona Python and optional child agents.",
        }[route]
        routing = {**evidence, "effective_route": route.value, "override_reason": reason}
        active_span = mlflow.get_current_active_span()
        if active_span is not None:
            active_span.set_attribute("fleet.routing", routing)
        self.sandbox_mgr.emit("triage_decision", mode=mode, route=route.value, summary=summary, evidence=routing)

        if route is Route.DIRECT:
            with dspy.context(lm=self._metered_root):
                answer = self.direct_cot(
                    memory_summary=memory_summary,
                    session_history=session_history,
                    task=task,
                ).answer
            return dspy.Prediction(answer=answer, learned_skill_note="", route=route.value)

        agent = self.flex_agent if route is Route.FLEX else self.primary_rlm
        with dspy.context(lm=self._metered_root, adapter=FleetRLMChatAdapter()):
            prediction = agent(
                workspace_manifest=workspace_manifest,
                skills_catalog=skills_catalog,
                memory_summary=memory_summary,
                session_history=session_history,
                execution_route=route.value,
                task=task,
                context=context if context is not None else "",
            )
        self.sandbox_mgr.check_turn()
        return dspy.Prediction(
            answer=prediction.answer,
            learned_skill_note=getattr(prediction, "learned_skill_note", ""),
            route=route.value,
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
