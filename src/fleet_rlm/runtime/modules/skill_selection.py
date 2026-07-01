"""Skill selection module — proactively selects and injects relevant skills into context."""

from __future__ import annotations

import logging
import re
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import SkillSelectionSignature
from fleet_rlm.runtime.sandbox_types import ActiveSkills
from fleet_rlm.runtime.task_intent import has_url, has_url_document_intent
from fleet_rlm.runtime.tools.skill_tools import _load_skill_impl, discover_scaffold_skills

logger = logging.getLogger(__name__)

# Skill selection picks 1-2 skills from a keyword-filtered candidate set — a
# cheap classification, not a reasoning task. These caps stop the planner LM
# (e.g. qwen3.7-max with max_tokens=65536 and extended thinking) from turning a
# routing pick into a multi-minute call that retries past its 60s timeout.
# See: analyzing-mlflow-session trace tr-73ee45…/tr-d6ad21… (skill-selection
# LM.__call__ took 138s/140s per turn, ~70-80% of total turn latency).
_SKILL_SELECTION_MAX_TOKENS = 512
_SKILL_SELECTION_TEMPERATURE = 0.0
_SKILL_SELECTION_TIMEOUT_S = 30.0


# Keyword overrides for skills not fully captured by frontmatter alone.
_KEYWORD_OVERRIDES: dict[str, list[str]] = {
    "rlm": [
        "recursive language",
        "dspy.rlm",
        "variable mode",
        "variable-mode",
        "repl loop",
    ],
    "sandbox-execution": [
        "execute",
        "sandbox",
        "run code",
        "daytona",
        "interpreter",
        "SUBMIT",
        "volume",
        "persist",
        "workspace",
    ],
    "delegation": [
        "delegate",
        "child",
        "sub_rlm",
        "rlm_query",
        "batch",
        "fan-out",
        "fanout",
        "budget",
        "recursion",
    ],
    "dspy-programs": [
        "dspy",
        "signature",
        "module",
        "InputField",
        "OutputField",
        "registry",
        "execution mode",
        "escalat",
    ],
    "long-context": [
        "chunk",
        "long context",
        "large document",
        "map-reduce",
        "semantic chunk",
        "variable-mode",
        "codebase analysis",
    ],
    "browser-interaction": [
        "render the page",
        "rendered page",
        "javascript page",
        "spa content",
        "headless browser",
        "playwright",
        "dynamic page",
        "browser fetch",
        "screenshot the page",
        "interact with the page",
    ],
    "optimization": [
        "optimi",
        "GEPA",
        "scorer",
        "dataset",
        "mlflow",
        "compile",
    ],
    "diagnostics": [
        "diagnos",
        "troubleshoot",
        "contract drift",
        "broken sandbox",
        "runtime failure",
    ],
    "volume-bootstrap": [
        "volume init",
        "filesystem",
        "core.db",
        "remember",
        "recall",
        "memory db",
        "bootstrap",
        "directory structure",
    ],
}


def _build_keyword_map() -> dict[str, list[str]]:
    catalog = discover_scaffold_skills()
    keyword_map: dict[str, list[str]] = {}
    for skill_name in catalog:
        keywords = list(_KEYWORD_OVERRIDES.get(skill_name, []))
        normalized = skill_name.replace("-", " ")
        keywords.append(normalized)
        keyword_map[skill_name] = keywords
    for skill_name, keywords in _KEYWORD_OVERRIDES.items():
        if skill_name not in keyword_map:
            keyword_map[skill_name] = list(keywords)
    return keyword_map


AVAILABLE_SKILLS: dict[str, str] = discover_scaffold_skills()
_KEYWORD_MAP: dict[str, list[str]] = _build_keyword_map()


def _keyword_match(user_request: str) -> list[str]:
    """Fast keyword-based skill matching (zero LLM cost)."""
    request_lower = user_request.lower()
    scores: dict[str, int] = {}
    for skill_name, keywords in _KEYWORD_MAP.items():
        score = 0
        for kw in keywords:
            if kw.lower() in request_lower:
                score += 1
        if skill_name == "long-context" and has_url_document_intent(user_request):
            score += 2
        if score > 0:
            scores[skill_name] = score

    if not scores:
        return []

    max_score = max(scores.values())
    threshold = max(1, max_score - 1)
    return [name for name, score in scores.items() if score >= threshold]


