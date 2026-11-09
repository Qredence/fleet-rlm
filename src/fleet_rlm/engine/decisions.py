"""Route and optional Skill coordination using the engine's existing predictors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import dspy
import mlflow
from mlflow.entities import SpanType

from fleet_rlm.contracts import (
    ROUTE_SUMMARIES,
    TRIAGE_CRITERIA,
    Route,
    catalog_entries,
    decision_evidence,
    parse_route,
    turn_is_over,
)
from fleet_rlm.engine.signatures import SkillRelevance, TurnTriage
from fleet_rlm.observability import activity_text, evidence_span, typed_decision_scope

_LOGGER = logging.getLogger(__name__)
_MAX_SELECTABLE_SKILLS = 20
_MAX_PRELOADED_SKILLS = 3


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


def select_skills(
    *,
    skill_selector: dspy.Predict,
    consume_budget: Callable[..., Any],
    emit: Callable[..., Any],
    jev_lm: Any | None,
    task: str,
    skills_catalog: str,
    route: Route,
) -> list[str]:
    """Select bounded catalog identities; resolving their bytes remains a host capability."""
    skills = (catalog_entries(skills_catalog) or [])[:_MAX_SELECTABLE_SKILLS]
    if jev_lm is None or route is Route.DIRECT or not skills:
        return []
    try:
        consume_budget(len(skills), category="skill_selection")
        examples = [
            dspy.Example(task=task, skill_name=name, skill_description=description).with_inputs(
                "task", "skill_name", "skill_description"
            )
            for name, description in skills
        ]
        with evidence_span(
            "typesafe_skill_selection", inputs={"task": activity_text(task), "skills": [name for name, _ in skills]}
        ) as span:
            with typed_decision_scope(lm=jev_lm, span=span, typesafe=True):
                # Decisions are prepaid; disable DSPy's speculative straggler resubmission.
                predictions = skill_selector.batch(examples, num_threads=min(8, len(examples)), timeout=0)
            decisions = []
            for (name, _), prediction in zip(skills, predictions, strict=True):
                decision = getattr(prediction, "relevance", None)
                decisions.append(
                    {"skill": name, "decision": getattr(decision, "value", None)} | decision_evidence(decision, jev_lm)
                )
            selected = [name for (name, _), row in zip(skills, decisions, strict=True) if row["decision"] == "relevant"]
            selected = selected[:_MAX_PRELOADED_SKILLS]
            span.set_outputs({"selected": selected, "decisions": decisions})
    except Exception as exc:
        if turn_is_over(exc):
            raise
        _LOGGER.warning("Optional Skill selection failed (%s); using the catalog only", type(exc).__name__)
        return []
    emit("skill_selection", selected=selected, decisions=decisions)
    return selected


def select_route(
    *,
    triage: dspy.Predict,
    consume_budget: Callable[..., Any],
    emit: Callable[..., Any],
    jev_lm: Any | None,
    route_lm: Any,
    sub_adapter: dspy.Adapter,
    mode: str,
    task: str,
    workspace_manifest: str,
    skills_catalog: str,
    has_context: bool,
) -> Route:
    """Pick the route (session mode, or hybrid triage), record the evidence, and announce it."""
    route = Route.FLEX
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
            route = {"direct": Route.DIRECT, "rlm": Route.RLM}.get(mode, Route.FLEX)
            summary = f"Session mode {mode} selects this route without hybrid triage."
        else:
            if jev_lm is not None:
                consume_budget(category="routing")
            # A generative backend has its own LM child span; do not count the wrapper twice.
            with evidence_span(
                "typesafe_route_selection",
                span_type=SpanType.LLM if jev_lm is not None else SpanType.CHAIN,
                inputs={name: activity_text(value) for name, value in triage_inputs.items()},
            ) as span:
                with typed_decision_scope(lm=route_lm, span=span, typesafe=jev_lm is not None, adapter=sub_adapter):
                    decision = triage(**triage_inputs).route
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
            _safe_route_error(exc, route_lm),
        )
        route = Route.RLM
        reason = "routing_error"
        summary = f"Route selection failed ({type(exc).__name__}); falling back to bounded RLM execution."

    # Triage never routes a context-bearing task to the context-free direct prompt. An operator
    # who selects direct mode explicitly keeps it: the direct branch inlines the context.
    if has_context and route is Route.DIRECT and mode != "direct":
        reason = "selected_context"
        route = Route.RLM
        summary = "Context is selected; overriding the context-free direct route with bounded RLM execution."
    summary += " " + ROUTE_SUMMARIES[route.value]
    routing = {**evidence, "effective_route": route.value, "override_reason": reason}
    active_span = mlflow.get_current_active_span()
    if active_span is not None:
        active_span.set_attribute("fleet.routing", routing)
    emit("triage_decision", mode=mode, route=route.value, summary=summary, evidence=routing)
    return route


class Routing(dspy.Module):
    """Own native route prediction and effective-route policy, without workspace access."""

    def __init__(self, route_lm: Any, jev_lm: Any, adapter: dspy.Adapter, consume_budget: Any, emit: Any) -> None:
        super().__init__()
        self.predict = dspy.Predict(TurnTriage)
        self.predict.set_criteria("route", TRIAGE_CRITERIA)
        self._route_lm, self._jev_lm, self._adapter = route_lm, jev_lm, adapter
        self._consume_budget, self._emit = consume_budget, emit

    def forward(self, task: str, workspace_manifest: str, skills_catalog: str, mode: str, has_context: bool) -> Route:
        """Apply strict-mode bypass or native hybrid triage with host-enforced safeguards."""
        return select_route(
            triage=self.predict,
            consume_budget=self._consume_budget,
            emit=self._emit,
            jev_lm=self._jev_lm,
            route_lm=self._route_lm,
            sub_adapter=self._adapter,
            mode=mode,
            task=task,
            workspace_manifest=workspace_manifest,
            skills_catalog=skills_catalog,
            has_context=has_context,
        )


class SkillSelection(dspy.Module):
    """Own native batched relevance prediction over host-supplied catalog metadata."""

    def __init__(self, jev_lm: Any, consume_budget: Any, emit: Any) -> None:
        super().__init__()
        self.predict = dspy.Predict(SkillRelevance)
        self._jev_lm, self._consume_budget, self._emit = jev_lm, consume_budget, emit

    def forward(self, task: str, skills_catalog: str, route: Route) -> list[str]:
        """Select catalog identities without loading or activating their bytes."""
        return select_skills(
            skill_selector=self.predict,
            consume_budget=self._consume_budget,
            emit=self._emit,
            jev_lm=self._jev_lm,
            task=task,
            skills_catalog=skills_catalog,
            route=route,
        )
