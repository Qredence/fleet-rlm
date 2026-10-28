"""The Turn program: route selection, then a direct answer, a Flex pipeline, or a primary RLM."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import partial
from pathlib import Path
from typing import Any

import dspy
import mlflow
from dspy.primitives.facade import SandboxLM
from mlflow.entities import SpanType

from fleet_rlm.adapters import FleetRLMChatAdapter
from fleet_rlm.contracts import (
    ROUTE_SUMMARIES,
    TRIAGE_CRITERIA,
    OptimizationInputs,
    Route,
    catalog_entries,
    decision_evidence,
    parse_route,
)
from fleet_rlm.engine.children import ChildAgents, turn_is_over
from fleet_rlm.engine.signatures import DirectAnswer, FleetTask, SkillRelevance, TurnTriage
from fleet_rlm.observability import activity_text, evidence_span, turn_callbacks, typed_decision_scope
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
_MAX_SELECTABLE_SKILLS = 20
_MAX_PRELOADED_SKILLS = 3
_MAX_PRELOADED_SKILL_CHARS = 20_000
# A Flex baseline is dspy.RLM with DSPy's defaults (20 iterations, 50 sub-LLM calls). The rlm route
# uses the Turn's own budgets, so the flex route gets the same ones.
_FLEX_MAX_ITERS = 16


def _lm_api_key(lm: Any) -> str | None:
    """Find the credential on a DSPy LM, a sandbox proxy, or a TypeSafe client.

    `dspy.LM` keeps it in `kwargs`, the sandbox proxy exposes `kwargs` and `_lm`, and a TypeSafe
    client keeps an `api_key` attribute. The walk is id-guarded in case a proxy points at itself.
    """
    seen: set[int] = set()
    while lm is not None and id(lm) not in seen:
        seen.add(id(lm))
        for candidate in (getattr(lm, "api_key", None), (getattr(lm, "kwargs", None) or {}).get("api_key")):
            if isinstance(candidate, str) and candidate:
                return candidate
        lm = getattr(lm, "_lm", None)
    return None


def _safe_route_error(exc: Exception, route_lm: Any) -> str:
    """Keep useful route errors in logs without echoing the active credential."""
    detail = " ".join(str(exc).split())
    api_key = _lm_api_key(route_lm)
    if api_key:
        detail = detail.replace(api_key, "[redacted]")
    return detail[:500] or "no exception message"


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
    ) -> None:
        """Build the triage, direct, Flex, and RLM programs, then load any compiled artifact."""
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
        self.skill_selector = dspy.Predict(SkillRelevance)

        self.children = ChildAgents(sandbox_mgr, self._metered_sub)
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
        # Set by the optimizer's per-example factory; IsolatedFleetProgram clears it after the rollout.
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
        if mode not in {"rlm", "flex", "hybrid"}:
            raise ValueError("Invalid execution mode")
        if isinstance(context, WorkspaceDataset):
            metadata = self.sandbox_mgr.stat_workspace_file(context.workspace_path)
            if context.sha256 and metadata["sha256"] != context.sha256:
                raise ValueError("Dataset source changed; select the new revision")
        route = self._route(mode, task, workspace_manifest, skills_catalog, context)
        skills_catalog = self._select_skills(task, skills_catalog, route)
        return self._execute(route, workspace_manifest, memory_summary, task, skills_catalog, session_history, context)

    def _select_skills(self, task: str, skills_catalog: str, route: Route) -> str:
        """Preload the instructions of Skills the TypeSafe Jev marks relevant; other Skills stay in the catalog."""
        skills = (catalog_entries(skills_catalog) or [])[:_MAX_SELECTABLE_SKILLS]
        if self.jev_lm is None or route is Route.DIRECT or not skills:
            return skills_catalog
        try:
            self.sandbox_mgr.consume_budget(len(skills), category="skill_selection")
            examples = [
                dspy.Example(task=task, skill_name=name, skill_description=description).with_inputs(
                    "task", "skill_name", "skill_description"
                )
                for name, description in skills
            ]
            with evidence_span(
                "typesafe_skill_selection", inputs={"task": activity_text(task), "skills": [name for name, _ in skills]}
            ) as span:
                with typed_decision_scope(lm=self.jev_lm, span=span, typesafe=True):
                    predictions = self.skill_selector.batch(examples, num_threads=min(8, len(examples)))
                decisions = []
                for (name, _), prediction in zip(skills, predictions, strict=True):
                    decision = getattr(prediction, "relevance", None)
                    decisions.append(
                        {"skill": name, "decision": getattr(decision, "value", None)}
                        | decision_evidence(decision, self.jev_lm)
                    )
                selected = [row["skill"] for row in decisions if row["decision"] == "relevant"]
                selected = selected[:_MAX_PRELOADED_SKILLS]
                span.set_outputs({"selected": selected, "decisions": decisions})
        except Exception as exc:
            if turn_is_over(exc):
                raise
            _LOGGER.warning("Optional Skill selection failed (%s); using the catalog only", type(exc).__name__)
            return skills_catalog
        preloaded = []
        for name in selected:
            try:
                text = self.sandbox_mgr.read_workspace_file(f"skills/{name}/SKILL.md").decode("utf-8")
            except (OSError, ValueError, RuntimeError, UnicodeError):
                continue
            body = text[:_MAX_PRELOADED_SKILL_CHARS].replace("</preloaded_skill>", "")
            preloaded.append(f'<preloaded_skill name="{name}">\n{body}\n</preloaded_skill>')
        self.sandbox_mgr.emit("skill_selection", selected=selected, decisions=decisions)
        return "\n".join([skills_catalog, *preloaded])

    def _route(self, mode: str, task: str, workspace_manifest: str, skills_catalog: str, context: Any) -> Route:
        """Pick the route (session mode, or hybrid triage), record the evidence, and announce it."""
        route = Route.FLEX
        route_lm = self.jev_lm or SandboxLM(self.sub_lm, partial(self.sandbox_mgr.consume_budget, category="routing"))
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
                    self.sandbox_mgr.consume_budget(category="routing")
                # A generative backend has its own LM child span; do not count the wrapper twice.
                with evidence_span(
                    "typesafe_route_selection",
                    span_type=SpanType.LLM if self.jev_lm is not None else SpanType.CHAIN,
                    inputs={name: activity_text(value) for name, value in triage_inputs.items()},
                ) as span:
                    with typed_decision_scope(lm=route_lm, span=span, typesafe=self.jev_lm is not None):
                        decision = self.triage(**triage_inputs).route
                    evidence = decision_evidence(decision, route_lm)
                    # An unknown or missing label takes the bounded RLM path.
                    route = parse_route(decision) or Route.RLM
                    span.set_outputs({"route": route.value, **evidence})
        except Exception as exc:
            if turn_is_over(exc):
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
        summary += " " + ROUTE_SUMMARIES[route.value]
        routing = {**evidence, "effective_route": route.value, "override_reason": reason}
        active_span = mlflow.get_current_active_span()
        if active_span is not None:
            active_span.set_attribute("fleet.routing", routing)
        self.sandbox_mgr.emit("triage_decision", mode=mode, route=route.value, summary=summary, evidence=routing)
        return route

    def _execute(
        self,
        route: Route,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str,
        session_history: str,
        context: Any,
    ) -> dspy.Prediction:
        if route is Route.DIRECT:
            with dspy.context(lm=self._metered_root):
                answer = self.direct_cot(
                    memory_summary=memory_summary,
                    session_history=session_history,
                    task=task,
                ).answer
            return dspy.Prediction(answer=answer, learned_skill_note="", route=route.value)

        agent = self.flex_agent if route is Route.FLEX else self.primary_rlm
        if route is Route.FLEX and isinstance(context, WorkspaceDataset):
            context = context.flex_input()
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
