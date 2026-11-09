"""Shared host construction, admission and invocation for serving and isolated rollouts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

import dspy
from dspy.primitives.facade import SandboxLM

from fleet_rlm.contracts import OptimizationInputs, turn_is_over
from fleet_rlm.engine.children import ChildPolicies
from fleet_rlm.engine.compiled import CompiledProgramFile
from fleet_rlm.engine.decisions import Routing, SkillSelection
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.engine.reasoning import Reasoning
from fleet_rlm.engine.signatures import ChildTask
from fleet_rlm.observability import turn_callbacks
from fleet_rlm.sandbox.delegation import ChildDelegation
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
_MAX_DIRECT_CONTEXT_CHARS = 1_000_000
_MAX_PRELOADED_SKILL_CHARS = 20_000


class FleetRuntime:
    """Host bindings and selection; this object is not a learned DSPy module."""

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
        *,
        baseline: bool = False,
        reconcile_selection: Callable[[str], str] | None = None,
    ) -> None:
        if execution_mode not in {"direct", "flex", "rlm", "hybrid"}:
            raise ValueError("Invalid execution mode")
        self.sandbox_mgr, self.root_lm, self.sub_lm, self.jev_lm = sandbox_mgr, root_lm, sub_lm, jev_lm
        self.compiled_path, self.execution_mode = compiled_path, execution_mode
        self.reconcile_selection = reconcile_selection
        self.root_adapter = root_adapter if root_adapter is not None else dspy.ChatAdapter()
        self.sub_adapter = sub_adapter if sub_adapter is not None else dspy.ChatAdapter()
        sandbox_mgr.sub_adapter = self.sub_adapter
        self._metered_root = SandboxLM(root_lm, sandbox_mgr.consume_budget)
        self._metered_sub = SandboxLM(sub_lm, sandbox_mgr.consume_budget)
        self.delegation = ChildDelegation(sandbox_mgr, self._metered_sub, ChildTask, self.sub_adapter)
        self.workspace = WorkspaceInputs(sandbox_mgr)
        tools = self.delegation.tools()
        toolset = {tool.__name__: tool for tool in tools}
        self.program = FleetProgram(
            Routing(
                jev_lm or SandboxLM(sub_lm, partial(sandbox_mgr.consume_budget, category="routing")),
                jev_lm,
                self.sub_adapter,
                sandbox_mgr.consume_budget,
                sandbox_mgr.emit,
            ),
            SkillSelection(jev_lm, sandbox_mgr.consume_budget, sandbox_mgr.emit),
            Reasoning(
                root_lm=self._metered_root,
                sub_lm=self._metered_sub,
                adapter=self.root_adapter,
                tools=tools,
                rlm_factory=sandbox_mgr.interpreter_factory(tools=toolset),
                flex_factory=sandbox_mgr.interpreter_factory(
                    tools=toolset, rlm_defaults={"max_iters": 16, "max_llm_calls": sandbox_mgr.max_llm_calls}
                ),
                max_llm_calls=sandbox_mgr.max_llm_calls,
                inline_context=self.workspace.inline_context,
                emit=sandbox_mgr.emit,
                check_turn=sandbox_mgr.check_turn,
            ),
            ChildPolicies(self.delegation.spawn_agent, sandbox_mgr.interpreter_factory()),
            self.workspace.load_skills,
        )
        self.compiled = CompiledProgramFile(compiled_path, baseline=baseline)
        self.optimization: OptimizationInputs | None = None
        self.compiled.load_initial(self.program)
        self.delegation.bind_policies(self.program.child_policies.snapshot())

    def reload_program(self) -> None:
        """Reconcile durable selection before admission without persisting a stale baseline."""
        self.compiled.reload_if_changed(self.program)
        source = "artifact" if self.compiled.digest else "baseline"
        if self.reconcile_selection is not None:
            selected = self.reconcile_selection(source)
            if selected == "artifact" and source == "baseline":
                self.compiled.reload_if_changed(self.program, require_artifact=True)

    def __call__(
        self,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str = "<available_skills></available_skills>",
        session_history: str = "",
        context: Any = None,
        execution_mode: str | None = None,
    ) -> dspy.Prediction:
        """Use the same host path for ordinary Turns and reconstructed evaluation rollouts."""
        return invoke_program(
            self,
            workspace_manifest=workspace_manifest,
            memory_summary=memory_summary,
            task=task,
            skills_catalog=skills_catalog,
            session_history=session_history,
            context=context,
            execution_mode=execution_mode or self.execution_mode,
        )

    def execution_evidence(self) -> dict[str, Any]:
        """Only host receipts certify usage and artifacts; ordinary collection failures score incomplete."""
        verifications: dict[str, Any] = {}
        for entry in self.optimization.verifications if self.optimization else ():
            try:
                verifications[entry["path"]] = self.sandbox_mgr.verify_python_artifact(
                    entry["path"], entry["timeout_seconds"]
                )
            except Exception as exc:
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


class WorkspaceInputs:
    """Bounded byte readers, with no reference back to the learned graph or invocation."""

    def __init__(self, sandbox_mgr: DaytonaSandboxManager) -> None:
        self.sandbox_mgr = sandbox_mgr

    def load_skills(self, catalog: str, selected: list[str]) -> str:
        """Resolve selected metadata identities through the existing bounded host reader."""
        preloaded = []
        for name in selected:
            try:
                text = self.sandbox_mgr.read_workspace_file(f"skills/{name}/SKILL.md").decode("utf-8")
            except (OSError, ValueError, RuntimeError, UnicodeError):
                continue
            body = text[:_MAX_PRELOADED_SKILL_CHARS].replace("</preloaded_skill>", "")
            preloaded.append(f'<preloaded_skill name="{name}">\n{body}\n</preloaded_skill>')
        return "\n".join([catalog, *preloaded])

    def inline_context(self, task: str, workspace_manifest: str) -> tuple[str, int, bool]:
        """Read only files/ for explicit Direct, bounded and before the task, preserving clipping evidence."""
        if "/workspace/files/" not in workspace_manifest:
            return task, 0, False
        paths = [path for path in self.sandbox_mgr.list_workspace_files() if path.startswith("files/")]
        blocks: list[str] = []
        inlined, truncated = 0, False
        for path in paths:
            if truncated:
                break
            try:
                text = self.sandbox_mgr.read_workspace_file(path).decode("utf-8")
            except (OSError, ValueError, RuntimeError, UnicodeError):
                continue
            room = _MAX_DIRECT_CONTEXT_CHARS - inlined
            if room <= 0:
                truncated = True
                break
            if len(text) > room:
                text, truncated = text[:room], True
            blocks.append(f'<file path="/workspace/{path}">\n{text}\n</file>')
            inlined += len(text)
        prompt = "<workspace_files>\n" + "\n".join(blocks) + "\n</workspace_files>\n\n" + task if blocks else task
        return prompt, inlined, truncated


def invoke_program(runtime: FleetRuntime, **inputs: Any) -> dspy.Prediction:
    """Admit, invoke natively and certify once; outer owners retain persistence and teardown."""
    runtime.reload_program()
    source = "artifact" if runtime.compiled.digest else "baseline"
    manager = runtime.sandbox_mgr
    mode = inputs["execution_mode"]
    if mode not in {"direct", "flex", "rlm", "hybrid"}:
        raise ValueError("Invalid execution mode")
    with manager.turn_scope():
        manager.check_turn()
        context = inputs.get("context")
        if isinstance(context, WorkspaceDataset):
            metadata = manager.stat_workspace_file(context.workspace_path)
            if context.sha256 and metadata["sha256"] != context.sha256:
                raise ValueError("Dataset source changed; select the new revision")
        runtime.delegation.bind_policies(runtime.program.child_policies.snapshot())
        manager.emit("program_selection", source=source, digest=runtime.compiled.digest)
        with dspy.context(callbacks=turn_callbacks()):
            prediction = runtime.program(**inputs)
        prediction["execution_evidence"] = runtime.execution_evidence()
        for name, value in manager.turn_usage().items():
            prediction[name] = value
        return prediction
