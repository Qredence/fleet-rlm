"""ActiveSkills — SandboxSerializable skill payload for REPL injection."""

from __future__ import annotations

import json

from dspy.primitives.sandbox_serializable import SandboxSerializable

from fleet_rlm.skills.schemas import SkillResource


class ActiveSkills(SandboxSerializable):
    """Selected skill instructions injected into the REPL as structured data.

    The model prompt should only see a compact preview. Full markdown remains
    available to sandbox code through ``active_skills["instructions"]``.
    """

    def __init__(
        self,
        *,
        selected: list[str] | None = None,
        catalog: dict[str, str] | None = None,
        instructions: dict[str, str] | None = None,
        sources: dict[str, str] | None = None,
        resources: dict[str, list[SkillResource]] | None = None,
        sandbox_paths: dict[str, str] | None = None,
    ) -> None:
        self.selected = [str(item) for item in (selected or [])]
        self.catalog = {str(key): str(value) for key, value in (catalog or {}).items()}
        self.instructions = {str(key): str(value) for key, value in (instructions or {}).items()}
        self.sources = {str(key): str(value) for key, value in (sources or {}).items()}
        self.resources = {str(key): list(value) for key, value in (resources or {}).items()}
        self.sandbox_paths = {str(key): str(value) for key, value in (sandbox_paths or {}).items()}

    def sandbox_setup(self) -> str:
        return "import json"

    def to_sandbox(self) -> bytes:
        serialized_resources: dict[str, list[dict[str, str | None]]] = {}
        for name, items in self.resources.items():
            serialized_resources[name] = [item.model_dump() for item in items]
        payload = {
            "selected": self.selected,
            "catalog": self.catalog,
            "instructions": self.instructions,
            "sources": self.sources,
            "resources": serialized_resources,
            "sandbox_paths": self.sandbox_paths,
        }
        return json.dumps(payload).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = json.loads({data_expr})"

    def rlm_preview(self, max_chars: int = 500) -> str:
        _ = max_chars
        if not self.selected:
            return "dict with keys ['selected', 'catalog', 'instructions', 'sources']; no active skills selected"
        parts = [
            "dict with keys ['selected', 'catalog', 'instructions', 'sources']",
            f"selected={self.selected!r}",
        ]
        summaries: list[str] = []
        for name in self.selected:
            description = self.catalog.get(name, "")
            source = self.sources.get(name, "")
            summary = name
            if description:
                summary = f"{summary}: {description}"
            if source:
                summary = f"{summary} ({source})"
            summaries.append(summary)
        if summaries:
            parts.append("skill previews: " + "; ".join(summaries))
        parts.append("full markdown is available in active_skills['instructions'][name]")
        return "; ".join(parts)


__all__ = ["ActiveSkills"]
