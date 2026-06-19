"""SandboxSerializable models for large runtime inputs.

These models let ``dspy.RLM`` inject large payloads directly into the sandbox
as native Python objects: the LM sees only ``rlm_preview()`` metadata while the
full value is reconstructed inside the REPL via ``sandbox_assignment()``.
"""

from __future__ import annotations

import json

from dspy.primitives.sandbox_serializable import SandboxSerializable


class LargeDocument(SandboxSerializable):
    """A fetched or extracted document injected into the sandbox as a dict.

    Inside the REPL the variable is a plain dict with keys ``text``,
    ``source_url``, and ``metadata``.
    """

    def __init__(
        self,
        *,
        text: str,
        source_url: str = "",
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.text = text or ""
        self.source_url = source_url or ""
        self.metadata = dict(metadata or {})

    def sandbox_setup(self) -> str:
        return "import json"

    def to_sandbox(self) -> bytes:
        payload = {
            "text": self.text,
            "source_url": self.source_url,
            "metadata": self.metadata,
        }
        return json.dumps(payload).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = json.loads({data_expr})"

    def rlm_preview(self, max_chars: int = 500) -> str:
        snippet = " ".join(self.text[:max_chars].split())
        parts = [
            "dict with keys ['text', 'source_url', 'metadata']",
            f"text has {len(self.text)} chars",
        ]
        if self.source_url:
            parts.append(f"source_url={self.source_url!r}")
        if snippet:
            parts.append(f"text preview: {snippet!r}")
        return "; ".join(parts)


class WorkspaceContext(SandboxSerializable):
    """Large local turn context staged into the sandbox as a dict.

    Inside the REPL the variable is a plain dict with keys ``document_text``,
    ``context_paths``, ``manifest``, and ``metadata``. ``context_paths`` are
    host paths recorded for reference only; staged sandbox copies are listed in
    the workspace ``.fleet-rlm/context/manifest.json``.
    """

    def __init__(
        self,
        *,
        document_text: str = "",
        context_paths: list[str] | None = None,
        manifest: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.document_text = document_text or ""
        self.context_paths = [str(item) for item in (context_paths or [])]
        self.manifest = dict(manifest or {})
        self.metadata = dict(metadata or {})

    def sandbox_setup(self) -> str:
        return "import json"

    def to_sandbox(self) -> bytes:
        payload = {
            "document_text": self.document_text,
            "context_paths": self.context_paths,
            "manifest": self.manifest,
            "metadata": self.metadata,
        }
        return json.dumps(payload).encode("utf-8")

    def sandbox_assignment(self, var_name: str, data_expr: str) -> str:
        return f"{var_name} = json.loads({data_expr})"

    def rlm_preview(self, max_chars: int = 500) -> str:
        parts = [
            "dict with keys ['document_text', 'context_paths', 'manifest', 'metadata']",
            f"document_text has {len(self.document_text)} chars",
            f"{len(self.context_paths)} host context path(s)",
        ]
        if not self.document_text:
            parts.append(
                "document_text is empty; use sandbox paths from metadata.sandbox_staged_paths or "
                "read .fleet-rlm/context/manifest.json in the workspace and open each staged "
                ".extracted.txt file (host context_paths are not readable in the REPL)"
            )
        else:
            snippet = " ".join(self.document_text[:max_chars].split())
            if snippet:
                parts.append(f"document_text preview: {snippet!r}")
        return "; ".join(parts)


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
    ) -> None:
        self.selected = [str(item) for item in (selected or [])]
        self.catalog = {str(key): str(value) for key, value in (catalog or {}).items()}
        self.instructions = {str(key): str(value) for key, value in (instructions or {}).items()}
        self.sources = {str(key): str(value) for key, value in (sources or {}).items()}

    def sandbox_setup(self) -> str:
        return "import json"

    def to_sandbox(self) -> bytes:
        payload = {
            "selected": self.selected,
            "catalog": self.catalog,
            "instructions": self.instructions,
            "sources": self.sources,
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


__all__ = [
    "ActiveSkills",
    "LargeDocument",
    "WorkspaceContext",
]
