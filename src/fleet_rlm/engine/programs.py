"""The Turn program: route selection, then a direct answer, a Flex pipeline, or a primary RLM."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ElementTree
from contextlib import nullcontext
from functools import partial
from typing import Any

import dspy
import mlflow
from mlflow.entities import SpanType

from fleet_rlm.dspy_support import (
    FleetRLMChatAdapter,
    MeteredLM,
    decision_evidence,
    without_mlflow_dspy_callback,
)
from fleet_rlm.engine.children import ChildAgents, turn_is_over
from fleet_rlm.engine.signatures import (
    TRIAGE_CRITERIA,
    DirectAnswer,
    FleetTask,
    Route,
    SkillRelevance,
    TurnTriage,
    parse_route,
)
from fleet_rlm.lm_usage import decision_usage, usage_callbacks
from fleet_rlm.observability import activity_text
from fleet_rlm.optimize.artifacts import CompiledProgramFile
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)
_MAX_SELECTABLE_SKILLS = 20
_MAX_PRELOADED_SKILLS = 3
_MAX_PRELOADED_SKILL_CHARS = 20_000


def _catalog_entries(skills_catalog: str) -> list[tuple[str, str]]:
    """(name, description) pairs from the Stage-1 catalog; empty when it lists no Skills or is malformed."""
    try:
        root = ElementTree.fromstring(skills_catalog)
    except ElementTree.ParseError:
        return []
    return [(item.get("name") or "", (item.text or "").strip()) for item in root.iter("skill") if item.get("name")]


def _safe_route_error(exc: Exception, route_lm: Any) -> str:
    """Keep useful route errors in logs without echoing the active credential."""
    detail = " ".join(str(exc).split())
    api_key = getattr(route_lm, "api_key", None)
    if isinstance(api_key, str) and api_key:
        detail = detail.replace(api_key, "[redacted]")
    return detail[:500] or "no exception message"


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
        self.optimization_context: WorkspaceDataset | None = None
        self._metered_root = MeteredLM(root_lm, sandbox_mgr.consume_budget)
        self._metered_sub = MeteredLM(sub_lm, sandbox_mgr.consume_budget)
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
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
        )
        self.primary_rlm = dspy.RLM(
            FleetTask,
            tools=tools,
            sub_lm=self._metered_sub,
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools=toolset),
            max_iters=16,
            max_llm_calls=sandbox_mgr.max_llm_calls,
        )
        self.compiled = CompiledProgramFile(compiled_path)
        # Set by the optimizer's per-example factory; IsolatedFleetProgram deletes it after the rollout.
        self.optimization_artifact_path: str | None = None
        self.optimization_verifications: list[dict[str, Any]] = []
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
        owns_turn = not self.sandbox_mgr.turn_active
        try:
            with dspy.context(callbacks=usage_callbacks(list(dspy.settings.callbacks))):
                prediction = self._run_turn(
                    workspace_manifest, memory_summary, task, skills_catalog, session_history, context, execution_mode
                )
            # Replace any candidate-provided evidence with observations from the host manager.
            prediction["execution_evidence"] = self._execution_evidence()
            # The metric scores efficiency from these, so evaluation and GEPA see the same numbers.
            for name, value in self.sandbox_mgr.turn_usage().items():
                prediction[name] = value
            return prediction
        finally:
            if owns_turn:
                self.sandbox_mgr.end_turn()

    def _execution_evidence(self) -> dict[str, Any]:
        """Collection failures mark evidence incomplete, which scores zero, instead of failing an answered Turn."""
        verifications: dict[str, Any] = {}
        for entry in self.optimization_verifications:
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
        if not self.sandbox_mgr.turn_active:
            # Only the Turn's owner initializes the shared budgets; a Turn already started by
            # the API stream (including a deadline-free one) must not be reset a second time.
            self.sandbox_mgr.reset_turn_budget()
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
        skills = _catalog_entries(skills_catalog)[:_MAX_SELECTABLE_SKILLS]
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
            callbacks = without_mlflow_dspy_callback(list(dspy.settings.callbacks))
            with mlflow.start_span(name="typesafe_skill_selection", span_type=SpanType.LLM) as span:
                span.set_inputs({"task": activity_text(task), "skills": [name for name, _ in skills]})
                with dspy.context(lm=self.jev_lm, callbacks=callbacks), decision_usage(span):
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
        route_lm = self.jev_lm or MeteredLM(self.sub_lm, partial(self.sandbox_mgr.consume_budget, category="routing"))
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
                callbacks = (
                    without_mlflow_dspy_callback(list(dspy.settings.callbacks))
                    if self.jev_lm is not None
                    else list(dspy.settings.callbacks)
                )
                # A generative backend has its own LM child span; do not count the wrapper twice.
                with mlflow.start_span(
                    name="typesafe_route_selection",
                    span_type=SpanType.LLM if self.jev_lm is not None else SpanType.CHAIN,
                ) as span:
                    span.set_inputs({name: activity_text(value) for name, value in triage_inputs.items()})
                    with (
                        dspy.context(lm=route_lm, callbacks=callbacks),
                        decision_usage(span) if self.jev_lm is not None else nullcontext(),
                    ):
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