def _routing_skill_hints(
    *,
    execution_mode: str,
    routing_decision: str | None,
    is_first_turn: bool,
    user_request: str = "",
) -> list[str]:
    hints: list[str] = []
    if routing_decision == "url_document_rlm":
        hints.append("long-context")
    if routing_decision == "large_context_rlm" or execution_mode in {"rlm", "rlm_only"}:
        hints.append("long-context")
    if is_first_turn:
        hints.append("rlm")
    if has_url(user_request) and "playwright" in user_request.lower():
        hints.append("browser-interaction")
    return hints


def _merge_skill_candidates(
    *,
    keyword_candidates: list[str],
    routing_hints: list[str],
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for name in [*routing_hints, *keyword_candidates]:
        if name not in AVAILABLE_SKILLS or name in seen:
            continue
        seen.add(name)
        merged.append(name)
    return merged


def select_skill_candidates(
    user_request: str,
    *,
    execution_mode: str = "auto",
    routing_decision: str | None = None,
    is_first_turn: bool = False,
) -> list[str]:
    """Return merged keyword and routing-hint skill candidates for a turn."""
    routing_hints = _routing_skill_hints(
        execution_mode=execution_mode,
        routing_decision=routing_decision,
        is_first_turn=is_first_turn,
        user_request=user_request,
    )
    return _merge_skill_candidates(
        keyword_candidates=_keyword_match(user_request),
        routing_hints=routing_hints,
    )


def preview_skills_for_turn(
    user_request: str,
    *,
    execution_mode: str = "auto",
    routing_decision: str | None = None,
    is_first_turn: bool = False,
) -> list[str]:
    """Canonical skill preview for routing, snapshots, and runtime selection."""
    return select_skill_candidates(
        user_request,
        execution_mode=execution_mode,
        routing_decision=routing_decision,
        is_first_turn=is_first_turn,
    )


def _format_available_skills() -> str:
    lines = [f"- {name}: {desc}" for name, desc in AVAILABLE_SKILLS.items()]
    return "\n".join(lines)


class SkillSelectionModule(dspy.Module):
    """Selects relevant skills and loads their instructions for injection."""

    def __init__(
        self,
        *,
        volume_mount_path: str | None = None,
        max_skills: int = 2,
        lm: Any | None = None,
    ) -> None:
        super().__init__()
        self.select = dspy.ChainOfThought(SkillSelectionSignature)
        self._volume_mount_path = volume_mount_path
        self._max_skills = max_skills
        # Prefer a capped clone of the delegate LM for the LLM disambiguation
        # call; fall back to the raw delegate LM (already bounded by
        # agent_delegate_max_tokens) and finally to the global dspy LM.
        self._select_lm = lm

    def _load_active_skills(self, names: list[str]) -> ActiveSkills:
        instructions: dict[str, str] = {}
        sources: dict[str, str] = {}
        catalog: dict[str, str] = {}
        for name in names[: self._max_skills]:
            result = _load_skill_impl(name, volume_mount_path=self._volume_mount_path)
            if result.status == "ok" and result.instructions:
                instructions[name] = result.instructions
                source = str(result.scope or "")
                path = str(result.path or "")
                sources[name] = f"{source}:{path}" if source and path else source or path
                catalog[name] = AVAILABLE_SKILLS.get(name, f"Bundled fleet-rlm skill: {name}")
        selected = [name for name in names[: self._max_skills] if name in instructions]
        return ActiveSkills(
            selected=selected,
            catalog=catalog,
            instructions=instructions,
            sources=sources,
        )

    def _skill_summary(self, active_skills: ActiveSkills) -> str:
        if not active_skills.selected:
            return ""
        lines = ["[Active Skills]", "Selected skill guidance is available in the REPL variable `active_skills`."]
        for name in active_skills.selected:
            description = active_skills.catalog.get(name, "")
            source = active_skills.sources.get(name, "")
            detail = f"- {name}"
            if description:
                detail += f": {description}"
            if source:
                detail += f" ({source})"
            lines.append(detail)
        lines.append("Inspect only relevant sections; do not print or copy full skill markdown.")
        return "\n".join(lines)

    def _prediction(self, *, selected: list[str], reasoning: str = "") -> dspy.Prediction:
        selected = selected[: self._max_skills]
        active_skills = self._load_active_skills(selected) if selected else ActiveSkills()
        skill_context = self._skill_summary(active_skills)
        return dspy.Prediction(
            selected_skills=active_skills.selected,
            skill_context=skill_context,
            active_skills=active_skills,
            reasoning=reasoning,
        )

    def _parse_skill_names(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            names = [str(s).strip() for s in raw]
        else:
            text = str(raw or "")
            names = [s.strip().strip("[]()").strip("\"'") for s in re.split(r"[,\n]", text) if s.strip()]
        return [n for n in names if n in AVAILABLE_SKILLS]

    def _get_skill_selection_config(self) -> tuple[Any | None, dict[str, Any]]:
        """Resolve delegate/small/global LM and its stateless config overrides.

        Mirrors ``_get_action_lm_config`` (factory.py): prefer a wired-in
        delegate LM (``self._select_lm``), else self-resolve from env at call
        time — small delegate → delegate → ``dspy.settings.lm``. Returns
        ``(base_lm, config_overrides)``.
        """
        import dspy as _dspy

        from fleet_rlm.runtime.config import (
            build_lm_config,
            get_delegate_lm_from_env,
            get_delegate_small_lm_from_env,
        )

        base: Any | None = None
        if self._select_lm is not None:
            base = self._select_lm
        else:
            try:
                base = get_delegate_small_lm_from_env() or get_delegate_lm_from_env()
            except Exception:
                base = None

        target_lm = base if base is not None else getattr(_dspy.settings, "lm", None)

        config_overrides = build_lm_config(
            target_lm,
            max_tokens=_SKILL_SELECTION_MAX_TOKENS,
            temperature=_SKILL_SELECTION_TEMPERATURE,
            timeout=_SKILL_SELECTION_TIMEOUT_S,
        )
        return base, config_overrides

    def _invoke_select(self, *, context: str, available_skills: str) -> dspy.Prediction:
        """Run the LLM skill selector on a bounded LM.

        Binds the capped delegate LM via ``dspy.settings.context`` and passes
        call-time config overrides so this trivial routing call never lands on
        the unbounded planner LM.
        """
        base_lm, config_overrides = self._get_skill_selection_config()
        kw: dict[str, Any] = {}
        if config_overrides:
            kw["config"] = config_overrides
        if base_lm is not None:
            with dspy.settings.context(lm=base_lm):
                return self.select(context=context, available_skills=available_skills, **kw)
        return self.select(context=context, available_skills=available_skills, **kw)

    def forward(
        self,
        *,
        user_request: str,
        core_memory: str = "",
        execution_mode: str = "auto",
        routing_decision: str | None = None,
        is_first_turn: bool = False,
    ) -> dspy.Prediction:
        candidates = select_skill_candidates(
            user_request,
            execution_mode=execution_mode,
            routing_decision=routing_decision,
            is_first_turn=is_first_turn,
        )

        if len(candidates) == 1:
            return self._prediction(selected=candidates)

        if len(candidates) > self._max_skills:
            context = f"{user_request}\n\nRecent context: {core_memory[:500]}" if core_memory else user_request
            try:
                prediction = self._invoke_select(
                    context=context,
                    available_skills=_format_available_skills(),
                )
                selected = self._parse_skill_names(getattr(prediction, "skills", []))
                reasoning = str(getattr(prediction, "reasoning", "") or "")
                if not selected:
                    selected = candidates[: self._max_skills]
                    reasoning = ""
            except Exception as exc:
                logger.warning("SkillSelectionModule: LLM selection failed (%s), using keyword candidates", exc)
                selected = candidates[: self._max_skills]
                reasoning = ""
        elif candidates:
            selected = candidates
            reasoning = ""
        else:
            return self._prediction(selected=[])

        return self._prediction(selected=selected, reasoning=reasoning)


__all__ = [
    "AVAILABLE_SKILLS",
    "SkillSelectionModule",
    "_keyword_match",
    "preview_skills_for_turn",
    "select_skill_candidates",
]
