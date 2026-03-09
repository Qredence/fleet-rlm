"""Thin product adapter over the guide-native Daytona-backed RLM core."""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.core.config import get_planner_lm_from_env
from fleet_rlm.models import StreamEvent

from .diagnostics import as_diagnostic_error
from .protocol import HostCallbackRequest, HostCallbackResponse
from .results import persist_result
from .sandbox import DaytonaSandboxRuntime
from .spawn import llm_query, llm_query_batched
from .system_prompt import build_system_prompt, build_user_prompt
from .types import (
    AgentNode,
    ChildLink,
    ChildTaskResult,
    DaytonaRunResult,
    DaytonaRunCancelled,
    ExecutionObservation,
    FinalArtifact,
    PromptHandle,
    PromptManifest,
    RecursiveTaskSpec,
    RolloutBudget,
    RolloutSummary,
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_PREVIEW_LIMIT = 1200
_RESULT_PREVIEW_LIMIT = 280
_ROOT_MIN_CHARS = 80
_ROOT_MIN_WORDS = 12
_WORD_RE = re.compile(r"\b\w+\b")
_WHITESPACE_RE = re.compile(r"\s+")
_PATH_LINE_RE = re.compile(r"^(?:/|\.{1,2}/|[A-Za-z]:\\|[A-Za-z0-9._-]+/).*$")
_GREP_LINE_RE = re.compile(r"^[^:\n]+:\d+(?::\d+)?(?:\s*(?:-|\|)\s*.*|: .*)?$")
_GUIDE_SUBMIT_SCHEMA = [
    {"name": "summary", "type": "str | None"},
    {"name": "final_markdown", "type": "str | None"},
    {"name": "output", "type": "object"},
]
_INLINE_PROMPT_LIMIT = 4_000


def _coerce_lm_output(response: Any) -> str:
    if isinstance(response, list) and response:
        first = response[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"])
        return str(first)
    return str(response)


def _preview_text(text: str, *, limit: int = _PREVIEW_LIMIT) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + "\n\n[truncated preview]"


def _collapse_plain_text(text: str, *, limit: int | None = None) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if limit is not None and len(collapsed) > limit:
        return collapsed[:limit].rstrip()
    return collapsed


class DaytonaRLMRunner:
    """Orchestrate Daytona rollouts while preserving product-specific policy."""

    def __init__(
        self,
        *,
        lm: Any,
        runtime: DaytonaSandboxRuntime | Any | None = None,
        budget: RolloutBudget | None = None,
        output_dir: Path | str = "results/daytona-rlm",
        event_callback: Callable[[StreamEvent], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self.lm = lm
        self.runtime = runtime or DaytonaSandboxRuntime()
        self.budget = budget or RolloutBudget()
        self.output_dir = Path(output_dir)
        self.event_callback = event_callback
        self.cancel_check = cancel_check
        self.run_id = str(uuid.uuid4())
        self._started_at = time.monotonic()
        self._nodes: dict[str, AgentNode] = {}
        self._lock = threading.Lock()
        self._created_sandboxes = 0

    def run(self, *, repo: str, task: str, ref: str | None = None) -> DaytonaRunResult:
        root_id = self._new_node_id()
        termination_reason = "completed"
        error_text: str | None = None
        final_artifact: FinalArtifact | None = None

        try:
            final_artifact = self._run_agent(
                node_id=root_id,
                parent_id=None,
                depth=0,
                repo=repo,
                ref=ref,
                task=task,
            )
        except DaytonaRunCancelled as exc:
            termination_reason = "cancelled"
            error_text = str(exc)
            raise
        except TimeoutError as exc:
            termination_reason = "timeout"
            error_text = str(exc)
            raise
        except Exception as exc:
            termination_reason = "error"
            error_text = str(exc)
            raise
        finally:
            summary = RolloutSummary(
                duration_ms=int((time.monotonic() - self._started_at) * 1000),
                sandboxes_used=self._created_sandboxes,
                termination_reason=termination_reason,
                error=error_text,
            )
            result = DaytonaRunResult(
                run_id=self.run_id,
                repo=repo,
                ref=ref,
                task=task,
                budget=self.budget,
                root_id=root_id,
                nodes=self._nodes,
                final_artifact=final_artifact,
                summary=summary,
            )
            persist_result(result, output_dir=self.output_dir)

        return result

    def run_child_task(
        self,
        *,
        parent_id: str,
        depth: int,
        task_spec: RecursiveTaskSpec,
    ) -> ChildTaskResult:
        self._assert_not_cancelled()
        if depth > self.budget.max_depth:
            raise RuntimeError(
                f"Recursive depth exceeded: {depth} > max_depth={self.budget.max_depth}"
            )

        parent = self._nodes[parent_id]
        child_id = self._new_node_id()
        artifact = self._run_agent(
            node_id=child_id,
            parent_id=parent_id,
            depth=depth,
            repo=parent.repo,
            ref=parent.ref,
            task=task_spec.task,
        )
        rendered = self._render_child_result(artifact)
        return ChildTaskResult(
            child_id=child_id,
            task=task_spec,
            text=rendered,
            result_preview=self._build_result_preview(artifact, fallback_text=rendered),
            status=self._child_status_from_artifact(artifact),
        )

    def _run_agent(
        self,
        *,
        node_id: str,
        parent_id: str | None,
        depth: int,
        repo: str,
        ref: str | None,
        task: str,
    ) -> FinalArtifact | None:
        self._assert_time_budget()
        node = AgentNode(
            node_id=node_id,
            parent_id=parent_id,
            depth=depth,
            task=task,
            repo=repo,
            ref=ref,
        )
        with self._lock:
            self._nodes[node_id] = node
            if parent_id is not None:
                self._nodes[parent_id].child_ids.append(node_id)
        self._assert_not_cancelled(node=node)
        self._emit_status(
            node=node,
            text=f"Bootstrapping Daytona sandbox at depth {depth}.",
            phase="sandbox_create",
        )

        try:
            session = self._create_session(repo=repo, ref=ref)
        except Exception as exc:
            diagnostic = as_diagnostic_error(exc, phase="sandbox_create")
            node.status = "error"
            node.error = f"{diagnostic.category}: {diagnostic}"
            node.final_artifact = FinalArtifact(
                kind="error",
                value=node.error,
                finalization_mode="error",
            )
            raise

        node.sandbox_id = session.sandbox_id
        node.workspace_path = session.repo_path
        self._emit_status(
            node=node,
            text="Daytona sandbox ready.",
            phase="sandbox_ready",
        )

        system_prompt = build_system_prompt(
            repo_path=session.repo_path, budget=self.budget
        )
        user_prompt = build_user_prompt(repo=repo, ref=ref)
        observation_text = "No execution has happened yet."

        try:
            try:
                self._assert_not_cancelled(node=node)
                self._emit_status(
                    node=node,
                    text="Starting persistent Daytona driver.",
                    phase="driver_start",
                )
                session.start_driver(timeout=self._remaining_timeout())
                self._emit_status(
                    node=node,
                    text="Persistent Daytona driver is ready.",
                    phase="driver_ready",
                )
            except Exception as exc:
                diagnostic = as_diagnostic_error(exc, phase="driver_start")
                node.status = "error"
                node.error = f"{diagnostic.category}: {diagnostic}"
                node.final_artifact = FinalArtifact(
                    kind="error",
                    value=node.error,
                    finalization_mode="error",
                )
                raise
            for iteration in range(1, self.budget.max_iterations + 1):
                self._assert_not_cancelled(node=node)
                self._assert_time_budget()
                self._emit_status(
                    node=node,
                    text=f"Running Daytona iteration {iteration} at depth {depth}.",
                    phase="iteration",
                )
                prompt = self._build_iteration_prompt(
                    session=session,
                    node=node,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    task=task,
                    observation_text=observation_text,
                    iteration=iteration,
                )
                response_text = self._call_lm(prompt)
                node.iteration_count = iteration
                node.prompt_previews.append(_preview_text(prompt))
                node.response_previews.append(_preview_text(response_text))
                code = self._extract_code(response_text)
                if code is None:
                    fallback = FinalArtifact(
                        kind="markdown",
                        value=response_text,
                        finalization_mode="fallback",
                    )
                    if self._accept_final_artifact(node=node, artifact=fallback):
                        node.final_artifact = fallback
                        node.status = "completed"
                        return fallback
                    observation_text = self._build_root_retry_observation(
                        artifact=fallback,
                        base_observation=(
                            "The previous response did not produce executable code "
                            "or an acceptable synthesized final answer."
                        ),
                    )
                    continue

                execution = session.execute_code(
                    code=code,
                    callback_handler=lambda request: self._handle_host_callback(
                        node=node,
                        request=request,
                    ),
                    timeout=self._remaining_timeout(),
                    submit_schema=_GUIDE_SUBMIT_SCHEMA,
                )
                observation = ExecutionObservation(
                    iteration=iteration,
                    code=code,
                    stdout=_preview_text(execution.stdout),
                    stderr=_preview_text(execution.stderr),
                    error=_preview_text(execution.error) if execution.error else None,
                    duration_ms=execution.duration_ms,
                    callback_count=execution.callback_count,
                )
                node.observations.append(observation)

                if execution.final_artifact is not None:
                    artifact = FinalArtifact(
                        kind=str(execution.final_artifact.get("kind", "markdown")),
                        value=execution.final_artifact.get("value"),
                        variable_name=execution.final_artifact.get("variable_name"),
                        finalization_mode=str(
                            execution.final_artifact.get(
                                "finalization_mode",
                                "fallback",
                            )
                        ),
                    )
                    if self._accept_final_artifact(node=node, artifact=artifact):
                        node.final_artifact = artifact
                        node.status = "completed"
                        return artifact
                    observation_text = self._build_root_retry_observation(
                        artifact=artifact,
                        base_observation=self._render_observation(observation),
                    )
                    continue

                if self._is_fatal_execution_error(execution.error):
                    node.status = "error"
                    node.error = execution.error
                    artifact = FinalArtifact(
                        kind="error",
                        value=execution.error,
                        finalization_mode="error",
                    )
                    node.final_artifact = artifact
                    return artifact

                observation_text = self._render_observation(observation)

            node.status = "error"
            node.error = (
                f"Exceeded max_iterations={self.budget.max_iterations} without SUBMIT()"
            )
            return FinalArtifact(
                kind="error",
                value=node.error,
                finalization_mode="error",
            )
        except DaytonaRunCancelled as exc:
            node.status = "cancelled"
            node.error = str(exc)
            artifact = FinalArtifact(
                kind="cancelled",
                value=str(exc),
                finalization_mode="cancelled",
            )
            node.final_artifact = artifact
            raise
        finally:
            with contextlib.suppress(Exception):
                session.close_driver(timeout=2.0)
            with contextlib.suppress(Exception):
                session.delete()

    def _build_iteration_prompt(
        self,
        *,
        session: Any,
        node: AgentNode,
        system_prompt: str,
        user_prompt: str,
        task: str,
        observation_text: str,
        iteration: int,
    ) -> str:
        task_externalized = len(str(task or "")) > _INLINE_PROMPT_LIMIT
        observation_externalized = (
            len(str(observation_text or "")) > _INLINE_PROMPT_LIMIT
        )
        task_section = self._externalized_prompt_section(
            session=session,
            node=node,
            title="Task",
            text=task,
            kind="task",
            label=f"node-{node.node_id}-task",
        )
        observation_section = self._externalized_prompt_section(
            session=session,
            node=node,
            title="Previous observation",
            text=observation_text,
            kind="observation",
            label=f"node-{node.node_id}-iteration-{iteration - 1}-observation",
        )
        manifest_section = ""
        if task_externalized or observation_externalized or node.prompt_handles:
            manifest = self._sync_prompt_manifest(session=session, node=node)
            manifest_section = self._render_prompt_manifest(manifest)
        sections = [
            system_prompt,
            user_prompt,
            f"Iteration: {iteration}",
            task_section,
            observation_section,
            manifest_section,
        ]
        return "\n\n".join(section for section in sections if section.strip())

    def _externalized_prompt_section(
        self,
        *,
        session: Any,
        node: AgentNode,
        title: str,
        text: str,
        kind: str,
        label: str,
    ) -> str:
        content = str(text or "")
        if len(content) <= _INLINE_PROMPT_LIMIT:
            if "\n" not in content:
                return f"{title}: {content}"
            return f"{title}:\n{content}"

        handle = next(
            (
                item
                for item in node.prompt_handles
                if item.kind == kind and item.label == label
            ),
            None,
        )
        if handle is None:
            handle = session.store_prompt(
                text=content,
                kind=kind,
                label=label,
                timeout=self._remaining_timeout(),
            )
            self._record_prompt_handle(node=node, handle=handle)
        return (
            f"{title}: externalized as prompt handle `{handle.handle_id}`.\n"
            f"- kind: {handle.kind}\n"
            f"- label: {handle.label or 'none'}\n"
            f"- path: {handle.path}\n"
            f"- chars: {handle.char_count}\n"
            f"- lines: {handle.line_count}\n"
            f"- preview: {handle.preview or '[empty]'}\n"
            "Inspect it from executed Python with list_prompts() or "
            "read_prompt_slice(handle_id=..., start_line=..., num_lines=...)."
        )

    def _sync_prompt_manifest(self, *, session: Any, node: AgentNode) -> PromptManifest:
        manifest = session.list_prompts(timeout=self._remaining_timeout())
        existing = {handle.handle_id for handle in node.prompt_handles}
        for handle in manifest.handles:
            if handle.handle_id in existing:
                continue
            node.prompt_handles.append(handle)
            existing.add(handle.handle_id)
        return manifest

    @staticmethod
    def _record_prompt_handle(*, node: AgentNode, handle: PromptHandle) -> None:
        if any(item.handle_id == handle.handle_id for item in node.prompt_handles):
            return
        node.prompt_handles.append(handle)

    @staticmethod
    def _render_prompt_manifest(manifest: PromptManifest) -> str:
        if not manifest.handles:
            return (
                "Prompt manifest: no externalized prompt objects yet. "
                "Use store_prompt(...) only when you need to preserve large "
                "derived context across iterations."
            )

        lines = ["Prompt manifest (externalized context available inside the sandbox):"]
        for handle in manifest.handles:
            lines.append(
                "- "
                f"{handle.handle_id}: kind={handle.kind}, "
                f"label={handle.label or 'none'}, "
                f"path={handle.path}, chars={handle.char_count}, "
                f"lines={handle.line_count}, "
                f"preview={handle.preview or '[empty]'}"
            )
        lines.append(
            "Use list_prompts() for the full manifest and read_prompt_slice(...) "
            "to inspect bounded slices instead of expecting long externalized "
            "content inline."
        )
        return "\n".join(lines)

    def _call_lm(self, prompt: str) -> str:
        with dspy.context(lm=self.lm):
            response = self.lm(prompt)
        return _coerce_lm_output(response)

    def _extract_code(self, response_text: str) -> str | None:
        match = _CODE_BLOCK_RE.search(response_text)
        if match is not None:
            return match.group(1).strip()
        return None

    def _handle_host_callback(
        self,
        *,
        node: AgentNode,
        request: HostCallbackRequest,
    ) -> HostCallbackResponse:
        task_specs: list[RecursiveTaskSpec] = []
        try:
            if request.name in {"llm_query", "rlm_query"}:
                task_spec = self._normalize_recursive_task(request.payload.get("task"))
                task_specs = [task_spec]
                self._emit_tool_call(
                    node=node,
                    request=request,
                    tool_input={"task": task_spec.to_dict()},
                )
                child_result = llm_query(
                    self,
                    parent_id=node.node_id,
                    depth=node.depth + 1,
                    task_spec=task_spec,
                )
                self._append_child_link(
                    node=node,
                    link=ChildLink(
                        child_id=child_result.child_id,
                        callback_name=request.name,
                        task=task_spec,
                        result_preview=child_result.result_preview,
                        status=child_result.status,
                    ),
                )
                value = child_result.text
                self._emit_tool_result(node=node, request=request, value=value)
            elif request.name in {"llm_query_batched", "rlm_query_batched"}:
                task_specs = self._normalize_recursive_tasks(
                    request.payload.get("tasks")
                )
                self._emit_tool_call(
                    node=node,
                    request=request,
                    tool_input={
                        "tasks": [task_spec.to_dict() for task_spec in task_specs]
                    },
                )
                value = self._run_batched_recursive_tasks(
                    node=node,
                    request=request,
                    task_specs=task_specs,
                )
                self._emit_tool_result(node=node, request=request, value=value)
            else:
                raise RuntimeError(f"Unsupported host callback: {request.name}")
        except Exception as exc:
            for task_spec in task_specs:
                self._append_child_link(
                    node=node,
                    link=ChildLink(
                        child_id=None,
                        callback_name=request.name,
                        task=task_spec,
                        result_preview=self._truncate_result_text(str(exc)),
                        status="error",
                    ),
                )
            return HostCallbackResponse(
                callback_id=request.callback_id,
                ok=False,
                error=str(exc),
            )

        return HostCallbackResponse(
            callback_id=request.callback_id,
            ok=True,
            value=value,
        )

    @staticmethod
    def _normalize_recursive_task(raw: Any) -> RecursiveTaskSpec:
        return RecursiveTaskSpec.from_raw(raw)

    def _normalize_recursive_tasks(self, raw_tasks: Any) -> list[RecursiveTaskSpec]:
        if raw_tasks is None:
            return []
        if not isinstance(raw_tasks, list):
            raise ValueError("llm_query_batched expects a list of task specs.")
        return [self._normalize_recursive_task(raw_task) for raw_task in raw_tasks]

    def _run_batched_recursive_tasks(
        self,
        *,
        node: AgentNode,
        request: HostCallbackRequest,
        task_specs: list[RecursiveTaskSpec],
    ) -> list[str]:
        if not task_specs:
            return []

        unique_specs: list[RecursiveTaskSpec] = []
        key_to_unique_index: dict[tuple[Any, ...], int] = {}
        unique_index_by_original_index: list[int] = []
        is_deduped_reuse_by_original_index: list[bool] = []

        for task_spec in task_specs:
            dedupe_key = self._task_dedupe_key(task_spec)
            if dedupe_key in key_to_unique_index:
                unique_index_by_original_index.append(key_to_unique_index[dedupe_key])
                is_deduped_reuse_by_original_index.append(True)
                continue

            unique_index = len(unique_specs)
            key_to_unique_index[dedupe_key] = unique_index
            unique_specs.append(task_spec)
            unique_index_by_original_index.append(unique_index)
            is_deduped_reuse_by_original_index.append(False)

        unique_results = llm_query_batched(
            self,
            parent_id=node.node_id,
            depth=node.depth + 1,
            task_specs=unique_specs,
        )

        values: list[str] = []
        for original_index, task_spec in enumerate(task_specs):
            unique_result = unique_results[
                unique_index_by_original_index[original_index]
            ]
            values.append(unique_result.text)
            self._append_child_link(
                node=node,
                link=ChildLink(
                    child_id=unique_result.child_id,
                    callback_name=request.name,
                    task=task_spec,
                    result_preview=unique_result.result_preview,
                    status=(
                        "deduped_reused"
                        if is_deduped_reuse_by_original_index[original_index]
                        else unique_result.status
                    ),
                ),
            )
        return values

    @staticmethod
    def _task_dedupe_key(task_spec: RecursiveTaskSpec) -> tuple[Any, ...]:
        source = task_spec.source
        source_key: Any = source.source_id or (
            source.kind,
            source.path,
            source.start_line,
            source.end_line,
            source.chunk_index,
            source.header,
            source.pattern,
        )
        return (task_spec.task, source_key)

    def _append_child_link(self, *, node: AgentNode, link: ChildLink) -> None:
        node.child_links.append(link)

    def _render_child_result(self, artifact: FinalArtifact | None) -> str:
        if artifact is None:
            return ""
        rendered = self._textual_render_for_result(artifact.value)
        return self._truncate_result_text(rendered)

    def _build_result_preview(
        self,
        artifact: FinalArtifact | None,
        *,
        fallback_text: str = "",
    ) -> str:
        if artifact is not None:
            candidate = self._extract_synthesized_text(artifact.value)
            if candidate is not None:
                return self._truncate_plain_text(candidate, limit=_RESULT_PREVIEW_LIMIT)
            rendered = self._textual_render_for_result(artifact.value)
            if rendered:
                return self._truncate_plain_text(rendered, limit=_RESULT_PREVIEW_LIMIT)
        return self._truncate_plain_text(fallback_text, limit=_RESULT_PREVIEW_LIMIT)

    @staticmethod
    def _child_status_from_artifact(artifact: FinalArtifact | None) -> str:
        if artifact is None:
            return "completed"
        if artifact.finalization_mode == "error":
            return "error"
        if artifact.finalization_mode == "cancelled":
            return "cancelled"
        return "completed"

    @staticmethod
    def _extract_synthesized_text(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("summary", "final_markdown"):
                candidate = value.get(key)
                if candidate is None:
                    continue
                text = str(candidate)
                if text.strip():
                    return text
        return None

    @staticmethod
    def _textual_render_for_result(value: Any) -> str:
        if value is None:
            return ""
        candidate = DaytonaRLMRunner._extract_synthesized_text(value)
        if candidate is not None:
            return candidate
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    def _truncate_result_text(self, text: str) -> str:
        if len(text) <= self.budget.result_truncation_limit:
            return text
        return (
            text[: self.budget.result_truncation_limit].rstrip()
            + "\n\n[truncated child result]"
        )

    @staticmethod
    def _truncate_plain_text(text: str, *, limit: int) -> str:
        collapsed = _collapse_plain_text(text, limit=limit)
        return collapsed

    def _accept_final_artifact(
        self,
        *,
        node: AgentNode,
        artifact: FinalArtifact,
    ) -> bool:
        if node.depth > 0:
            return True
        return self._root_finalization_candidate(artifact) is not None

    def _root_finalization_candidate(self, artifact: FinalArtifact) -> str | None:
        value = artifact.value
        candidate = self._extract_synthesized_text(value)
        if candidate is None:
            return None

        normalized = _collapse_plain_text(candidate)
        if not normalized:
            return None
        if len(normalized) < _ROOT_MIN_CHARS:
            return None
        if len(_WORD_RE.findall(normalized)) < _ROOT_MIN_WORDS:
            return None
        if self._looks_like_unsynthesized_root_payload(value=value, raw_text=candidate):
            return None
        return candidate

    def _looks_like_unsynthesized_root_payload(
        self, *, value: Any, raw_text: str
    ) -> bool:
        if isinstance(value, (list, tuple)):
            return True
        if isinstance(value, dict) and self._extract_synthesized_text(value) is None:
            return True

        stripped = raw_text.strip()
        if not stripped:
            return True
        if stripped.startswith("```"):
            return True
        if stripped.startswith("{") or stripped.startswith("["):
            return True

        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if len(lines) >= 2:
            if all(_PATH_LINE_RE.match(line) for line in lines):
                return True
            grep_like_count = sum(1 for line in lines if _GREP_LINE_RE.match(line))
            if grep_like_count >= max(2, len(lines) - 1):
                return True
        return False

    def _build_root_retry_observation(
        self,
        *,
        artifact: FinalArtifact,
        base_observation: str,
    ) -> str:
        preview = self._build_result_preview(artifact)
        return (
            f"{base_observation}\n\n"
            "Your previous FINAL produced raw intermediate data instead of a "
            "human-readable synthesized answer. Reuse the repository evidence "
            "already stored in Python variables, summarize the key findings in "
            "concise markdown prose, and finalize with SUBMIT(...) using that "
            "summary text or a dict with 'summary' or 'final_markdown'. "
            "FINAL(...) and FINAL_VAR(...) remain available only as legacy aliases.\n\n"
            f"Rejected final preview: {preview or '[empty final output]'}"
        )

    def _render_observation(self, observation: ExecutionObservation) -> str:
        chunks = [f"Duration: {observation.duration_ms}ms"]
        if observation.callback_count:
            chunks.append(f"Host callbacks: {observation.callback_count}")
        if observation.stdout.strip():
            chunks.append(f"STDOUT:\n{observation.stdout.strip()}")
        if observation.stderr.strip():
            chunks.append(f"STDERR:\n{observation.stderr.strip()}")
        if observation.error:
            chunks.append(f"ERROR:\n{observation.error}")
        return "\n\n".join(chunks)

    @staticmethod
    def _is_fatal_execution_error(error: str | None) -> bool:
        if not error:
            return False
        fatal_markers = (
            "Recursive depth exceeded",
            "Sandbox budget exceeded",
            "Global timeout exceeded",
        )
        return any(marker in error for marker in fatal_markers)

    def _create_session(self, *, repo: str, ref: str | None) -> Any:
        with self._lock:
            if self._created_sandboxes >= self.budget.max_sandboxes:
                raise RuntimeError(
                    f"Sandbox budget exceeded: {self._created_sandboxes} >= {self.budget.max_sandboxes}"
                )
            self._created_sandboxes += 1
        return self.runtime.create_repo_session(repo_url=repo, ref=ref)

    def _remaining_timeout(self) -> float:
        elapsed = time.monotonic() - self._started_at
        remaining = self.budget.global_timeout - elapsed
        if remaining <= 0:
            raise TimeoutError(
                f"Global timeout exceeded after {elapsed:.1f}s > {self.budget.global_timeout}s"
            )
        return remaining

    def _assert_time_budget(self) -> None:
        _ = self._remaining_timeout()

    @staticmethod
    def _new_node_id() -> str:
        return uuid.uuid4().hex

    def _assert_not_cancelled(self, *, node: AgentNode | None = None) -> None:
        if self.cancel_check is None or not self.cancel_check():
            return
        if node is not None:
            node.status = "cancelled"
            node.error = "Request cancelled."
        raise DaytonaRunCancelled("Request cancelled.")

    def _runtime_payload(
        self,
        *,
        node: AgentNode | None = None,
        phase: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runtime": {
                "depth": node.depth if node is not None else 0,
                "max_depth": self.budget.max_depth,
                "execution_profile": "DAYTONA_PILOT",
                "sandbox_active": node is not None and node.sandbox_id is not None,
                "effective_max_iters": self.budget.max_iterations,
                "execution_mode": "daytona_pilot",
                "runtime_mode": "daytona_pilot",
                "sandbox_id": node.sandbox_id if node is not None else None,
            }
        }
        if node is not None:
            payload["node_id"] = node.node_id
            payload["repo"] = node.repo
            payload["ref"] = node.ref
            payload["task"] = node.task
        if phase:
            payload["phase"] = phase
        if extra:
            payload.update(extra)
        return payload

    def _emit_event(self, event: StreamEvent) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(event)
        except Exception:
            return

    def _emit_status(self, *, node: AgentNode, text: str, phase: str) -> None:
        self._emit_event(
            StreamEvent(
                kind="status",
                text=text,
                payload=self._runtime_payload(node=node, phase=phase),
            )
        )

    def _emit_tool_call(
        self,
        *,
        node: AgentNode,
        request: HostCallbackRequest,
        tool_input: Any,
    ) -> None:
        self._emit_event(
            StreamEvent(
                kind="tool_call",
                text=f"Calling {request.name}",
                payload=self._runtime_payload(
                    node=node,
                    phase="recursive_call",
                    extra={
                        "tool_name": request.name,
                        "tool_input": tool_input,
                    },
                ),
            )
        )

    def _emit_tool_result(
        self,
        *,
        node: AgentNode,
        request: HostCallbackRequest,
        value: Any,
    ) -> None:
        if isinstance(value, list):
            rendered: Any = {
                "count": len(value),
                "preview": value[: min(3, len(value))],
            }
        elif isinstance(value, dict):
            rendered = value
        else:
            rendered = str(value)
        self._emit_event(
            StreamEvent(
                kind="tool_result",
                text=f"{request.name} completed",
                payload=self._runtime_payload(
                    node=node,
                    phase="recursive_result",
                    extra={
                        "tool_name": request.name,
                        "tool_output": rendered,
                        "status": "ok",
                    },
                ),
            )
        )


def run_daytona_rlm_pilot(
    *,
    repo: str,
    task: str,
    ref: str | None = None,
    budget: RolloutBudget | None = None,
    output_dir: Path | str = "results/daytona-rlm",
    runtime: Any | None = None,
    lm: Any | None = None,
) -> DaytonaRunResult:
    """Run the experimental Daytona-backed RLM pilot."""

    planner_lm = lm or get_planner_lm_from_env()
    if planner_lm is None:
        raise RuntimeError(
            "Planner LM not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)."
        )

    runner = DaytonaRLMRunner(
        lm=planner_lm,
        runtime=runtime,
        budget=budget,
        output_dir=output_dir,
    )
    return runner.run(repo=repo, task=task, ref=ref)
