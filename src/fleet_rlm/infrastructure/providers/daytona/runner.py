"""Custom Daytona host-loop runner that preserves the guide's core RLM invariants."""

from __future__ import annotations

import contextvars
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

import dspy

from fleet_rlm.core.models import StreamEvent

from .config import resolve_daytona_lm_runtime_config
from .dspy_modules import (
    ChildResultSynthesisModule,
    DaytonaConversationGroundingModule,
    RecursiveSpawnPolicyModule,
    RecursiveTaskDecompositionModule,
)
from .protocol import (
    ExecutionEventFrame,
    HostCallbackRequest,
    HostCallbackResponse,
    RunEventFrame,
)
from .results import persist_result
from .runner_callbacks import DaytonaHostCallbackDispatcher, DaytonaRunnerProtocol
from .runner_events import DaytonaRuntimeEventEmitter
from .sandbox import DaytonaSandboxRuntime, DaytonaSandboxSession
from .system_prompt import build_system_prompt, build_user_prompt
from .types import (
    AgentNode,
    ChildLink,
    ChildTaskResult,
    DaytonaEvidenceRef,
    DaytonaRunCancelled,
    DaytonaRunResult,
    ExecutionObservation,
    FinalArtifact,
    PromptHandle,
    PromptManifest,
    RecursiveTaskSpec,
    RolloutBudget,
    RolloutSummary,
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\b\w+\b")
_PATH_LINE_RE = re.compile(r"^(?:/|\.{1,2}/|[A-Za-z]:\\|[A-Za-z0-9._-]+/).*$")
_GREP_LINE_RE = re.compile(r"^[^:\n]+:\d+(?::\d+)?(?:\s*(?:-|\|)\s*.*|: .*)?$")
_FILE_LIKE_RE = re.compile(r"^[^\s/\\]+\.[A-Za-z0-9]{1,8}$")
_INLINE_PROMPT_LIMIT = 4_000
_GUIDE_SUBMIT_SCHEMA = [
    {"name": "summary", "type": "str | None"},
    {"name": "final_markdown", "type": "str | None"},
    {"name": "output", "type": "object"},
]

_EVALUATION_TRACE_KEYS = ("spawn_policy", "decomposition", "child_synthesis")


@dataclass(slots=True)
class _IterationOutcome:
    completed: bool
    observation_text: str
    last_iteration: int
    final_artifact: FinalArtifact | None = None


@dataclass(slots=True)
class _RunTerminalState:
    termination_reason: str = "completed"
    error_text: str | None = None
    final_artifact: FinalArtifact | None = None


def _collapse_preview(text: str, *, limit: int = 240) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", str(text)).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip()


def _collapse_plain_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _preview_text(text: str, *, limit: int = 1_200) -> str:
    stripped = str(text or "").strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + "\n\n[truncated preview]"


def _coerce_lm_output(response: Any) -> str:
    if isinstance(response, list) and response:
        first = response[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"])
        return str(first)
    return str(response)


def _normalize_history_turn(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    user_request = str(raw.get("user_request", "") or "").strip()
    assistant_response = str(raw.get("assistant_response", "") or "").strip()
    if not user_request and not assistant_response:
        return None
    return {
        "user_request": user_request,
        "assistant_response": assistant_response,
    }


def _normalized_conversation_history(
    conversation_history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in conversation_history or []:
        turn = _normalize_history_turn(item)
        if turn is not None:
            normalized.append(turn)
    return normalized


def _task_spec_dedupe_key(task_spec: RecursiveTaskSpec) -> tuple[Any, ...]:
    source = task_spec.source
    return (
        _collapse_plain_text(task_spec.task).lower(),
        source.source_id,
        source.path,
        source.start_line or source.line,
        source.end_line,
        source.chunk_index,
        source.header,
        source.pattern,
    )


def _semantic_prompt_for_task(task_spec: RecursiveTaskSpec) -> str:
    lines = [f"Task:\n{task_spec.task}"]
    if task_spec.label:
        lines.append(f"Label: {task_spec.label}")
    source = task_spec.source
    if source.kind != "manual" or source.preview or source.path:
        parts = [f"Source kind: {source.kind}"]
        if source.path:
            parts.append(f"Path: {source.path}")
        if source.start_line is not None or source.end_line is not None:
            start = source.start_line or source.line or 1
            end = source.end_line or start
            parts.append(f"Line span: {start}-{end}")
        if source.header:
            parts.append(f"Header: {source.header}")
        if source.pattern:
            parts.append(f"Pattern: {source.pattern}")
        if source.preview:
            parts.append(f"Preview:\n{source.preview}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines).strip()


def _empty_node_evaluation() -> dict[str, list[dict[str, Any]]]:
    return {key: [] for key in _EVALUATION_TRACE_KEYS}


def _merge_evaluation_maps(
    base: dict[str, Any],
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(incoming, dict):
        return base
    incoming_nodes = incoming.get("nodes", {})
    if not isinstance(incoming_nodes, dict):
        return base

    base_nodes = base.setdefault("nodes", {})
    for node_id, payload in incoming_nodes.items():
        if not isinstance(payload, dict):
            continue
        target = base_nodes.setdefault(str(node_id), _empty_node_evaluation())
        for key in _EVALUATION_TRACE_KEYS:
            items = payload.get(key, [])
            if not isinstance(items, list):
                continue
            target.setdefault(key, [])
            target[key].extend(item for item in items if isinstance(item, dict))
    return base


class DaytonaRLMRunner:
    """Run Daytona RLM calls through a persistent sandbox driver."""

    def __init__(
        self,
        *,
        lm: Any | None = None,
        delegate_lm: Any | None = None,
        runtime: DaytonaSandboxRuntime | None = None,
        budget: RolloutBudget | None = None,
        output_dir: Path | str = "results/daytona-rlm",
        event_callback: Callable[[StreamEvent], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        volume_name: str | None = None,
    ) -> None:
        self.runtime = runtime or DaytonaSandboxRuntime()
        self.budget = budget or RolloutBudget()
        self.output_dir = Path(output_dir)
        self.event_callback = event_callback
        self.cancel_check = cancel_check
        self.volume_name = volume_name
        self.run_id = str(uuid.uuid4())
        self._lm = lm
        self._delegate_lm = delegate_lm
        self._host_callbacks: dict[str, Callable[..., Any]] = {}
        self._conversation_grounder = DaytonaConversationGroundingModule()
        self._spawn_policy = RecursiveSpawnPolicyModule()
        self._recursive_decomposer = RecursiveTaskDecompositionModule()
        self._child_synthesizer = ChildResultSynthesisModule()
        self._state_lock = threading.Lock()
        self._active_repo: str = ""
        self._active_ref: str | None = None
        self._active_context_paths: list[str] = []
        self._sandboxes_used = 0

    def register_host_callback(self, name: str, handler: Callable[..., Any]) -> None:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Host callback name cannot be empty.")
        self._host_callbacks[normalized] = handler

    def run(
        self,
        *,
        repo: str | None,
        task: str,
        ref: str | None = None,
        context_paths: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        session: DaytonaSandboxSession | None = None,
    ) -> DaytonaRunResult:
        self.run_id = str(uuid.uuid4())
        with self._state_lock:
            self._active_repo = repo or ""
            self._active_ref = ref
            self._active_context_paths = list(context_paths or [])
            self._sandboxes_used = 0
        self._reserve_sandbox()

        result = self._run_node(
            repo=repo,
            task=task,
            ref=ref,
            context_paths=context_paths,
            conversation_history=conversation_history,
            session=session,
            parent_id=None,
            depth=0,
            strict_finalization=True,
        )
        self._apply_root_synthesis_guard(result)
        result.summary.sandboxes_used = max(1, self._sandboxes_used)
        persist_result(result, output_dir=self.output_dir)
        return result

    def _run_node(
        self,
        *,
        repo: str | None,
        task: str,
        ref: str | None = None,
        context_paths: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        session: DaytonaSandboxSession | None = None,
        parent_id: str | None,
        depth: int,
        strict_finalization: bool,
        node_id: str | None = None,
    ) -> DaytonaRunResult:
        created_session = False
        active_session = session
        if active_session is None:
            active_session = self.runtime.create_workspace_session(
                repo_url=repo,
                ref=ref,
                context_paths=context_paths,
                volume_name=self.volume_name,
            )
            created_session = True

        active_session.reset_for_new_call()
        host_loop = _HostLoopDaytonaRuntime(
            runner=self,
            session=active_session,
            repo=repo,
            ref=ref,
            task=task,
            conversation_history=conversation_history,
            parent_id=parent_id,
            depth=depth,
            node_id=node_id,
            strict_finalization=strict_finalization,
        )
        try:
            return host_loop.run()
        finally:
            active_session.close_driver()
            if created_session:
                active_session.delete()

    def _ensure_lms(self) -> tuple[Any, Any]:
        if self._lm is None:
            current_lm = getattr(dspy.settings, "lm", None)
            if current_lm is not None:
                self._lm = current_lm
            else:
                config = resolve_daytona_lm_runtime_config()
                self._lm = dspy.LM(
                    config.model,
                    api_key=config.api_key,
                    api_base=config.api_base,
                    max_tokens=config.max_tokens,
                )
                if config.delegate_model and self._delegate_lm is None:
                    self._delegate_lm = dspy.LM(
                        config.delegate_model,
                        api_key=config.delegate_api_key or config.api_key,
                        api_base=config.delegate_api_base or config.api_base,
                        max_tokens=config.max_tokens,
                    )
        if self._delegate_lm is None:
            self._delegate_lm = self._lm
        return self._lm, self._delegate_lm

    def _invoke_lm(self, lm: Any, prompt: str) -> str:
        with dspy.context(lm=lm):
            response = lm(prompt)
        return _coerce_lm_output(response)

    def _ground_task_with_history(
        self,
        *,
        lm: Any,
        task: str,
        conversation_history: list[dict[str, str]] | None,
    ) -> str:
        normalized_history = _normalized_conversation_history(conversation_history)
        if not normalized_history:
            return ""

        with dspy.context(lm=lm):
            grounded_task = self._conversation_grounder(
                current_user_request=str(task or "").strip(),
                history=dspy.History(messages=normalized_history),
            )
        if not grounded_task:
            return ""
        if _collapse_plain_text(grounded_task) == _collapse_plain_text(str(task or "")):
            return ""
        return grounded_task

    def _reserve_sandbox(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._state_lock:
            remaining = max(0, self.budget.max_sandboxes - self._sandboxes_used)
            if self._sandboxes_used + count > self.budget.max_sandboxes:
                raise RuntimeError(
                    "Sandbox budget exceeded: "
                    f"need {count} more, have {remaining} remaining."
                )
            self._sandboxes_used += count

    def remaining_sandboxes(self) -> int:
        with self._state_lock:
            return max(0, self.budget.max_sandboxes - self._sandboxes_used)

    def run_semantic_task(self, *, task_spec: RecursiveTaskSpec) -> str:
        _, delegate_lm = self._ensure_lms()
        return self._invoke_lm(delegate_lm, _semantic_prompt_for_task(task_spec))

    def run_semantic_tasks_batched(
        self, *, task_specs: list[RecursiveTaskSpec]
    ) -> list[str]:
        if not task_specs:
            return []

        results: dict[int, str] = {}
        errors: list[tuple[int, Exception]] = []
        max_workers = max(1, min(len(task_specs), self.budget.batch_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    contextvars.copy_context().run,
                    self.run_semantic_task,
                    task_spec=task_spec,
                ): index
                for index, task_spec in enumerate(task_specs)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    results[index] = str(future.result())
                except Exception as exc:
                    errors.append((index, exc))

        if errors:
            errors.sort(key=lambda item: item[0])
            details = "; ".join(
                f"task[{index}]: {type(exc).__name__}: {exc}" for index, exc in errors
            )
            raise RuntimeError(
                "run_semantic_tasks_batched failed for "
                f"{len(errors)}/{len(task_specs)} tasks: {details}"
            ) from errors[0][1]
        return [results[index] for index in range(len(task_specs))]

    def run_child_task(
        self,
        *,
        parent_id: str,
        depth: int,
        task_spec: RecursiveTaskSpec,
        parent_task: str | None = None,
    ) -> ChildTaskResult:
        next_depth = depth + 1
        if next_depth > self.budget.max_depth:
            raise RuntimeError(
                f"Recursive depth exceeded: {next_depth} > max_depth={self.budget.max_depth}"
            )

        self._reserve_sandbox()
        child_run = self._run_node(
            repo=self._active_repo or None,
            task=task_spec.task,
            ref=self._active_ref,
            context_paths=self._active_context_paths,
            parent_id=parent_id,
            depth=next_depth,
            strict_finalization=False,
        )
        return self._synthesize_child_task_result(
            parent_task=parent_task or task_spec.task,
            task_spec=task_spec,
            child_run=child_run,
        )

    def _spawn_child_tasks_batched(
        self,
        *,
        parent_id: str,
        depth: int,
        parent_task: str,
        task_specs: list[RecursiveTaskSpec],
    ) -> list[ChildTaskResult]:
        if not task_specs:
            return []

        results: dict[int, ChildTaskResult] = {}
        errors: list[tuple[int, Exception]] = []
        max_workers = max(1, min(len(task_specs), self.budget.batch_concurrency))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    contextvars.copy_context().run,
                    self.run_child_task,
                    parent_id=parent_id,
                    depth=depth,
                    task_spec=task_spec,
                    parent_task=parent_task,
                ): index
                for index, task_spec in enumerate(task_specs)
            }
            for future in as_completed(future_map):
                index = cast(int, future_map[future])
                try:
                    child_result = cast(ChildTaskResult, future.result())
                    results[index] = child_result
                except Exception as exc:
                    errors.append((index, exc))

        if errors:
            errors.sort(key=lambda item: item[0])
            details = "; ".join(
                f"task[{index}]: {type(exc).__name__}: {exc}" for index, exc in errors
            )
            raise RuntimeError(
                "recursive child execution failed for "
                f"{len(errors)}/{len(task_specs)} tasks: {details}"
            ) from errors[0][1]
        return [results[index] for index in range(len(task_specs))]

    @staticmethod
    def _render_child_result(artifact: FinalArtifact | None) -> str:
        if artifact is None:
            return ""
        candidate = DaytonaRLMRunner._extract_synthesized_text(artifact.value)
        if candidate is not None:
            return candidate
        if artifact.value is None:
            return ""
        if isinstance(artifact.value, (dict, list, tuple)):
            return json.dumps(artifact.value, ensure_ascii=False, default=str)
        return str(artifact.value)

    @staticmethod
    def _child_status_from_result(result: DaytonaRunResult) -> str:
        reason = result.summary.termination_reason
        if reason == "cancelled":
            return "cancelled"
        if reason in {"error", "timeout"}:
            return "error"
        artifact = result.final_artifact
        if artifact is not None and artifact.finalization_mode == "error":
            return "error"
        return "completed"

    @staticmethod
    def _evidence_ref_from_task_source(
        source: RecursiveTaskSpec | Any,
    ) -> DaytonaEvidenceRef | None:
        provenance = source.source if isinstance(source, RecursiveTaskSpec) else source
        path = getattr(provenance, "path", None)
        preview = getattr(provenance, "preview", None)
        source_id = getattr(provenance, "source_id", None)
        if path is None and preview is None and source_id is None:
            return None
        line_start = getattr(provenance, "start_line", None) or getattr(
            provenance, "line", None
        )
        line_end = getattr(provenance, "end_line", None) or line_start
        return DaytonaEvidenceRef.from_raw(
            {
                "kind": getattr(provenance, "kind", None) or "manual",
                "source_id": source_id,
                "path": path,
                "start_line": line_start,
                "end_line": line_end,
                "header": getattr(provenance, "header", None),
                "preview": preview,
                "chunk_index": getattr(provenance, "chunk_index", None),
                "pattern": getattr(provenance, "pattern", None),
            }
        )

    @staticmethod
    def _evidence_ref_from_context_source(
        source: Any,
    ) -> DaytonaEvidenceRef | None:
        host_path = getattr(source, "host_path", None)
        source_id = getattr(source, "source_id", None)
        if host_path is None and source_id is None:
            return None
        return DaytonaEvidenceRef.from_raw(
            {
                "kind": getattr(source, "kind", None) or "context_source",
                "source_id": source_id,
                "path": host_path,
                "preview": None,
            }
        )

    @staticmethod
    def _normalize_evidence_refs(raw_items: Any) -> list[DaytonaEvidenceRef]:
        if not isinstance(raw_items, list):
            return []
        refs: list[DaytonaEvidenceRef] = []
        seen: set[tuple[Any, ...]] = set()
        for item in raw_items:
            try:
                ref = DaytonaEvidenceRef.from_raw(item)
            except ValueError:
                continue
            key = (
                ref.kind,
                ref.source_id,
                ref.path,
                ref.start_line,
                ref.end_line,
                ref.header,
                ref.chunk_index,
                ref.pattern,
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
        return refs

    def _normalized_child_evidence(
        self,
        *,
        child_run: DaytonaRunResult,
        task_spec: RecursiveTaskSpec,
    ) -> list[DaytonaEvidenceRef]:
        evidence: list[DaytonaEvidenceRef] = []
        seen: set[tuple[Any, ...]] = set()

        def _append(candidate: DaytonaEvidenceRef | None) -> None:
            if candidate is None:
                return
            key = (
                candidate.kind,
                candidate.source_id,
                candidate.path,
                candidate.start_line,
                candidate.end_line,
                candidate.header,
                candidate.chunk_index,
                candidate.pattern,
            )
            if key in seen:
                return
            seen.add(key)
            evidence.append(candidate)

        root = child_run.nodes.get(child_run.root_id)
        if root is not None:
            for link in root.child_links:
                _append(self._evidence_ref_from_task_source(link.task))

        _append(self._evidence_ref_from_task_source(task_spec))
        for source in child_run.context_sources:
            _append(self._evidence_ref_from_context_source(source))
        return evidence

    def _synthesize_child_task_result(
        self,
        *,
        parent_task: str,
        task_spec: RecursiveTaskSpec,
        child_run: DaytonaRunResult,
    ) -> ChildTaskResult:
        fallback_text = self._render_child_result(child_run.final_artifact)
        if not fallback_text and child_run.summary.error:
            fallback_text = child_run.summary.error
        fallback_preview = _collapse_preview(fallback_text, limit=280)
        child_status = self._child_status_from_result(child_run)
        child_evidence = self._normalized_child_evidence(
            child_run=child_run,
            task_spec=task_spec,
        )

        synthesized = None
        try:
            _, delegate_lm = self._ensure_lms()
            with dspy.context(lm=delegate_lm):
                synthesized = self._child_synthesizer(
                    parent_task=parent_task,
                    child_task_json=json.dumps(task_spec.to_dict(), ensure_ascii=False),
                    child_result_text=fallback_text,
                    child_evidence_json=json.dumps(
                        [item.to_dict() for item in child_evidence],
                        ensure_ascii=False,
                    ),
                    child_status=child_status,
                    child_summary_json=json.dumps(
                        child_run.summary.to_dict(), ensure_ascii=False
                    ),
                )
        except Exception:
            synthesized = None

        answer_markdown = (
            str(getattr(synthesized, "answer_markdown", "") or "").strip()
            if synthesized is not None
            else ""
        )
        result_preview = (
            str(getattr(synthesized, "result_preview", "") or "").strip()
            if synthesized is not None
            else ""
        )
        evidence = self._normalize_evidence_refs(
            list(getattr(synthesized, "evidence", []) or []) if synthesized else []
        )
        follow_up_needed = (
            bool(getattr(synthesized, "follow_up_needed", False))
            if synthesized
            else False
        )
        confidence = getattr(synthesized, "confidence", None) if synthesized else None

        if not answer_markdown:
            answer_markdown = fallback_text
        if not result_preview:
            result_preview = fallback_preview or _collapse_preview(
                answer_markdown, limit=280
            )
        if not evidence:
            evidence = child_evidence

        return ChildTaskResult(
            child_id=child_run.root_id,
            task=task_spec,
            text=answer_markdown,
            result_preview=result_preview,
            status=child_status,
            evidence=evidence,
            confidence=confidence,
            follow_up_needed=follow_up_needed,
            run_result=child_run,
        )

    def _handle_runtime_event(self, frame: RunEventFrame) -> None:
        if self.event_callback is None:
            return
        kind = frame.kind
        if kind not in {
            "status",
            "tool_call",
            "tool_result",
            "final",
            "error",
            "cancelled",
            "warning",
            "reasoning_step",
            "trajectory_step",
        }:
            kind = "status"
        self.event_callback(
            StreamEvent(
                kind=kind,  # type: ignore[arg-type]
                text=frame.text,
                payload=frame.payload or {},
            )
        )

    def _apply_root_synthesis_guard(self, result: DaytonaRunResult) -> None:
        if result.summary.termination_reason == "cancelled":
            return
        if result.final_artifact is None:
            return
        if self._root_finalization_candidate(result.final_artifact) is not None:
            return
        invalid_message = (
            "Root Daytona run returned raw intermediate data instead of a "
            "human-readable synthesized answer."
        )
        result.final_artifact = FinalArtifact(
            kind="error",
            value=invalid_message,
            finalization_mode="error",
        )
        root = result.nodes.get(result.root_id)
        if root is not None:
            root.final_artifact = result.final_artifact
            root.status = "error"
            root.error = invalid_message
        result.summary.termination_reason = "invalid_final_artifact"
        result.summary.error = invalid_message

    def _root_finalization_candidate(self, artifact: FinalArtifact) -> str | None:
        value = artifact.value
        candidate = self._extract_synthesized_text(value)
        if candidate is None:
            return None
        normalized = _collapse_plain_text(candidate)
        if not normalized:
            return None
        if self._looks_like_unsynthesized_root_payload(value=value, raw_text=candidate):
            return None
        if not self._looks_like_synthesized_root_text(normalized):
            return None
        return candidate

    @staticmethod
    def _looks_like_synthesized_root_text(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False

        if _FILE_LIKE_RE.fullmatch(stripped):
            return False

        if "\n" in stripped:
            return True

        if stripped.startswith(("#", "- ", "* ", ">")):
            return True

        if re.search(r"[.!?](?:\s|$)", stripped):
            return True

        word_count = len(_WORD_RE.findall(stripped))
        if word_count >= 3:
            return True

        return bool(re.search(r"\s", stripped) and len(stripped) >= 16)

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

    def _looks_like_unsynthesized_root_payload(
        self, *, value: Any, raw_text: str
    ) -> bool:
        if isinstance(value, (list, tuple)):
            return True
        if isinstance(value, dict) and self._extract_synthesized_text(value) is None:
            return True
        stripped = raw_text.strip()
        if (
            not stripped
            or stripped.startswith("```")
            or stripped.startswith("{")
            or stripped.startswith("[")
        ):
            return True
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if len(lines) >= 2:
            if all(_PATH_LINE_RE.match(line) for line in lines):
                return True
            grep_like_count = sum(1 for line in lines if _GREP_LINE_RE.match(line))
            if grep_like_count >= max(2, len(lines) - 1):
                return True
        return False


class _HostLoopDaytonaRuntime:
    """Own the host-managed iterative Daytona REPL loop for one node run."""

    def __init__(
        self,
        *,
        runner: DaytonaRLMRunner,
        session: DaytonaSandboxSession,
        repo: str | None,
        ref: str | None,
        task: str,
        conversation_history: list[dict[str, str]] | None = None,
        parent_id: str | None = None,
        depth: int = 0,
        node_id: str | None = None,
        strict_finalization: bool = True,
    ) -> None:
        self.runner = runner
        self.session = session
        self.repo = repo or ""
        self.ref = ref
        self.task = task
        self.conversation_history = _normalized_conversation_history(
            conversation_history
        )
        self.budget = runner.budget
        self.run_id = runner.run_id
        self.request_id = uuid.uuid4().hex
        self.root_id = node_id or uuid.uuid4().hex
        self.parent_id = parent_id
        self.depth = depth
        self.strict_finalization = strict_finalization
        self.started_at = time.monotonic()
        self.primary_lm, self.delegate_lm = self.runner._ensure_lms()
        self.nodes: dict[str, AgentNode] = {}
        self.summary_warnings: list[str] = []
        self._history_grounding: str = ""
        self._history_grounding_resolved = False
        self._active_iteration: int | None = None
        self._trajectory_step_index = 0
        self._evaluation: dict[str, Any] = {"nodes": {}}
        self._event_emitter = DaytonaRuntimeEventEmitter(
            emit_runtime_event=self.runner._handle_runtime_event,
            session=self.session,
            budget=self.budget,
            run_id=self.run_id,
            request_id=self.request_id,
            started_at=self.started_at,
            active_iteration_getter=lambda: self._active_iteration,
            volume_name=self.runner.volume_name,
        )
        self._callback_dispatcher = DaytonaHostCallbackDispatcher(
            runner=cast(DaytonaRunnerProtocol, self.runner),
            task=self.task,
            event_emitter=self._event_emitter,
            active_iteration_getter=lambda: self._active_iteration,
            merge_child_result=lambda node, child_result, callback_name: (
                self._merge_child_result(
                    node=node,
                    child_result=child_result,
                    callback_name=callback_name,
                )
            ),
        )

    def _node_evaluation(self, node_id: str) -> dict[str, list[dict[str, Any]]]:
        nodes = self._evaluation.setdefault("nodes", {})
        return nodes.setdefault(node_id, _empty_node_evaluation())

    def _record_spawn_policy(
        self,
        *,
        node: AgentNode,
        should_spawn: bool,
        recommended_fanout: int,
        rationale: str,
        stage: str,
        observation_text: str,
    ) -> None:
        self._node_evaluation(node.node_id)["spawn_policy"].append(
            {
                "iteration": self._active_iteration,
                "stage": stage,
                "should_spawn": should_spawn,
                "recommended_fanout": recommended_fanout,
                "rationale": rationale,
                "observation_preview": _collapse_preview(observation_text, limit=280),
                "current_depth": node.depth,
                "remaining_sandboxes": self.runner.remaining_sandboxes(),
            }
        )

    def _record_decomposition(
        self,
        *,
        node: AgentNode,
        decision_summary: str,
        proposed_tasks: list[RecursiveTaskSpec],
        selected_tasks: list[RecursiveTaskSpec],
        error: str | None = None,
    ) -> None:
        self._node_evaluation(node.node_id)["decomposition"].append(
            {
                "iteration": self._active_iteration,
                "decision_summary": decision_summary,
                "proposed_task_count": len(proposed_tasks),
                "selected_task_count": len(selected_tasks),
                "tasks": [task.to_dict() for task in selected_tasks],
                "error": error,
            }
        )

    def _record_child_synthesis(
        self,
        *,
        node: AgentNode,
        child_result: ChildTaskResult,
        callback_name: str,
    ) -> None:
        self._node_evaluation(node.node_id)["child_synthesis"].append(
            {
                "iteration": self._active_iteration,
                "callback_name": callback_name,
                "child_id": child_result.child_id,
                "task": child_result.task.to_dict(),
                "status": child_result.status,
                "confidence": child_result.confidence,
                "follow_up_needed": child_result.follow_up_needed,
                "evidence_count": len(child_result.evidence),
                "result_preview": child_result.result_preview,
            }
        )

    def _create_root_node(self) -> AgentNode:
        root = AgentNode(
            node_id=self.root_id,
            parent_id=self.parent_id,
            depth=self.depth,
            task=self.task,
            repo=self.repo,
            ref=self.ref,
            context_sources=list(self.session.context_sources),
            sandbox_id=self.session.sandbox_id,
            workspace_path=self.session.workspace_path,
        )
        self.nodes[root.node_id] = root
        self._node_evaluation(root.node_id)
        return root

    def _emit_startup(self, root: AgentNode) -> None:
        self._emit_status(root, "Starting Daytona host-loop run.", phase="node_start")
        for warning in self.summary_warnings:
            root.warnings.append(warning)
            self._emit_warning(root, warning, phase="compatibility")

    def _build_static_prompts(self) -> tuple[str, str]:
        system_prompt = build_system_prompt(
            workspace_path=self.session.workspace_path,
            budget=self.budget,
        )
        user_prompt = build_user_prompt(
            repo=self.repo or None,
            ref=self.ref,
            context_sources=self.session.context_sources,
        )
        return system_prompt, user_prompt

    def _start_iteration(self, *, root: AgentNode, iteration: int) -> None:
        self._assert_not_cancelled()
        self._assert_time_budget()
        self._active_iteration = iteration
        root.iteration_count = iteration
        self._emit_progress_status(
            root,
            f"Running Daytona iteration {iteration}.",
            phase="iteration",
            iteration=iteration,
        )
        self._emit_trajectory_step(
            root,
            f"Starting Daytona iteration {iteration}.",
            phase="iteration",
            iteration=iteration,
            thought=f"Begin host-loop iteration {iteration}.",
            action=f"Iteration {iteration}",
        )

    def _invoke_iteration(
        self,
        *,
        root: AgentNode,
        system_prompt: str,
        user_prompt: str,
        observation_text: str,
        iteration: int,
    ) -> str:
        self._emit_progress_status(
            root,
            f"Preparing prompt for iteration {iteration}.",
            phase="prepare_prompt",
            iteration=iteration,
        )
        prompt = self._build_iteration_prompt(
            node=root,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task=self.task,
            observation_text=observation_text,
            iteration=iteration,
        )
        self._emit_reasoning_step(
            root,
            f"Planner prompt preview:\n\n{_preview_text(prompt, limit=1_000)}",
            phase="prepare_prompt",
            iteration=iteration,
            label=f"prompt_iter_{iteration}",
        )
        self._emit_progress_status(
            root,
            f"Invoking planner model for iteration {iteration}.",
            phase="llm_invoke",
            iteration=iteration,
        )
        response_text = self.runner._invoke_lm(self.primary_lm, prompt)
        root.prompt_previews.append(_preview_text(prompt))
        root.response_previews.append(_preview_text(response_text))
        self._emit_reasoning_step(
            root,
            f"Planner response preview:\n\n{_preview_text(response_text, limit=1_000)}",
            phase="llm_invoke",
            iteration=iteration,
            label=f"planner_iter_{iteration}",
        )
        self._emit_progress_status(
            root,
            f"Extracting Python code for iteration {iteration}.",
            phase="code_extract",
            iteration=iteration,
        )
        return response_text

    def _handle_missing_code_block(
        self,
        *,
        root: AgentNode,
        iteration: int,
        response_text: str,
    ) -> _IterationOutcome:
        message = (
            "The previous response did not include a Python code block. "
            "Reply with exactly one Python code block and finalize "
            "through SUBMIT(...)."
        )
        root.observations.append(
            ExecutionObservation(
                iteration=iteration,
                code="",
                error=message,
            )
        )
        self._emit_progress_status(
            root,
            f"Iteration {iteration} needs a retry because no Python code block was returned.",
            phase="retry",
            iteration=iteration,
        )
        self._emit_trajectory_step(
            root,
            f"Iteration {iteration} needs a retry because no Python code block was returned.",
            phase="retry",
            iteration=iteration,
            thought="The planner response did not contain executable Python.",
            action="Retry with code-block guidance",
            observation=message,
        )
        return _IterationOutcome(
            completed=False,
            observation_text=(
                f"{message}\n\nPrevious model response preview:\n"
                f"{_preview_text(response_text, limit=800)}"
            ),
            last_iteration=iteration,
        )

    def _execute_iteration_code(
        self,
        *,
        root: AgentNode,
        iteration: int,
        code: str,
    ) -> tuple[Any, ExecutionObservation]:
        self._emit_progress_status(
            root,
            f"Executing iteration {iteration} code in the Daytona sandbox.",
            phase="code_execute",
            iteration=iteration,
        )
        response = self.session.execute_code(
            code=code,
            callback_handler=lambda request: self._handle_host_callback(
                node=root,
                request=request,
            ),
            timeout=self._remaining_timeout(),
            submit_schema=_GUIDE_SUBMIT_SCHEMA,
            cancel_check=self.runner.cancel_check,
            progress_handler=lambda frame: self._handle_execution_progress(
                node=root,
                iteration=iteration,
                frame=frame,
            ),
        )
        self._emit_progress_status(
            root,
            f"Summarizing execution output for iteration {iteration}.",
            phase="observation_build",
            iteration=iteration,
            extra_payload={
                "duration_ms": response.duration_ms,
                "callback_count": response.callback_count,
            },
        )
        observation = ExecutionObservation(
            iteration=iteration,
            code=code,
            stdout=_preview_text(response.stdout),
            stderr=_preview_text(response.stderr),
            error=_preview_text(response.error) if response.error else None,
            duration_ms=response.duration_ms,
            callback_count=response.callback_count,
        )
        root.observations.append(observation)
        self._emit_reasoning_step(
            root,
            self._render_observation(observation),
            phase="observation",
            iteration=iteration,
            label=f"observation_iter_{iteration}",
            extra_payload={
                "duration_ms": response.duration_ms,
                "callback_count": response.callback_count,
            },
        )
        self._emit_trajectory_step(
            root,
            f"Executed Daytona sandbox code for iteration {iteration}.",
            phase="observation",
            iteration=iteration,
            thought=f"Sandbox execution completed for iteration {iteration}.",
            action="Execute sandbox code",
            observation=self._render_observation(observation),
            extra_payload={
                "duration_ms": response.duration_ms,
                "callback_count": response.callback_count,
            },
        )
        self._emit_progress_status(
            root,
            f"Iteration {iteration} executed in {response.duration_ms}ms.",
            phase="observation",
            iteration=iteration,
            extra_payload={
                "duration_ms": response.duration_ms,
                "callback_count": response.callback_count,
            },
        )
        return response, observation

    def _handle_final_artifact_response(
        self,
        *,
        root: AgentNode,
        iteration: int,
        response: Any,
        observation: ExecutionObservation,
    ) -> _IterationOutcome:
        artifact = FinalArtifact.from_raw(response.final_artifact)
        if (
            not self.strict_finalization
            or self.runner._root_finalization_candidate(artifact) is not None
        ):
            root.final_artifact = artifact
            root.status = "completed"
            self._emit_progress_status(
                root,
                "Node completed.",
                phase="completed",
                iteration=iteration,
                extra_payload={"duration_ms": response.duration_ms},
            )
            self._emit_trajectory_step(
                root,
                f"Completed Daytona iteration {iteration}.",
                phase="completed",
                iteration=iteration,
                thought="A final artifact was accepted for the root node.",
                action="Complete run",
                observation=self._build_result_preview(artifact),
                extra_payload={"duration_ms": response.duration_ms},
            )
            return _IterationOutcome(
                completed=True,
                observation_text="",
                last_iteration=iteration,
                final_artifact=artifact,
            )

        self._emit_progress_status(
            root,
            f"Iteration {iteration} produced an intermediate artifact; retrying with synthesis guidance.",
            phase="retry",
            iteration=iteration,
        )
        self._emit_trajectory_step(
            root,
            f"Iteration {iteration} produced an intermediate artifact; retrying with synthesis guidance.",
            phase="retry",
            iteration=iteration,
            thought="The returned artifact was not yet a readable synthesized answer.",
            action="Retry with synthesis guidance",
            observation=self._build_result_preview(artifact),
        )
        return _IterationOutcome(
            completed=False,
            observation_text=self._build_root_retry_observation(
                artifact=artifact,
                base_observation=(
                    self._render_observation(observation)
                    or "The previous SUBMIT result was rejected."
                ),
            ),
            last_iteration=iteration,
        )

    def _handle_execution_error_response(
        self,
        *,
        root: AgentNode,
        iteration: int,
        response: Any,
        observation: ExecutionObservation,
    ) -> _IterationOutcome:
        self._emit_progress_status(
            root,
            f"Iteration {iteration} returned an execution error; retrying with the captured observation.",
            phase="retry",
            iteration=iteration,
            extra_payload={"callback_count": response.callback_count},
        )
        self._emit_trajectory_step(
            root,
            f"Iteration {iteration} returned an execution error; retrying with the captured observation.",
            phase="retry",
            iteration=iteration,
            thought="The sandbox returned an execution error.",
            action="Retry after execution error",
            observation=self._render_observation(observation),
            extra_payload={"callback_count": response.callback_count},
        )
        return _IterationOutcome(
            completed=False,
            observation_text=self._render_observation(observation),
            last_iteration=iteration,
        )

    def _handle_observation_response(
        self,
        *,
        root: AgentNode,
        iteration: int,
        observation: ExecutionObservation,
    ) -> _IterationOutcome:
        observation_text = self._render_observation(observation)
        recursive_results = self._maybe_auto_decompose(
            node=root,
            observation_text=observation_text,
        )
        if recursive_results:
            self._emit_reasoning_step(
                root,
                f"Recursive child-result synthesis summary:\n\n{recursive_results}",
                phase="recursive_decompose",
                iteration=iteration,
                label=f"recursive_results_iter_{iteration}",
            )
            self._emit_trajectory_step(
                root,
                f"Merged recursive child results into iteration {iteration}.",
                phase="recursive_decompose",
                iteration=iteration,
                thought="Recursive children returned additional evidence for the parent node.",
                action="Merge recursive child results",
                observation=recursive_results,
            )
            observation_text = (
                f"{observation_text}\n\nRecursive child results:\n{recursive_results}"
            )
        return _IterationOutcome(
            completed=False,
            observation_text=observation_text,
            last_iteration=iteration,
        )

    def _resolve_iteration_outcome(
        self,
        *,
        root: AgentNode,
        iteration: int,
        response: Any,
        observation: ExecutionObservation,
    ) -> _IterationOutcome:
        if response.final_artifact is not None:
            return self._handle_final_artifact_response(
                root=root,
                iteration=iteration,
                response=response,
                observation=observation,
            )
        if response.error:
            return self._handle_execution_error_response(
                root=root,
                iteration=iteration,
                response=response,
                observation=observation,
            )
        return self._handle_observation_response(
            root=root,
            iteration=iteration,
            observation=observation,
        )

    def _run_iterations(
        self,
        *,
        root: AgentNode,
        system_prompt: str,
        user_prompt: str,
    ) -> _IterationOutcome:
        observation_text = "No execution has happened yet."

        for iteration in range(1, self.budget.max_iterations + 1):
            self._start_iteration(root=root, iteration=iteration)
            response_text = self._invoke_iteration(
                root=root,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                observation_text=observation_text,
                iteration=iteration,
            )
            code = self._extract_code(response_text)
            if code is None:
                observation_text = self._handle_missing_code_block(
                    root=root,
                    iteration=iteration,
                    response_text=response_text,
                ).observation_text
                continue
            self._emit_reasoning_step(
                root,
                f"```python\n{_preview_text(code, limit=1_000)}\n```",
                phase="code_extract",
                iteration=iteration,
                label=f"extracted_code_iter_{iteration}",
            )

            response, observation = self._execute_iteration_code(
                root=root,
                iteration=iteration,
                code=code,
            )
            outcome = self._resolve_iteration_outcome(
                root=root,
                iteration=iteration,
                response=response,
                observation=observation,
            )
            if outcome.completed:
                return outcome
            observation_text = outcome.observation_text

        return _IterationOutcome(
            completed=False,
            observation_text=observation_text,
            last_iteration=self.budget.max_iterations,
        )

    def _handle_max_iterations_exhausted(
        self,
        *,
        root: AgentNode,
        last_iteration: int,
    ) -> _RunTerminalState:
        error_text = (
            f"Exceeded max_iterations={self.budget.max_iterations} without SUBMIT()."
        )
        final_artifact = FinalArtifact(
            kind="error",
            value=error_text,
            finalization_mode="error",
        )
        root.status = "error"
        root.error = error_text
        root.final_artifact = final_artifact
        self._emit_progress_status(
            root,
            "Node failed.",
            phase="error",
            iteration=last_iteration,
        )
        self._emit_trajectory_step(
            root,
            "Node failed after exhausting the iteration budget.",
            phase="error",
            iteration=last_iteration,
            thought="The run never produced a terminal SUBMIT before max_iterations was reached.",
            action="Fail run",
            observation=error_text,
        )
        return _RunTerminalState(
            termination_reason="error",
            error_text=error_text,
            final_artifact=final_artifact,
        )

    def _handle_cancelled_exception(
        self,
        *,
        root: AgentNode,
        exc: DaytonaRunCancelled,
    ) -> _RunTerminalState:
        error_text = str(exc)
        root.status = "cancelled"
        root.error = error_text
        self._emit_cancelled(root, error_text, phase="cancelled")
        self._emit_trajectory_step(
            root,
            "Daytona run cancelled.",
            phase="cancelled",
            iteration=self._active_iteration,
            thought="The run was cancelled before completion.",
            action="Cancel run",
            observation=error_text,
        )
        return _RunTerminalState(
            termination_reason="cancelled",
            error_text=error_text,
        )

    def _handle_runtime_exception(
        self,
        *,
        root: AgentNode,
        exc: Exception,
        phase: str,
    ) -> _RunTerminalState:
        error_text = str(exc)
        final_artifact = FinalArtifact(
            kind="error",
            value=error_text,
            finalization_mode="error",
        )
        root.status = "error"
        root.error = error_text
        root.final_artifact = final_artifact
        self._emit_error(root, error_text, phase=phase)
        self._emit_trajectory_step(
            root,
            "Daytona run failed.",
            phase=phase,
            iteration=self._active_iteration,
            thought="The host-loop runtime raised an unrecoverable error.",
            action="Fail run",
            observation=error_text,
        )
        return _RunTerminalState(
            termination_reason="error",
            error_text=error_text,
            final_artifact=final_artifact,
        )

    def _build_run_result(self, state: _RunTerminalState) -> DaytonaRunResult:
        summary = RolloutSummary(
            duration_ms=int((time.monotonic() - self.started_at) * 1000),
            sandboxes_used=1,
            termination_reason=state.termination_reason,
            error=state.error_text,
            warnings=list(dict.fromkeys(self.summary_warnings)),
            phase_timings_ms=self._event_emitter.phase_timings_ms(),
        )
        return DaytonaRunResult(
            run_id=self.run_id,
            repo=self.repo,
            ref=self.ref,
            context_sources=list(self.session.context_sources),
            task=self.task,
            budget=self.budget,
            root_id=self.root_id,
            nodes=self.nodes,
            final_artifact=state.final_artifact,
            summary=summary,
            evaluation=self._evaluation,
        )

    def run(self) -> DaytonaRunResult:
        root = self._create_root_node()
        self._emit_startup(root)
        system_prompt, user_prompt = self._build_static_prompts()
        state = _RunTerminalState()

        try:
            outcome = self._run_iterations(
                root=root,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            if outcome.completed:
                state.final_artifact = outcome.final_artifact
            else:
                state = self._handle_max_iterations_exhausted(
                    root=root,
                    last_iteration=outcome.last_iteration,
                )
        except DaytonaRunCancelled as exc:
            state = self._handle_cancelled_exception(root=root, exc=exc)
        except TimeoutError as exc:
            state = self._handle_runtime_exception(
                root=root,
                exc=exc,
                phase="timeout",
            )
            raise
        except Exception as exc:
            state = self._handle_runtime_exception(
                root=root,
                exc=exc,
                phase="error",
            )
            raise
        finally:
            self._active_iteration = None

        return self._build_run_result(state)

    def _handle_host_callback(
        self, *, node: AgentNode, request: HostCallbackRequest
    ) -> HostCallbackResponse:
        return self._callback_dispatcher.handle(node=node, request=request)

    def _merge_child_result(
        self,
        *,
        node: AgentNode,
        child_result: ChildTaskResult,
        callback_name: str,
    ) -> None:
        if child_result.run_result is not None:
            self._merge_child_run_result(child_result.run_result)
        if child_result.child_id and child_result.child_id not in node.child_ids:
            node.child_ids.append(child_result.child_id)
        node.child_links.append(
            ChildLink(
                child_id=child_result.child_id,
                callback_name=callback_name,
                iteration=self._active_iteration,
                task=child_result.task,
                result_preview=child_result.result_preview,
                status=child_result.status,
            )
        )
        self._record_child_synthesis(
            node=node,
            child_result=child_result,
            callback_name=callback_name,
        )

    def _merge_child_run_result(self, child_result: DaytonaRunResult) -> None:
        for child_node_id, child_node in child_result.nodes.items():
            self.nodes[child_node_id] = child_node
        _merge_evaluation_maps(self._evaluation, child_result.evaluation)

    def _maybe_auto_decompose(self, *, node: AgentNode, observation_text: str) -> str:
        if node.depth >= self.budget.max_depth:
            self._record_spawn_policy(
                node=node,
                should_spawn=False,
                recommended_fanout=0,
                rationale="Current node already reached max_depth.",
                stage="guard",
                observation_text=observation_text,
            )
            return ""
        if self.runner.remaining_sandboxes() <= 0:
            self._record_spawn_policy(
                node=node,
                should_spawn=False,
                recommended_fanout=0,
                rationale="No sandbox budget remains for recursive children.",
                stage="guard",
                observation_text=observation_text,
            )
            return ""
        if not _collapse_plain_text(observation_text):
            self._record_spawn_policy(
                node=node,
                should_spawn=False,
                recommended_fanout=0,
                rationale="Observation was empty after normalization.",
                stage="guard",
                observation_text=observation_text,
            )
            return ""

        existing_specs = [link.task.to_dict() for link in node.child_links]
        budget_payload = {
            "current_depth": node.depth,
            "max_depth": self.budget.max_depth,
            "remaining_sandboxes": self.runner.remaining_sandboxes(),
            "batch_concurrency": self.budget.batch_concurrency,
        }
        try:
            with dspy.context(lm=self.delegate_lm):
                spawn_policy = self.runner._spawn_policy(
                    parent_task=self.task,
                    latest_observation=observation_text,
                    workspace_context_summary=build_user_prompt(
                        repo=self.repo or None,
                        ref=self.ref,
                        context_sources=self.session.context_sources,
                    ),
                    existing_child_tasks_json=json.dumps(
                        existing_specs, ensure_ascii=False
                    ),
                    budget_json=json.dumps(budget_payload, ensure_ascii=False),
                )
        except Exception as exc:
            self._record_spawn_policy(
                node=node,
                should_spawn=False,
                recommended_fanout=0,
                rationale=(
                    "Spawn policy failed; continuing without recursion "
                    f"({type(exc).__name__}: {exc})."
                ),
                stage="module_error",
                observation_text=observation_text,
            )
            return ""
        self._record_spawn_policy(
            node=node,
            should_spawn=spawn_policy.should_spawn,
            recommended_fanout=spawn_policy.recommended_fanout,
            rationale=spawn_policy.rationale,
            stage="module",
            observation_text=observation_text,
        )
        if not spawn_policy.should_spawn or spawn_policy.recommended_fanout <= 0:
            return ""

        try:
            with dspy.context(lm=self.delegate_lm):
                decision = self.runner._recursive_decomposer(
                    parent_task=self.task,
                    latest_observation=observation_text,
                    workspace_context_summary=build_user_prompt(
                        repo=self.repo or None,
                        ref=self.ref,
                        context_sources=self.session.context_sources,
                    ),
                    existing_child_tasks_json=json.dumps(
                        existing_specs, ensure_ascii=False
                    ),
                    budget_json=json.dumps(budget_payload, ensure_ascii=False),
                )
        except Exception as exc:
            self._record_decomposition(
                node=node,
                decision_summary="Recursive decomposition failed.",
                proposed_tasks=[],
                selected_tasks=[],
                error=f"{type(exc).__name__}: {exc}",
            )
            return ""

        existing_keys = {_task_spec_dedupe_key(link.task) for link in node.child_links}
        unique_specs: list[RecursiveTaskSpec] = []
        for task_spec in getattr(decision, "tasks", []) or []:
            key = _task_spec_dedupe_key(task_spec)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            unique_specs.append(task_spec)

        cap = max(
            0,
            min(
                spawn_policy.recommended_fanout,
                self.budget.batch_concurrency,
                self.runner.remaining_sandboxes(),
            ),
        )
        task_specs = unique_specs[:cap]
        self._record_decomposition(
            node=node,
            decision_summary=str(
                getattr(decision, "decision_summary", "") or ""
            ).strip(),
            proposed_tasks=list(getattr(decision, "tasks", []) or []),
            selected_tasks=task_specs,
        )
        if cap <= 0:
            return ""
        if not task_specs:
            return ""

        self._emit_progress_status(
            node,
            f"Spawning {len(task_specs)} recursive child tasks.",
            phase="recursive_decompose",
            iteration=self._active_iteration,
            extra_payload={"count": len(task_specs)},
        )
        self._emit_trajectory_step(
            node,
            f"Spawning {len(task_specs)} recursive child tasks.",
            phase="recursive_decompose",
            iteration=self._active_iteration,
            thought=str(getattr(decision, "decision_summary", "") or "").strip()
            or "The recursive decomposer selected child tasks to gather more evidence.",
            action="Spawn recursive child tasks",
            observation=[task.to_dict() for task in task_specs],
            extra_payload={"count": len(task_specs)},
        )
        try:
            child_results = self.runner._spawn_child_tasks_batched(
                parent_id=node.node_id,
                depth=node.depth,
                parent_task=self.task,
                task_specs=task_specs,
            )
        except Exception as exc:  # noqa: BLE001 - do not fail the parent outright
            warning = (
                "Automatic recursive decomposition failed; continuing without "
                f"child results ({type(exc).__name__}: {exc})."
            )
            node.warnings.append(warning)
            self.summary_warnings.append(warning)
            self._emit_warning(node, warning, phase="recursive_decompose")
            return ""
        for child_result in child_results:
            self._merge_child_result(
                node=node,
                child_result=child_result,
                callback_name="recursive_auto",
            )
        return self._render_recursive_child_results(child_results)

    def _render_recursive_child_results(
        self, child_results: list[ChildTaskResult]
    ) -> str:
        lines: list[str] = []
        for index, child_result in enumerate(child_results, start=1):
            label = child_result.task.label or child_result.task.task
            lines.append(
                f"{index}. [{child_result.status}] {label}: "
                f"{_collapse_preview(child_result.text or child_result.result_preview, limit=800)}"
            )
            if child_result.confidence is not None:
                lines.append(f"   Confidence: {child_result.confidence:.2f}")
            if child_result.follow_up_needed:
                lines.append("   Follow-up needed: yes")
        return "\n".join(lines)

    def _build_iteration_prompt(
        self,
        *,
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
        history_grounding_section = self._conversation_grounding_section(node=node)
        task_section = self._externalized_prompt_section(
            node=node,
            title="Task",
            text=task,
            kind="task",
            label=f"node-{node.node_id}-task",
        )
        conversation_history_section = self._conversation_history_section(node=node)
        observation_section = self._externalized_prompt_section(
            node=node,
            title="Previous observation",
            text=observation_text,
            kind="observation",
            label=f"node-{node.node_id}-iteration-{iteration - 1}-observation",
        )
        manifest_section = ""
        if task_externalized or observation_externalized or node.prompt_handles:
            manifest = self._sync_prompt_manifest(node=node)
            manifest_section = self._render_prompt_manifest(manifest)
        sections = [
            system_prompt,
            user_prompt,
            f"Iteration: {iteration}",
            history_grounding_section,
            task_section,
            conversation_history_section,
            observation_section,
            manifest_section,
        ]
        return "\n\n".join(section for section in sections if section.strip())

    def _conversation_grounding_section(self, *, node: AgentNode) -> str:
        if not self.conversation_history:
            return ""
        grounding = self._resolve_history_grounding(node=node)
        if not grounding:
            return ""
        return f"Session grounding from DSPy history input:\n{grounding}"

    def _resolve_history_grounding(self, *, node: AgentNode) -> str:
        if self._history_grounding_resolved:
            return self._history_grounding

        self._history_grounding_resolved = True
        try:
            self._history_grounding = self.runner._ground_task_with_history(
                lm=self.primary_lm,
                task=self.task,
                conversation_history=self.conversation_history,
            )
        except Exception as exc:  # noqa: BLE001 - degrade gracefully for chat turns
            warning = (
                "DSPy conversation-history grounding failed; falling back to "
                f"sandbox prompt handles only ({type(exc).__name__}: {exc})."
            )
            if warning not in self.summary_warnings:
                self.summary_warnings.append(warning)
            if warning not in node.warnings:
                node.warnings.append(warning)
                self._emit_warning(node, warning, phase="history_grounding")
            self._history_grounding = ""
        return self._history_grounding

    def _conversation_history_section(self, *, node: AgentNode) -> str:
        if not self.conversation_history:
            return "Conversation history: none for this session yet."

        handle = next(
            (
                item
                for item in node.prompt_handles
                if item.kind == "conversation_history"
                and item.label == f"node-{node.node_id}-conversation-history"
            ),
            None,
        )
        payload_text = json.dumps(
            {
                "history_turns": len(self.conversation_history),
                "turns": self.conversation_history,
            },
            indent=2,
            ensure_ascii=False,
        )
        if handle is None:
            handle = self.session.store_prompt(
                text=payload_text,
                kind="conversation_history",
                label=f"node-{node.node_id}-conversation-history",
                timeout=self._remaining_timeout(),
            )
            self._record_prompt_handle(node=node, handle=handle)

        recent_turns = self.conversation_history[-3:]
        recap_lines: list[str] = []
        for turn in recent_turns:
            user_request = str(turn.get("user_request", "") or "").strip()
            assistant_response = str(turn.get("assistant_response", "") or "").strip()
            if user_request:
                recap_lines.append(f"- User: {user_request}")
            if assistant_response:
                recap_lines.append(f"  Assistant: {assistant_response}")

        lines = [
            f"Conversation history: structured history is externalized as prompt handle `{handle.handle_id}`.",
            f"- kind: {handle.kind}",
            f"- label: {handle.label or 'none'}",
            f"- path: {handle.path}",
            f"- chars: {handle.char_count}",
            f"- lines: {handle.line_count}",
            f"- preview: {handle.preview or '[empty]'}",
            "Inspect it from executed Python with list_prompts() and read_prompt_slice(...); parse the JSON if you need exact turn-by-turn details in code.",
        ]
        if recap_lines:
            lines.append("Recent conversation recap:")
            lines.extend(recap_lines)
        return "\n".join(lines)

    def _externalized_prompt_section(
        self,
        *,
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
            handle = self.session.store_prompt(
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

    def _sync_prompt_manifest(self, *, node: AgentNode) -> PromptManifest:
        manifest = self.session.list_prompts(timeout=self._remaining_timeout())
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

    @staticmethod
    def _extract_code(response_text: str) -> str | None:
        lines = str(response_text or "").splitlines()
        opening_index: int | None = None

        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("```"):
                continue
            language = stripped[3:].strip().lower()
            if language in {"", "python", "py"}:
                opening_index = index
                break

        if opening_index is not None:
            for closing_index in range(len(lines) - 1, opening_index, -1):
                if lines[closing_index].strip() != "```":
                    continue
                code = "\n".join(lines[opening_index + 1 : closing_index]).strip()
                if code:
                    return code

        match = _CODE_BLOCK_RE.search(response_text)
        if match is None:
            return None
        return match.group(1).strip()

    def _render_observation(self, observation: ExecutionObservation) -> str:
        chunks = [f"Duration: {observation.duration_ms}ms"]
        if observation.callback_count:
            chunks.append(f"Host callback calls: {observation.callback_count}")
        if observation.stdout.strip():
            chunks.append(f"STDOUT:\n{observation.stdout.strip()}")
        if observation.stderr.strip():
            chunks.append(f"STDERR:\n{observation.stderr.strip()}")
        if observation.error:
            chunks.append(f"ERROR:\n{observation.error}")
        return "\n\n".join(chunks)

    def _build_root_retry_observation(
        self, *, artifact: FinalArtifact, base_observation: str
    ) -> str:
        preview = self._build_result_preview(artifact)
        return (
            f"{base_observation}\n\n"
            "Your previous SUBMIT produced raw intermediate data instead of a "
            "human-readable synthesized answer. Reuse the evidence already "
            "stored in Python variables, summarize the findings in concise "
            "markdown prose, and finalize with SUBMIT(summary=...) or "
            "SUBMIT(final_markdown=...).\n\n"
            f"Rejected final preview: {preview or '[empty final output]'}"
        )

    def _build_result_preview(
        self, artifact: FinalArtifact | None, *, fallback_text: str = ""
    ) -> str:
        if artifact is not None:
            candidate = self.runner._extract_synthesized_text(artifact.value)
            if candidate is not None:
                return _collapse_preview(candidate, limit=280)
            rendered = self._textual_render_for_result(artifact.value)
            if rendered:
                return _collapse_preview(rendered, limit=280)
        return _collapse_preview(fallback_text, limit=280)

    def _handle_execution_progress(
        self,
        *,
        node: AgentNode,
        iteration: int,
        frame: ExecutionEventFrame,
    ) -> None:
        text = str(frame.text or "")
        if not text.strip():
            return
        preview = _collapse_preview(text, limit=280)
        bounded_text = _preview_text(text, limit=1_200)
        stream_name = frame.stream.lower() if frame.stream else "stdout"
        self._emit_progress_status(
            node,
            f"Sandbox {stream_name}: {preview}",
            phase="sandbox_output",
            iteration=iteration,
            extra_payload={
                "stream": stream_name,
                "stream_text": bounded_text,
                "truncated": frame.truncated or bounded_text != text.strip(),
            },
        )

    def _next_trajectory_step_index(self) -> int:
        index = self._trajectory_step_index
        self._trajectory_step_index += 1
        return index

    def _emit_reasoning_step(
        self,
        node: AgentNode,
        text: str,
        *,
        phase: str,
        iteration: int | None = None,
        label: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        content = str(text or "").strip()
        if not content:
            return
        self._event_emitter.emit_reasoning_step(
            node,
            content,
            phase=phase,
            iteration=iteration,
            label=label,
            extra_payload=extra_payload,
        )

    def _emit_trajectory_step(
        self,
        node: AgentNode,
        text: str,
        *,
        phase: str,
        iteration: int | None = None,
        thought: str | None = None,
        action: str | None = None,
        tool_name: str | None = None,
        tool_input: Any | None = None,
        observation: Any | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        self._event_emitter.emit_trajectory_step(
            node,
            text,
            phase=phase,
            step_index=self._next_trajectory_step_index(),
            iteration=iteration,
            thought=thought,
            action=action,
            tool_name=tool_name,
            tool_input=tool_input,
            observation=observation,
            extra_payload=extra_payload,
        )

    @staticmethod
    def _textual_render_for_result(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, indent=2, ensure_ascii=False)
        except Exception:
            return str(value)

    def _assert_not_cancelled(self) -> None:
        if self.runner.cancel_check is not None and self.runner.cancel_check():
            raise DaytonaRunCancelled("Request cancelled.")

    def _assert_time_budget(self) -> None:
        if time.monotonic() - self.started_at > self.budget.global_timeout:
            raise TimeoutError(
                f"Global timeout exceeded ({self.budget.global_timeout}s)."
            )

    def _remaining_timeout(self) -> float:
        remaining = self.budget.global_timeout - (time.monotonic() - self.started_at)
        if remaining <= 0:
            raise TimeoutError(
                f"Global timeout exceeded ({self.budget.global_timeout}s)."
            )
        return max(1.0, remaining)

    def _emit_status(self, node: AgentNode, text: str, *, phase: str) -> None:
        self._event_emitter.emit_status(node, text, phase=phase)

    def _emit_progress_status(
        self,
        node: AgentNode,
        text: str,
        *,
        phase: str,
        iteration: int | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        self._event_emitter.emit_progress_status(
            node,
            text,
            phase=phase,
            iteration=iteration,
            extra_payload=extra_payload,
        )

    def _emit_warning(self, node: AgentNode, text: str, *, phase: str) -> None:
        self._event_emitter.emit_warning(node, text, phase=phase)

    def _emit_error(self, node: AgentNode, text: str, *, phase: str) -> None:
        self._event_emitter.emit_error(node, text, phase=phase)

    def _emit_cancelled(self, node: AgentNode, text: str, *, phase: str) -> None:
        self._event_emitter.emit_cancelled(node, text, phase=phase)

    def _emit_tool_call(
        self, node: AgentNode, callback_name: str, tool_input: dict[str, Any]
    ) -> None:
        self._event_emitter.emit_tool_call(node, callback_name, tool_input)

    def _emit_tool_result(
        self,
        node: AgentNode,
        callback_name: str,
        value: dict[str, Any],
        *,
        tool_input: dict[str, Any] | None = None,
    ) -> None:
        self._event_emitter.emit_tool_result(
            node,
            callback_name,
            value,
            tool_input=tool_input,
        )


def run_daytona_rlm_pilot(
    *,
    repo: str | None,
    task: str,
    ref: str | None = None,
    context_paths: list[str] | None = None,
    budget: RolloutBudget | None = None,
    output_dir: Path | str = "results/daytona-rlm",
    runtime: Any | None = None,
    lm: Any | None = None,
) -> DaytonaRunResult:
    """Run the experimental Daytona-backed RLM pilot."""

    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=runtime,
        budget=budget,
        output_dir=output_dir,
    )
    return runner.run(repo=repo, task=task, ref=ref, context_paths=context_paths)
