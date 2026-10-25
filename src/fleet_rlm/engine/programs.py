"""The Turn program: route selection, then a direct answer, a Flex pipeline, or a primary RLM."""

from __future__ import annotations

import ast
import logging
from functools import partial
from typing import Any, TypeGuard

import dspy
import mlflow
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
from fleet_rlm.lm_usage import turn_callbacks, typed_decision_scope
from fleet_rlm.metering import MeteredLM
from fleet_rlm.observability import activity_text, evidence_span
from fleet_rlm.program_file import CompiledProgramFile
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


def _is_dspy_rlm(node: ast.AST) -> TypeGuard[ast.Call]:
    """True for a `dspy.RLM(...)` call in generated Flex source."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "RLM"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dspy"
    )


def _with_turn_budgets(source: str, *, max_iters: int, max_llm_calls: int) -> str:
    """Add the Turn's RLM budgets to every `dspy.RLM(...)` call in generated Flex source.

    The rewrite is AST-driven, so nested signature text and quoting cannot confuse it, and a source
    that already sets a budget keeps its own value. Source that does not parse, or holds no RLM
    construction, is returned unchanged.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not _is_dspy_rlm(node):
            continue
        declared = {keyword.arg for keyword in node.keywords}
        additions = [
            f"{name}={value}"
            for name, value in (("max_iters", max_iters), ("max_llm_calls", max_llm_calls))
            if name not in declared
        ]
        if additions and node.end_lineno is not None and node.end_col_offset is not None:
            edits.append((node.end_lineno - 1, node.end_col_offset - 1, ", " + ", ".join(additions)))
    for line_index, column, insertion in sorted(edits, reverse=True):
        line = lines[line_index]
        lines[line_index] = line[:column] + insertion + line[column:]
    return "".join(lines)


def _bind_flex_budgets(flex: Any, *, max_iters: int, max_llm_calls: int) -> bool:
    """Apply the Turn's RLM budgets to a Flex module source; True when the source changed.

    DSPy's Flex bridge scopes the call-time LM, so a sandbox-built `dspy.RLM` has no `sub_lm` hook:
    its sub-queries run on the serving LM, metered, with DSPy's default call cap. The budgets are
    the part Fleet can control, and `_bind_code` is the only hook for them, so it stays here.
    """
    source = flex.module_src or ""
    rewritten = _with_turn_budgets(source, max_iters=max_iters, max_llm_calls=max_llm_calls)
    if not source or rewritten == source:
        return False
    flex._bind_code(rewritten)
    return True


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
            max_iters=_FLEX_MAX_ITERS,
            max_llm_calls=sandbox_mgr.max_llm_calls,
        )
        self.compiled = CompiledProgramFile(compiled_path)
        # Set by the optimizer's per-example factory; IsolatedFleetProgram clears it after the rollout.
        self.optimization: OptimizationInputs | None = None
        self.compiled.load_initial(self)
        self._apply_flex_budgets()

    def _apply_flex_budgets(self) -> None:
        """Give the Flex module source the Turn's RLM budgets after construction or an artifact reload."""
        _bind_flex_budgets(self.flex_agent, max_iters=_FLEX_MAX_ITERS, max_llm_calls=self.sandbox_mgr.max_llm_calls)

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
        # A reloaded artifact carries its own Flex source, so the budgets are re-applied here too.
        self._apply_flex_budgets()
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
