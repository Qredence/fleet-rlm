"""Skill selection module — proactively selects and injects relevant skills into context.

This module classifies the user request against the predefined skill set and
loads matching skill instructions into the agent's context before the main
reasoning path runs. It uses a fast keyword heuristic for obvious cases and
falls back to a ChainOfThought LLM call for ambiguous requests.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import SkillSelectionSignature
from fleet_rlm.runtime.tools.skill_tools import _load_skill_impl

logger = logging.getLogger(__name__)

AVAILABLE_SKILLS: dict[str, str] = {
    "sandbox-execution": "Execute Python in Daytona sandboxes, persist results, volume lifecycle",
    "delegation": "Recursive child RLMs, batch fan-out, budget management",
    "dspy-programs": "DSPy signature design, module registry, execution modes",
    "long-context": "Chunk large documents, variable-mode, hierarchical map-reduce",
    "optimization": "GEPA/MIPROv2 optimization, evaluation metrics, MLflow",
    "diagnostics": "Diagnose runtime failures, contract drift, test triage",
    "volume-bootstrap": "Volume filesystem structure, CRUD helpers, persistence guarantees",
}

_KEYWORD_MAP: dict[str, list[str]] = {
    "sandbox-execution": [
        "execute",
        "sandbox",
        "run code",
        "daytona",
        "interpreter",
        "SUBMIT",
        "volume",
        "persist",
        "shutdown",
        "workspace",
    ],
    "delegation": [
        "delegate",
        "child",
        "sub_rlm",
        "rlm_query",
        "batch",
        "concurrent",
        "fan-out",
        "fanout",
        "budget",
        "recursion",
    ],
    "dspy-programs": [
        "signature",
        "dspy",
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
    "optimization": [
        "optimi",
        "GEPA",
        "MIPROv2",
        "evaluate",
        "scorer",
        "dataset",
        "mlflow",
        "training",
        "compile",
    ],
    "diagnostics": [
        "debug",
        "diagnos",
        "broken",
        "fail",
        "error",
        "test",
        "contract drift",
        "troubleshoot",
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


def _keyword_match(user_request: str) -> list[str]:
    """Fast keyword-based skill matching (zero LLM cost)."""
    request_lower = user_request.lower()
    scores: dict[str, int] = {}
    for skill_name, keywords in _KEYWORD_MAP.items():
        score = sum(1 for kw in keywords if kw.lower() in request_lower)
        if score > 0:
            scores[skill_name] = score

    if not scores:
        return []

    max_score = max(scores.values())
    threshold = max(1, max_score - 1)
    return [name for name, score in scores.items() if score >= threshold]


def _format_available_skills() -> str:
    """Format the skill catalog as a compact string for the LLM."""
    lines = [f"- {name}: {desc}" for name, desc in AVAILABLE_SKILLS.items()]
    return "\n".join(lines)


class SkillSelectionModule(dspy.Module):
    """Selects relevant skills from the predefined set and loads their instructions.

    Uses a two-phase approach:
    1. Fast keyword heuristic — if exactly one skill matches clearly, use it (no LLM cost)
    2. ChainOfThought fallback — for ambiguous requests, ask the LLM to select

    The loaded skill instructions are returned as a string suitable for injection
    into core_memory or the RLM prompt context.
    """

    def __init__(self, *, volume_mount_path: str | None = None, max_skills: int = 2) -> None:
        super().__init__()
        self.select = dspy.ChainOfThought(SkillSelectionSignature)
        self._volume_mount_path = volume_mount_path
        self._max_skills = max_skills

    def _load_skills(self, names: list[str]) -> str:
        """Load skill instructions from the volume and return as formatted context."""
        loaded: list[str] = []
        for name in names[: self._max_skills]:
            result = _load_skill_impl(name, volume_mount_path=self._volume_mount_path)
            if result.status == "ok" and result.instructions:
                loaded.append(f"[Skill: {name}]\n{result.instructions}")
        return "\n\n".join(loaded)

    def _prediction(self, *, selected: list[str], reasoning: str = "") -> dspy.Prediction:
        """Build a selection prediction whose metadata matches loaded context."""
        selected = selected[: self._max_skills]
        skill_context = self._load_skills(selected) if selected else ""
        return dspy.Prediction(
            selected_skills=selected,
            skill_context=skill_context,
            reasoning=reasoning,
        )

    def _parse_skill_names(self, raw: Any) -> list[str]:
        """Parse skill names from LLM output (handles list or comma-separated string)."""
        if isinstance(raw, list):
            names = [str(s).strip() for s in raw]
        else:
            text = str(raw or "")
            names = [s.strip().strip("\"'") for s in re.split(r"[,\n]", text) if s.strip()]
        return [n for n in names if n in AVAILABLE_SKILLS]

    def forward(
        self,
        *,
        user_request: str,
        core_memory: str = "",
    ) -> dspy.Prediction:
        """Select and load relevant skills for the given request.

        Returns a Prediction with:
        - selected_skills: list[str] — names of selected skills
        - skill_context: str — loaded skill instructions (for injection into context)
        - reasoning: str — why these skills were selected (empty for keyword path)
        """
        candidates = _keyword_match(user_request)

        if len(candidates) == 1:
            return self._prediction(selected=candidates)

        if len(candidates) > self._max_skills:
            context = f"{user_request}\n\nRecent context: {core_memory[:500]}" if core_memory else user_request
            try:
                prediction = self.select(
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
]
