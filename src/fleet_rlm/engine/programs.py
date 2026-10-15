"""The Turn program: route selection, then a direct answer, a Flex pipeline, or a primary RLM."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import dspy
import mlflow
from dspy.primitives.facade import SandboxLM
from mlflow.entities import SpanType

from fleet_rlm.dspy_support import FleetRLMChatAdapter, decision_evidence
from fleet_rlm.dspy_support import without_mlflow_dspy_callback as _without_mlflow_dspy_callback
from fleet_rlm.engine.children import ChildAgents, turn_is_over
from fleet_rlm.engine.routes import Route, parse_route
from fleet_rlm.engine.signatures import (
    TRIAGE_CRITERIA,
    DirectAnswer,
    FleetTask,
    TurnTriage,
)
from fleet_rlm.observability import activity_text
from fleet_rlm.optimize.artifacts import CompiledProgramFile
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.workspace import WorkspaceDataset

_LOGGER = logging.getLogger(__name__)


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
        route = self._route(mode, task, workspace_manifest, skills_catalog, context)
        return self._execute(route, workspace_manifest, memory_summary, task, skills_catalog, session_history, context)

    def _route(self, mode: str, task: str, workspace_manifest: str, skills_catalog: str, context: Any) -> Route:
        """Pick the route (session mode, or hybrid triage), record the evidence, and announce it."""
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
