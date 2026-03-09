"""Experimental Daytona-backed strict-RLM runner."""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.core.config import get_planner_lm_from_env

from .diagnostics import as_diagnostic_error
from .protocol import HostCallbackRequest, HostCallbackResponse
from .results import persist_result
from .sandbox import DaytonaSandboxRuntime
from .spawn import rlm_query, rlm_query_batched
from .system_prompt import build_system_prompt, build_user_prompt
from .types import (
    AgentNode,
    DaytonaRunResult,
    ExecutionObservation,
    FinalArtifact,
    RolloutBudget,
    RolloutSummary,
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_PREVIEW_LIMIT = 1200


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


class DaytonaRLMRunner:
    """Run a strict-RLM style recursive rollout over Daytona sandboxes."""

    def __init__(
        self,
        *,
        lm: Any,
        runtime: DaytonaSandboxRuntime | Any | None = None,
        budget: RolloutBudget | None = None,
        output_dir: Path | str = "results/daytona-rlm",
    ) -> None:
        self.lm = lm
        self.runtime = runtime or DaytonaSandboxRuntime()
        self.budget = budget or RolloutBudget()
        self.output_dir = Path(output_dir)
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

    def run_child_task(self, *, parent_id: str, depth: int, task: str) -> str:
        if depth > self.budget.max_depth:
            raise RuntimeError(
                f"Recursive depth exceeded: {depth} > max_depth={self.budget.max_depth}"
            )

        parent = self._nodes[parent_id]
        artifact = self._run_agent(
            node_id=self._new_node_id(),
            parent_id=parent_id,
            depth=depth,
            repo=parent.repo,
            ref=parent.ref,
            task=task,
        )
        if artifact is None:
            return ""
        text = artifact.value
        if isinstance(text, (dict, list)):
            rendered = json.dumps(text, ensure_ascii=False)
        else:
            rendered = str(text)
        if len(rendered) > self.budget.result_truncation_limit:
            return (
                rendered[: self.budget.result_truncation_limit].rstrip()
                + "\n\n[truncated child result]"
            )
        return rendered

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

        system_prompt = build_system_prompt(
            repo_path=session.repo_path, budget=self.budget
        )
        user_prompt = build_user_prompt(task=task, repo=repo, ref=ref)
        observation_text = "No execution has happened yet."

        try:
            try:
                session.start_driver(timeout=self._remaining_timeout())
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
                self._assert_time_budget()
                prompt = (
                    f"{system_prompt}\n\n"
                    f"{user_prompt}\n\n"
                    f"Iteration: {iteration}\n"
                    f"Previous observation:\n{observation_text}\n"
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
                    node.final_artifact = fallback
                    node.status = "completed"
                    return fallback

                execution = session.execute_code(
                    code=code,
                    callback_handler=lambda request: self._handle_host_callback(
                        node=node,
                        request=request,
                    ),
                    timeout=self._remaining_timeout(),
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
                    node.final_artifact = artifact
                    node.status = "completed"
                    return artifact

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
                f"Exceeded max_iterations={self.budget.max_iterations} without FINAL()"
            )
            return FinalArtifact(
                kind="error",
                value=node.error,
                finalization_mode="error",
            )
        finally:
            with contextlib.suppress(Exception):
                session.close_driver(timeout=2.0)
            with contextlib.suppress(Exception):
                session.delete()

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
        try:
            if request.name == "rlm_query":
                value = rlm_query(
                    self,
                    parent_id=node.node_id,
                    depth=node.depth + 1,
                    task=str(request.payload["task"]),
                )
            elif request.name == "rlm_query_batched":
                tasks = [str(task) for task in request.payload.get("tasks", [])]
                value = rlm_query_batched(
                    self,
                    parent_id=node.node_id,
                    depth=node.depth + 1,
                    tasks=tasks,
                )
            else:
                raise RuntimeError(f"Unsupported host callback: {request.name}")
        except Exception as exc:
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
