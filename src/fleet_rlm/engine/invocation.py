"""Shared host construction, admission and invocation for serving and isolated rollouts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, cast

import dspy
from dspy.primitives.facade import SandboxLM

from fleet_rlm import skills as fleet_skills
from fleet_rlm.contracts import OptimizationInputs, RequiredSkill, Route, SkillAdmissionError, turn_is_over
from fleet_rlm.engine.children import ChildPolicies
from fleet_rlm.engine.compiled import CompiledProgramFile, installed_program_error
from fleet_rlm.engine.decisions import Routing, SkillSelection
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.engine.reasoning import Reasoning
from fleet_rlm.engine.signatures import ChildTask
from fleet_rlm.observability import turn_callbacks
from fleet_rlm.sandbox.delegation import ChildDelegation
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.skills import MAX_PRELOADED_SKILL_CHARS, MAX_PRELOADED_TOTAL_CHARS
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
_MAX_DIRECT_CONTEXT_CHARS = 1_000_000


def _active_turn_generation(manager: Any) -> int | None:
    if not getattr(manager, "turn_active", False):
        return None
    root = getattr(manager, "root", manager)
    generation = getattr(root, "_turn_generation", None)
    return generation if isinstance(generation, int) and generation > 0 else None


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
        installed_program: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        if execution_mode not in {"direct", "flex", "rlm", "hybrid"}:
            raise ValueError("Invalid execution mode")
        self.sandbox_mgr, self.root_lm, self.sub_lm, self.jev_lm = sandbox_mgr, root_lm, sub_lm, jev_lm
        self.compiled_path, self.execution_mode = compiled_path, execution_mode
        self.installed_program = installed_program
        self.root_adapter = (
            root_adapter if root_adapter is not None else dspy.XMLAdapter(use_native_function_calling=True)
        )
        self.sub_adapter = sub_adapter if sub_adapter is not None else dspy.XMLAdapter(use_native_function_calling=True)
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
            SkillSelection(jev_lm, sandbox_mgr.consume_budget, sandbox_mgr.emit, sandbox_mgr.read_workspace_file),
            Reasoning(
                root_lm=self._metered_root,
                sub_lm=self._metered_sub,
                adapter=self.root_adapter,
                tools=tools,
                rlm_factory=sandbox_mgr.interpreter_factory(tools=toolset),
                flex_factory=sandbox_mgr.interpreter_factory(tools=toolset),
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
        self._preview_turn_generation: int | None = None
        self.compiled.load_initial(self.program)
        self.delegation.bind_policies(self.program.child_policies.snapshot())

    def reload_program(self) -> None:
        """Pick up the global installed artifact before admission, refusing if the ledger contradicts it.

        Absent means baseline only while no global job was ever approved.
        """
        self.compiled.reload_if_changed(self.program)
        if self.installed_program is not None and (
            problem := installed_program_error(self.compiled.digest, self.installed_program())
        ):
            raise ValueError(problem)

    def __call__(
        self,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str = "<available_skills></available_skills>",
        session_history: str = "",
        context: Any = None,
        execution_mode: str | None = None,
        required_skills: tuple[RequiredSkill, ...] = (),
        selected_route: Route | None = None,
        rlm_history: dspy.History | None = None,
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
            required_skills=required_skills,
            selected_route=selected_route,
            rlm_history=rlm_history,
        )

    def preview_route(self, *, task: str, workspace_manifest: str, skills_catalog: str) -> Route:
        """Run the existing native router once when authoritative state proves the workspace empty."""
        self._preview_turn_generation = None
        self.reload_program()
        self.sandbox_mgr.check_turn()
        generation = _active_turn_generation(self.sandbox_mgr)
        with dspy.context(callbacks=turn_callbacks()):
            route = self.program.routing(
                task=task,
                workspace_manifest=workspace_manifest,
                skills_catalog=skills_catalog,
                mode=self.execution_mode,
                has_context=False,
                has_required_skills=False,
            )
        self._preview_turn_generation = generation
        return cast(Route, route)

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


def _resource_summary_from_paths(paths: Iterable[str]) -> dict[str, list[str]]:
    """Group skill-relative resource paths under scripts/, references/, and assets/, omitting empty folders."""
    grouped: dict[str, list[str]] = {}
    unique_paths = {path for path in paths if not fleet_skills._is_skill_bytecode_relpath(path)}
    for folder in ("scripts", "references", "assets"):
        prefix = folder + "/"
        entries = sorted(path for path in unique_paths if path.startswith(prefix))
        if entries:
            grouped[folder] = entries
    return grouped


def _local_skill_resources(skill_dir: Path) -> dict[str, list[str]]:
    """Collect regular non-symlink resource paths under a local bundled Skill directory."""
    if skill_dir.is_symlink() or not skill_dir.is_dir():
        return {}
    paths: list[str] = []
    for folder in ("scripts", "references", "assets"):
        root = skill_dir / folder
        if root.is_symlink() or not root.is_dir():
            continue
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                name for name in dirs if name != "__pycache__" and not os.path.islink(os.path.join(parent, name))
            )
            for filename in sorted(files):
                if filename.endswith((".pyc", ".pyo")):
                    continue
                candidate = Path(parent) / filename
                if not candidate.is_symlink() and candidate.is_file():
                    paths.append(candidate.relative_to(skill_dir).as_posix())
    return _resource_summary_from_paths(paths)


class WorkspaceInputs:
    """Bounded byte readers, with no reference back to the learned graph or invocation."""

    def __init__(self, sandbox_mgr: DaytonaSandboxManager) -> None:
        self.sandbox_mgr = sandbox_mgr

    def load_skills(self, catalog: str, selected: list[str], *, required_skills: tuple[RequiredSkill, ...] = ()) -> str:
        """Resolve selected metadata identities through the existing bounded host reader."""
        preloaded: list[str] = []
        remaining = MAX_PRELOADED_TOTAL_CHARS
        for skill in required_skills:
            resources = _resource_summary_from_paths(path for path, _ in skill.files)
            resources_suffix = (
                f"\nResources: {json.dumps(resources, ensure_ascii=False)}".replace("</preloaded_skill>", "")
                if resources
                else ""
            )
            remaining -= len(skill.skill_md) + len(resources_suffix)
            preloaded.append(
                f'<preloaded_skill name="{skill.name}">\n{skill.skill_md}{resources_suffix}\n</preloaded_skill>'
            )
        required_names = {skill.name for skill in required_skills}
        bundled_names = fleet_skills.bundled_skill_names(fleet_skills.BUNDLED_SKILLS_DIR)
        workspace_files: list[str] | None = None
        for name in [name for name in selected if name not in required_names][:3]:
            if remaining <= 0:
                break
            try:
                text = self.sandbox_mgr.read_workspace_file(f"skills/{name}/SKILL.md").decode("utf-8")
            except (OSError, ValueError, RuntimeError, UnicodeError):
                continue
            if name in bundled_names:
                resources = _local_skill_resources(fleet_skills.BUNDLED_SKILLS_DIR / name)
            else:
                if workspace_files is None:
                    if getattr(self.sandbox_mgr, "_sandbox", None) is not None:
                        try:
                            workspace_files = self.sandbox_mgr.list_workspace_files()
                        except (OSError, ValueError, RuntimeError) as exc:
                            _LOGGER.warning(
                                "Workspace Skill resource listing failed (%s); resources omitted", type(exc).__name__
                            )
                            workspace_files = []
                    else:
                        workspace_files = []
                prefix = f"skills/{name}/"
                resources = _resource_summary_from_paths(
                    path.removeprefix(prefix) for path in workspace_files if path.startswith(prefix)
                )
            resources_suffix = (
                f"\nResources: {json.dumps(resources, ensure_ascii=False)}".replace("</preloaded_skill>", "")
                if resources
                else ""
            )
            if resources_suffix and remaining > len(resources_suffix):
                body_limit = min(MAX_PRELOADED_SKILL_CHARS, remaining - len(resources_suffix))
                body = text[:body_limit].replace("</preloaded_skill>", "")
                remaining -= len(body) + len(resources_suffix)
            else:
                resources_suffix = ""
                body = text[: min(MAX_PRELOADED_SKILL_CHARS, remaining)].replace("</preloaded_skill>", "")
                remaining -= len(body)
            preloaded.append(f'<preloaded_skill name="{name}">\n{body}{resources_suffix}\n</preloaded_skill>')
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
    preview_generation = runtime._preview_turn_generation
    runtime._preview_turn_generation = None
    active_generation = _active_turn_generation(runtime.sandbox_mgr)
    if inputs.get("selected_route") is None or active_generation is None or preview_generation != active_generation:
        runtime.reload_program()
    source = "artifact" if runtime.compiled.digest else "baseline"
    manager = runtime.sandbox_mgr
    mode = inputs["execution_mode"]
    if mode not in {"direct", "flex", "rlm", "hybrid"}:
        raise ValueError("Invalid execution mode")
    required = inputs.get("required_skills", ())
    if required and mode == "direct":
        raise SkillAdmissionError("Direct mode cannot use required Skills; select Hybrid, RLM or Flex")
    with manager.turn_scope():
        manager.check_turn()
        for skill in required:
            for path, content in skill.files:
                if (
                    manager.stat_workspace_file(f"skills/{skill.name}/{path}")["sha256"]
                    != hashlib.sha256(content).hexdigest()
                ):
                    raise SkillAdmissionError("Required Skill files changed before execution")
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
