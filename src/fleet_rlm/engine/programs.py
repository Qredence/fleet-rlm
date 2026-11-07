"""The Turn program: route selection, then a direct answer, a Flex pipeline, or a primary RLM."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import dspy
from dspy.primitives.facade import SandboxLM

from fleet_rlm.contracts import (
    TRIAGE_CRITERIA,
    OptimizationInputs,
    Route,
)
from fleet_rlm.engine.children import ChildAgents, turn_is_over
from fleet_rlm.engine.decisions import preload_skills, select_route
from fleet_rlm.engine.signatures import DirectAnswer, FleetTask, SkillRelevance, TurnTriage
from fleet_rlm.observability import turn_callbacks
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
# A Flex baseline is dspy.RLM with DSPy's defaults (20 iterations, 50 sub-LLM calls). The rlm route
# uses the Turn's own budgets, so the flex route gets the same ones.
_FLEX_MAX_ITERS = 16
# Ceiling on the workspace bytes a direct-mode Turn folds into its single prompt. Hitting it is
# recorded as `direct_context_truncated`, so a benchmark can refuse a clipped arm instead of
# silently scoring an answer produced from half the context.
_MAX_DIRECT_CONTEXT_CHARS = 1_000_000
# Workspace content is data, not instructions: delimit it so an inlined file cannot pose as the task.
# The block precedes the task because the questions Fleet is asked about workspace data refer to it
# as already supplied ("in the above data"), and a question must follow the material it asks about.
_DIRECT_CONTEXT_HEADER = "<workspace_files>\n"
_DIRECT_CONTEXT_FOOTER = "\n</workspace_files>\n\n"


class CompiledProgramFile:
    """The one path from a compiled-program file to a live engine: bounded, atomic, rolled back on failure.

    Accepts artifacts saved from the engine itself and from the IsolatedFleetProgram wrapper
    (whose state keys carry a ``program.`` prefix).
    """

    MAX_BYTES = 10_000_000

    def __init__(self, path: str | None) -> None:
        """Record the artifact path before any file is read or applied."""
        self.path = path
        self.digest = ""
        self._stat: tuple[int, int] | None = None

    def load_initial(self, program: dspy.Module) -> None:
        """Apply the artifact at startup when it exists; a missing file keeps the program unchanged."""
        if self.path and os.path.lexists(self.path):
            self._apply(program, self._read())

    def reload_if_changed(self, program: dspy.Module) -> None:
        """Reapply the artifact only when its mtime or size has changed since the last load."""
        if not self.path or not os.path.lexists(self.path):
            return
        stat = self._regular_stat(self.path)
        if (stat.st_mtime_ns, stat.st_size) != self._stat:
            self._apply(program, self._read())

    def _read(self) -> bytes:
        """Read the configured regular file, rejecting sizes over MAX_BYTES with ValueError.

        Invalid paths raise ValueError; filesystem errors propagate.
        """
        assert self.path is not None
        if self._regular_stat(self.path).st_size > self.MAX_BYTES:
            raise ValueError("Compiled program exceeds the 10 MB size limit")
        return Path(self.path).read_bytes()

    @staticmethod
    def _regular_stat(path: str) -> os.stat_result:
        """Return file metadata, rejecting symlinks and non-files with ValueError."""
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError("Compiled program path must be a regular file")
        return os.stat(path)

    def _apply(self, program: dspy.Module, content: bytes) -> None:
        """Apply JSON program state, retaining existing state for omitted predictors.

        Accept wrapped ``program.`` keys. Track the digest and, when the file exists,
        its metadata after loading. JSON that is not an object raises ValueError;
        JSON decoding, DSPy loading, and filesystem errors propagate.
        """
        state = json.loads(content)
        if not isinstance(state, dict):
            raise ValueError("Compiled program must be a JSON object")
        if state and all(key.startswith("program.") or key == "metadata" for key in state):
            state = {key.removeprefix("program."): value for key, value in state.items()}
        # A program saved before a predictor existed keeps that predictor's current state.
        state = {**program.dump_state(), **state}
        program.load_state(state)  # DSPy trial-loads a copy first, so a bad artifact leaves `program` unchanged.
        if self.path and os.path.lexists(self.path):
            stat = self._regular_stat(self.path)
            self._stat = (stat.st_mtime_ns, stat.st_size)
        self.digest = hashlib.sha256(content).hexdigest()


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
        root_adapter: dspy.Adapter | None = None,
        sub_adapter: dspy.Adapter | None = None,
    ) -> None:
        """Build the triage, direct, Flex, and RLM programs, then load any compiled artifact."""
        super().__init__()
        self.sandbox_mgr = sandbox_mgr
        self.root_lm = root_lm
        self.sub_lm = sub_lm
        self.jev_lm = jev_lm
        self.compiled_path = compiled_path
        if execution_mode not in {"direct", "flex", "rlm", "hybrid"}:
            raise ValueError("Invalid execution mode")
        self.execution_mode = execution_mode
        self.root_adapter = root_adapter if root_adapter is not None else dspy.ChatAdapter()
        self.sub_adapter = sub_adapter if sub_adapter is not None else dspy.ChatAdapter()
        sandbox_mgr.sub_adapter = self.sub_adapter
        self._metered_root = SandboxLM(root_lm, sandbox_mgr.consume_budget)
        self._metered_sub = SandboxLM(sub_lm, sandbox_mgr.consume_budget)
        self.triage = dspy.Predict(TurnTriage)
        self.triage.set_criteria("route", TRIAGE_CRITERIA)
        self.direct_cot = dspy.ChainOfThought(DirectAnswer)
        self.skill_selector = dspy.Predict(SkillRelevance)

        self.children = ChildAgents(sandbox_mgr, self._metered_sub, self.sub_adapter)
        tools = self.children.tools()
        toolset = {tool.__name__: tool for tool in tools}
        self.flex_agent = dspy.Flex(
            FleetTask,
            tools=tools,
            interpreter_factory=self.sandbox_mgr.interpreter_factory(
                tools=toolset,
                rlm_defaults={"max_iters": _FLEX_MAX_ITERS, "max_llm_calls": sandbox_mgr.max_llm_calls},
            ),
        )
        self.primary_rlm = dspy.RLM(
            FleetTask,
            tools=tools,
            sub_lm=self._metered_sub,
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
            max_iters=_FLEX_MAX_ITERS,
            max_llm_calls=sandbox_mgr.max_llm_calls,
        )
        self.compiled = CompiledProgramFile(compiled_path)
        # Set by the optimizer's per-example factory on disposable rollout engines.
        self.optimization: OptimizationInputs | None = None
        self.compiled.load_initial(self)

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
        """Run one Turn, attaching the execution evidence and turn usage that scoring reads."""
        with self.sandbox_mgr.turn_scope():
            with dspy.context(callbacks=turn_callbacks()):
                prediction = self._run_turn(
                    workspace_manifest, memory_summary, task, skills_catalog, session_history, context, execution_mode
                )
            # Replace any candidate-provided evidence with observations from the host manager.
            prediction["execution_evidence"] = self._execution_evidence()
            # The metric scores efficiency from these, so evaluation and GEPA see the same numbers.
            for name, value in self.sandbox_mgr.turn_usage().items():
                prediction[name] = value
            return prediction

    def _execution_evidence(self) -> dict[str, Any]:
        """Collection failures mark evidence incomplete, which scores zero, instead of failing an answered Turn."""
        verifications: dict[str, Any] = {}
        for entry in self.optimization.verifications if self.optimization else ():
            try:
                verifications[entry["path"]] = self.sandbox_mgr.verify_python_artifact(
                    entry["path"], entry["timeout_seconds"]
                )
            except Exception as exc:
                # An unknown remote outcome must stay terminal; it must never be replayed.
                if isinstance(exc, ExecutionOutcomeUnknownError) or turn_is_over(exc):
                    raise
                _LOGGER.warning("Python artifact verification failed (%s)", type(exc).__name__)
                verifications[entry["path"]] = {"complete": False}
        try:
            evidence = self.sandbox_mgr.execution_evidence()
        except Exception as exc:
            if turn_is_over(exc):
                raise
            _LOGGER.warning("Execution evidence collection failed (%s)", type(exc).__name__)
            evidence = {
                "schema_version": 1,
                "artifacts": {"complete": False, "files": {}},
                "children": [],
                "children_complete": False,
            }
        return {**evidence, "python": verifications}

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
        """Execute the selected route, starting Turn budgets only when no Turn is active.

        Reloads an approved program if changed and checks selected dataset integrity.
        Raises ValueError for an invalid mode or changed dataset digest; deadline,
        cancellation, and execution errors propagate.
        """
        self.compiled.reload_if_changed(self)
        self.sandbox_mgr.check_turn()
        mode = execution_mode or self.execution_mode
        if mode not in {"direct", "flex", "rlm", "hybrid"}:
            raise ValueError("Invalid execution mode")
        if isinstance(context, WorkspaceDataset):
            metadata = self.sandbox_mgr.stat_workspace_file(context.workspace_path)
            if context.sha256 and metadata["sha256"] != context.sha256:
                raise ValueError("Dataset source changed; select the new revision")
        route = select_route(
            triage=self.triage,
            sandbox_mgr=self.sandbox_mgr,
            jev_lm=self.jev_lm,
            sub_lm=self.sub_lm,
            sub_adapter=self.sub_adapter,
            mode=mode,
            task=task,
            workspace_manifest=workspace_manifest,
            skills_catalog=skills_catalog,
            context=context,
        )
        skills_catalog = preload_skills(
            skill_selector=self.skill_selector,
            sandbox_mgr=self.sandbox_mgr,
            jev_lm=self.jev_lm,
            task=task,
            skills_catalog=skills_catalog,
            route=route,
        )
        return self._execute(
            route, workspace_manifest, memory_summary, task, skills_catalog, session_history, context, mode
        )

    def _inline_context(self, task: str, workspace_manifest: str) -> tuple[str, int, bool]:
        """Fold this Session's workspace files into the task for the context-free direct prompt.

        Return the prompt, the inlined character count, and whether the ceiling clipped it.
        Only ``files/`` is treated as dataset context: Skills and artifacts are Fleet bookkeeping,
        not task input. An empty manifest means no remote listing is issued at all, so a direct
        Turn with no workspace stays as cheap as it was before inlining existed. Unreadable files
        are skipped rather than failing the Turn.
        """
        # Only a manifest that actually names a files/ path can carry inlinable context. This keeps
        # a direct Turn with no workspace entirely local: no sandbox access, exactly as before.
        if "/workspace/files/" not in workspace_manifest:
            return task, 0, False
        paths = [
            path for path in self.sandbox_mgr.list_workspace_files() if path.startswith("files/") and path != "files"
        ]
        if not paths:
            return task, 0, False
        budget = _MAX_DIRECT_CONTEXT_CHARS
        blocks: list[str] = []
        inlined = 0
        truncated = False
        for path in paths:
            if truncated:
                break
            try:
                text = self.sandbox_mgr.read_workspace_file(path).decode("utf-8")
            except (OSError, ValueError, RuntimeError, UnicodeError):
                continue
            room = budget - inlined
            if room <= 0:
                truncated = True
                break
            if len(text) > room:
                text = text[:room]
                truncated = True
            blocks.append(f'<file path="/workspace/{path}">\n{text}\n</file>')
            inlined += len(text)
        if not blocks:
            return task, 0, truncated
        prompt = _DIRECT_CONTEXT_HEADER + "\n".join(blocks) + _DIRECT_CONTEXT_FOOTER + task
        return prompt, inlined, truncated

    def _execute(
        self,
        route: Route,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str,
        session_history: str,
        context: Any,
        mode: str,
    ) -> dspy.Prediction:
        if route is Route.DIRECT:
            inlined, chars, truncated = task, 0, False
            if mode == "direct":
                # Explicit direct mode carries workspace context in its single prompt.
                inlined, chars, truncated = self._inline_context(task, workspace_manifest)
                self.sandbox_mgr.emit("direct_context", chars=chars, truncated=truncated)
            with dspy.context(lm=self._metered_root, adapter=self.root_adapter):
                answer = self.direct_cot(
                    memory_summary=memory_summary,
                    session_history=session_history,
                    task=inlined,
                ).answer
            return dspy.Prediction(
                answer=answer,
                learned_skill_note="",
                route=route.value,
                direct_context_chars=chars,
                direct_context_truncated=truncated,
            )

        agent = self.flex_agent if route is Route.FLEX else self.primary_rlm
        if route is Route.FLEX and isinstance(context, WorkspaceDataset):
            context = context.flex_input()
        with dspy.context(lm=self._metered_root, adapter=self.root_adapter):
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
