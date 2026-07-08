"""Skill selection — keyword hints, DSPy selector, and ActiveSkills assembly."""

from __future__ import annotations

import logging
import re
from typing import Any

import dspy

from fleet_rlm.runtime.task_intent import has_url, has_url_document_intent
from fleet_rlm.skills.active import ActiveSkills
from fleet_rlm.skills.loader import load_skill_bundle, load_skill_impl
from fleet_rlm.skills.repository import AVAILABLE_SKILLS, list_visible
from fleet_rlm.skills.schemas import SkillResource, SkillRuntimeContext
from fleet_rlm.skills.signatures import SkillSelectionSignature

logger = logging.getLogger(__name__)

_SKILL_SELECTION_MAX_TOKENS = 512
_SKILL_SELECTION_TEMPERATURE = 0.0
_SKILL_SELECTION_TIMEOUT_S = 30.0

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
    from fleet_rlm.skills.catalog import discover_scaffold_skills

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


_KEYWORD_MAP: dict[str, list[str]] = _build_keyword_map()


def _keyword_match(user_request: str) -> list[str]:
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
    return select_skill_candidates(
        user_request,
        execution_mode=execution_mode,
        routing_decision=routing_decision,
        is_first_turn=is_first_turn,
    )


def _format_available_skills(context: SkillRuntimeContext | None = None) -> str:
    if context is not None:
        lines = [f"- {entry.name}: {entry.description}" for entry in list_visible(context)]
    else:
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
        self._select_lm = lm

    def _visible_skill_names(self, context: SkillRuntimeContext | None) -> set[str]:
        if context is not None:
            return {entry.name for entry in list_visible(context)}
        return set(AVAILABLE_SKILLS.keys())

    def _resolve_candidates(
        self,
        user_request: str,
        *,
        context: SkillRuntimeContext | None,
        selected_skill_ids: list[str] | None,
        execution_mode: str,
        routing_decision: str | None,
        is_first_turn: bool,
        explicit_only: bool,
    ) -> list[str]:
        visible_names = self._visible_skill_names(context)
        explicit_source = selected_skill_ids
        if explicit_source is None and context is not None:
            explicit_source = list(context.selected_skill_ids)

        merged: list[str] = []
        seen: set[str] = set()
        for skill_id in explicit_source or []:
            if skill_id in visible_names and skill_id not in seen:
                merged.append(skill_id)
                seen.add(skill_id)
            elif skill_id and skill_id not in visible_names:
                logger.warning(
                    "SkillSelectionModule: dropping invisible or unknown skill id %r",
                    skill_id,
                )

        if explicit_only:
            return merged

        routing_hints = _routing_skill_hints(
            execution_mode=execution_mode,
            routing_decision=routing_decision,
            is_first_turn=is_first_turn,
            user_request=user_request,
        )
        keyword_candidates = _keyword_match(user_request)
        for name in [*routing_hints, *keyword_candidates]:
            if name in visible_names and name not in seen:
                seen.add(name)
                merged.append(name)
        return merged

    def _load_active_skills(
        self,
        names: list[str],
        *,
        context: SkillRuntimeContext | None = None,
        max_skills: int | None = None,
    ) -> ActiveSkills:
        limit = max_skills if max_skills is not None else self._max_skills
        if context is None and self._volume_mount_path:
            context = SkillRuntimeContext(volume_mount_path=self._volume_mount_path)
        instructions: dict[str, str] = {}
        sources: dict[str, str] = {}
        catalog: dict[str, str] = {}
        resources: dict[str, list[SkillResource]] = {}
        sandbox_paths: dict[str, str] = {}
        for name in names[:limit]:
            if context is not None:
                try:
                    bundle = load_skill_bundle(name, context)
                except ValueError:
                    continue
                if not bundle.instructions:
                    continue
                instructions[name] = bundle.instructions
                sources[name] = bundle.metadata.source
                catalog[name] = bundle.metadata.description
                if bundle.resources:
                    resources[name] = bundle.resources
                if bundle.metadata.directory_style and context.volume_mount_path:
                    sandbox_paths[name] = (
                        f"{context.volume_mount_path}/skills/{bundle.metadata.scope.value}/{bundle.metadata.name}"
                    )
                continue

            result = load_skill_impl(name, volume_mount_path=self._volume_mount_path)
            if result.status == "ok" and result.instructions:
                instructions[name] = result.instructions
                source = str(result.scope or "")
                path = str(result.path or "")
                sources[name] = f"{source}:{path}" if source and path else source or path
                catalog[name] = AVAILABLE_SKILLS.get(name, f"Bundled fleet-rlm skill: {name}")
        selected = [name for name in names[:limit] if name in instructions]
        return ActiveSkills(
            selected=selected,
            catalog=catalog,
            instructions=instructions,
            sources=sources,
            resources=resources or None,
            sandbox_paths=sandbox_paths or None,
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

    def _prediction(
        self,
        *,
        selected: list[str],
        reasoning: str = "",
        context: SkillRuntimeContext | None = None,
        max_skills: int | None = None,
    ) -> dspy.Prediction:
        limit = max_skills if max_skills is not None else self._max_skills
        selected = selected[:limit]
        active_skills = (
            self._load_active_skills(selected, context=context, max_skills=limit) if selected else ActiveSkills()
        )
        skill_context = self._skill_summary(active_skills)
        return dspy.Prediction(
            selected_skills=active_skills.selected,
            skill_context=skill_context,
            active_skills=active_skills,
            reasoning=reasoning,
        )

    def _parse_skill_names(
        self,
        raw: Any,
        *,
        context: SkillRuntimeContext | None = None,
    ) -> list[str]:
        visible_names = self._visible_skill_names(context)
        if isinstance(raw, list):
            names = [str(s).strip() for s in raw]
        else:
            text = str(raw or "")
            names = [s.strip().strip("[]()").strip("\"'") for s in re.split(r"[,\n]", text) if s.strip()]
        return [n for n in names if n in visible_names]

    def _get_skill_selection_config(self) -> tuple[Any | None, dict[str, Any]]:
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
        context: SkillRuntimeContext | None = None,
        selected_skill_ids: list[str] | None = None,
        explicit_only: bool = False,
    ) -> dspy.Prediction:
        max_skills = (
            context.max_active_skills
            if context is not None and context.max_active_skills is not None
            else self._max_skills
        )
        candidates = self._resolve_candidates(
            user_request,
            context=context,
            selected_skill_ids=selected_skill_ids,
            execution_mode=execution_mode,
            routing_decision=routing_decision,
            is_first_turn=is_first_turn,
            explicit_only=explicit_only,
        )

        if len(candidates) == 1:
            return self._prediction(
                selected=candidates,
                context=context,
                max_skills=max_skills,
            )

        if len(candidates) > max_skills:
            context_text = f"{user_request}\n\nRecent context: {core_memory[:500]}" if core_memory else user_request
            try:
                prediction = self._invoke_select(
                    context=context_text,
                    available_skills=_format_available_skills(context),
                )
                selected = self._parse_skill_names(getattr(prediction, "skills", []), context=context)
                reasoning = str(getattr(prediction, "reasoning", "") or "")
                if not selected:
                    selected = candidates[:max_skills]
                    reasoning = ""
            except Exception as exc:
                logger.warning("SkillSelectionModule: LLM selection failed (%s), using keyword candidates", exc)
                selected = candidates[:max_skills]
                reasoning = ""
        elif candidates:
            selected = candidates
            reasoning = ""
        else:
            return self._prediction(selected=[], context=context, max_skills=max_skills)

        return self._prediction(
            selected=selected,
            reasoning=reasoning,
            context=context,
            max_skills=max_skills,
        )


__all__ = [
    "AVAILABLE_SKILLS",
    "SkillSelectionModule",
    "_keyword_match",
    "preview_skills_for_turn",
    "select_skill_candidates",
]
