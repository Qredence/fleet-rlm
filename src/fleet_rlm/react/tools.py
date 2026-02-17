"""DSPy ReAct tool definitions for the RLM chat agent.

Tools are defined as standalone functions following the DSPy convention:
each tool has a docstring (for the LLM description), type-hinted parameters
(for JSON schema generation), and returns ``dict[str, Any]``.

The :func:`build_tool_list` factory creates closures that capture the agent
instance, so ``dspy.ReAct`` only sees clean user-facing signatures — no
``self`` parameter.

See: https://dspy.ai/tutorials/customer_service_agent/#define-tools
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from dspy.primitives.code_interpreter import FinalOutput

from ..chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from ..core.interpreter import ExecutionProfile

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)
_MARKITDOWN_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".rtf",
    ".epub",
    ".html",
    ".htm",
}

# ---------------------------------------------------------------------------
# Shared helpers (used by multiple tools, not exposed to DSPy)
# ---------------------------------------------------------------------------


def normalize_strategy(strategy: str) -> str:
    """Normalise a chunking strategy name to its canonical form."""
    normalized = strategy.strip().lower().replace("-", "_")
    mapping = {
        "size": "size",
        "headers": "headers",
        "header": "headers",
        "timestamps": "timestamps",
        "timestamp": "timestamps",
        "json": "json_keys",
        "json_keys": "json_keys",
    }
    if normalized not in mapping:
        raise ValueError(
            "Unsupported strategy. Choose one of: size, headers, timestamps, json_keys"
        )
    return mapping[normalized]


def chunk_text(
    text: str,
    strategy: str,
    *,
    size: int,
    overlap: int,
    pattern: str,
) -> list[Any]:
    """Chunk *text* using the named strategy."""
    strategy_norm = normalize_strategy(strategy)
    if strategy_norm == "size":
        return chunk_by_size(text, size=size, overlap=overlap)
    if strategy_norm == "headers":
        return chunk_by_headers(text, pattern=pattern or r"^#{1,3} ")
    if strategy_norm == "timestamps":
        return chunk_by_timestamps(text, pattern=pattern or r"^\d{4}-\d{2}-\d{2}[T ]")
    return chunk_by_json_keys(text)


def chunk_to_text(chunk: Any) -> str:
    """Convert a chunk to plain text.

    Uses a lookup-based approach instead of multiple ``isinstance`` checks
    for better performance with large document collections.
    """
    if isinstance(chunk, str):
        return chunk
    if not isinstance(chunk, dict):
        return str(chunk)
    if "header" in chunk:
        return f"{chunk.get('header', '')}\n{chunk.get('content', '')}".strip()
    if "timestamp" in chunk:
        return chunk.get("content", "")
    if "key" in chunk:
        return f"{chunk.get('key', '')}\n{chunk.get('content', '')}".strip()
    return json.dumps(chunk, ensure_ascii=False, default=str)


def resolve_document(agent: RLMReActChatAgent, alias: str) -> str:
    """Resolve a document alias to its full text content."""
    if alias == "active":
        if agent.active_alias is None:
            raise ValueError("No active document. Use load_document() first.")
        return agent._get_document(agent.active_alias)
    if alias not in agent._document_cache:
        raise ValueError(f"Unknown document alias: {alias}")
    return agent._get_document(alias)


def execute_submit(
    agent: RLMReActChatAgent,
    code: str,
    *,
    variables: dict[str, Any] | None = None,
    execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
) -> dict[str, Any]:
    """Run *code* in the sandbox and return the SUBMIT() result."""
    agent.start()
    result = agent.interpreter.execute(
        code,
        variables=variables or {},
        execution_profile=execution_profile,
    )
    if isinstance(result, FinalOutput):
        output = result.output
        if isinstance(output, dict):
            return output
        return {"output": output}
    return {"output": str(result)}


def _extract_text_with_markitdown(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract document text via MarkItDown."""
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError(
            "MarkItDown is not installed. Run `uv sync` to install runtime dependencies."
        ) from exc

    converter = MarkItDown()
    converted = converter.convert(str(path))

    text_value = ""
    for attr in ("text_content", "markdown", "content", "text"):
        candidate = getattr(converted, attr, None)
        if isinstance(candidate, str) and candidate.strip():
            text_value = candidate
            break
    if not text_value and isinstance(converted, str):
        text_value = converted.strip()

    return (
        text_value,
        {
            "source_type": path.suffix.lower().lstrip(".") or "document",
            "extraction_method": "markitdown",
        },
    )


def _extract_text_with_pypdf(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract PDF text with pypdf as fallback."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    page_texts: list[str] = []
    pages_with_text = 0
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages_with_text += 1
        page_texts.append(text)

    return (
        "\n\n".join(page_texts).strip(),
        {
            "source_type": "pdf",
            "extraction_method": "pypdf",
            "page_count": len(reader.pages),
            "pages_with_text": pages_with_text,
        },
    )


def _looks_like_binary(path: Path, probe_bytes: int = 2048) -> bool:
    """Heuristic for binary files to avoid UTF-8 decoding crashes."""
    sample = path.read_bytes()[:probe_bytes]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    textish = b"\n\r\t\f\b"
    printable = sum((32 <= byte <= 126) or byte in textish for byte in sample)
    return printable / len(sample) < 0.8


def _read_document_content(path: Path) -> tuple[str, dict[str, Any]]:
    """Read document content with safe handling for PDF/binary formats."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        extraction_errors: list[str] = []
        try:
            text, meta = _extract_text_with_markitdown(path)
            if text.strip():
                return text, meta
            extraction_errors.append("markitdown returned no text")
        except (
            Exception
        ) as exc:  # pragma: no cover - exercised via tests with monkeypatch
            extraction_errors.append(f"markitdown: {exc}")

        try:
            text, meta = _extract_text_with_pypdf(path)
            if text.strip():
                return text, meta
        except Exception as exc:
            extraction_errors.append(f"pypdf: {exc}")
            details = "; ".join(extraction_errors)
            raise ValueError(
                f"Could not extract text from PDF '{path}'. Details: {details}"
            ) from exc

        raise ValueError(
            f"PDF '{path}' appears to be image-only or scanned. OCR is required before analysis."
        )

    if suffix in _MARKITDOWN_SUFFIXES:
        try:
            text, meta = _extract_text_with_markitdown(path)
            if text.strip():
                return text, meta
        except Exception as exc:
            logger.warning(
                "markitdown extraction failed for '%s' (suffix %s); falling back to read_text: %s",
                path,
                suffix,
                exc,
            )

    try:
        return path.read_text(), {
            "source_type": "text",
            "extraction_method": "read_text",
        }
    except UnicodeDecodeError as exc:
        if _looks_like_binary(path):
            raise ValueError(
                f"Binary file detected at '{path}'. Use a text file or supported document format (for example, PDF)."
            ) from exc
        raise ValueError(f"Could not decode '{path}' as UTF-8 text.") from exc


def _rlm_trajectory_payload(result: Any, *, include_trajectory: bool) -> dict[str, Any]:
    """Build a normalized trajectory payload from a DSPy RLM result."""
    if not include_trajectory:
        return {}

    trajectory = list(getattr(result, "trajectory", []) or [])
    payload: dict[str, Any] = {
        "trajectory_steps": len(trajectory),
        "trajectory": trajectory,
    }
    final_reasoning = getattr(result, "final_reasoning", None)
    if final_reasoning:
        payload["final_reasoning"] = final_reasoning
    return payload


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_tool_list(
    agent: RLMReActChatAgent,
    extra_tools: list[Callable[..., Any]] | None = None,
) -> list[Any]:
    """Build the DSPy ReAct tool list with closures bound to *agent*.

    Each inner function has a descriptive ``__name__``, docstring, and
    type-hinted parameters so ``dspy.ReAct`` can introspect them cleanly.
    """

    # -- Document management -------------------------------------------------

    def load_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a text document from host filesystem into agent document memory.

        If path is a directory, returns a recursive file listing instead of loading.
        """
        docs_path = Path(path)
        if not docs_path.exists():
            raise FileNotFoundError(f"Document not found: {docs_path}")

        # Handle directory: return file listing
        if docs_path.is_dir():
            # Make paths relative to cwd for easy reuse in load_document
            cwd = Path.cwd()
            files = sorted(
                str(p.relative_to(cwd) if p.is_relative_to(cwd) else p)
                for p in docs_path.rglob("*")
                if p.is_file()
            )
            return {
                "status": "directory",
                "path": str(docs_path),
                "files": files[:100],  # Cap at 100 for display
                "total_count": len(files),
                "hint": "Use load_document with a specific file path from this listing.",
            }

        # Handle file: load content
        content, metadata = _read_document_content(docs_path)
        agent._set_document(alias, content)
        agent.active_alias = alias
        response = {
            "status": "ok",
            "alias": alias,
            "path": str(docs_path),
            "chars": len(content),
            "lines": len(content.splitlines()),
        }
        if metadata.get("source_type") != "text":
            response.update(metadata)
        return response

    def set_active_document(alias: str) -> dict[str, Any]:
        """Set which loaded document alias should be used by default tools."""
        if alias not in agent.documents:
            raise ValueError(f"Unknown document alias: {alias}")
        agent.active_alias = alias
        return {"status": "ok", "active_alias": alias}

    def list_documents() -> dict[str, Any]:
        """List loaded document aliases and active document metadata."""
        docs = []
        for doc_alias, text in agent._document_cache.items():
            docs.append(
                {
                    "alias": doc_alias,
                    "chars": len(text),
                    "lines": len(text.splitlines()),
                }
            )
        return {
            "documents": docs,
            "active_alias": agent.active_alias,
            "cache_size": len(agent._document_cache),
            "cache_limit": agent._max_documents,
        }

    # -- Filesystem navigation -----------------------------------------------

    def list_files(path: str = ".", pattern: str = "**/*") -> dict[str, Any]:
        """List files on the host filesystem matching a glob pattern.

        Returns files relative to the base path, with total count and size.
        """
        base = Path(path)
        if not base.exists():
            raise FileNotFoundError(f"Path not found: {base}")
        if not base.is_dir():
            # Single file: return it as a 1-item list
            return {
                "status": "ok",
                "path": str(base),
                "files": [base.name],
                "count": 1,
                "total_bytes": base.stat().st_size,
            }

        # Directory: glob for files matching pattern
        matched = [p for p in base.glob(pattern) if p.is_file()]
        files = sorted(str(p.relative_to(base)) for p in matched)
        total_bytes = sum(p.stat().st_size for p in matched)

        return {
            "status": "ok",
            "path": str(base),
            "files": files[:100],  # Cap at 100 for display
            "count": len(files),
            "total_bytes": total_bytes,
            "hint": "Use load_document to read a specific file from this listing.",
        }

    def read_file_slice(
        path: str, start_line: int = 1, num_lines: int = 100
    ) -> dict[str, Any]:
        """Read a range of lines from a host file without loading the full document.

        Useful for inspecting large files. Line numbers are 1-indexed.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if file_path.is_dir():
            raise IsADirectoryError(f"Cannot read lines from directory: {file_path}")

        content, _ = _read_document_content(file_path)
        lines = content.splitlines()
        total_lines = len(lines)

        # Adjust to 0-indexed
        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, start_idx + num_lines)

        slice_lines = lines[start_idx:end_idx]
        numbered = [
            {"line": start_idx + i + 1, "text": text}
            for i, text in enumerate(slice_lines)
        ]

        return {
            "status": "ok",
            "path": str(file_path),
            "start_line": start_line,
            "lines": numbered,
            "returned_count": len(numbered),
            "total_lines": total_lines,
        }

    def find_files(pattern: str, path: str = ".", include: str = "") -> dict[str, Any]:
        """Search file contents on the host using regex pattern (ripgrep).

        Returns matching files with line numbers and text snippets.
        Use 'include' to filter by file extension (e.g., '*.py').
        """
        try:
            from ripgrepy import Ripgrepy  # ty: ignore[unresolved-import]
        except ImportError:
            return {
                "status": "error",
                "error": "ripgrepy not installed (install with 'interactive' extra)",
            }

        rg = Ripgrepy(pattern, path).json().with_filename().line_number().max_count(50)
        if include:
            rg = rg.glob(include)

        try:
            out = rg.run()
        except Exception as exc:
            return {
                "status": "error",
                "pattern": pattern,
                "path": path,
                "error": str(exc),
            }

        hits = []
        for item in out.as_dict:
            if item.get("type") != "match":
                continue
            data = item.get("data", {})
            path_text = data.get("path", {}).get("text", "")
            line_no = data.get("line_number")
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            hits.append({"path": path_text, "line": line_no, "text": line_text})

        return {
            "status": "ok",
            "pattern": pattern,
            "search_path": path,
            "include": include or "all files",
            "count": len(hits),
            "hits": hits[:20],  # Cap display at 20
        }

    # -- Chunking ------------------------------------------------------------

    def chunk_host(
        strategy: str,
        alias: str = "active",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Chunk document on host using size/headers/timestamps/json-keys strategies."""
        text = resolve_document(agent, alias)
        chunks = chunk_text(text, strategy, size=size, overlap=overlap, pattern=pattern)
        preview = chunks[0] if chunks else ""
        return {
            "status": "ok",
            "strategy": strategy,
            "chunk_count": len(chunks),
            "preview": str(preview)[:400],
        }

    def chunk_sandbox(
        strategy: str,
        variable_name: str = "active_document",
        buffer_name: str = "chunks",
        size: int = 200_000,
        overlap: int = 0,
        pattern: str = "",
    ) -> dict[str, Any]:
        """Chunk the active document inside sandbox and store chunks in a buffer."""
        text = resolve_document(agent, "active")
        strategy_norm = normalize_strategy(strategy)

        code = """
import json

clear_buffer(buffer_name)

if strategy_norm == "size":
    chunks = chunk_by_size(active_document, size=size, overlap=overlap)
elif strategy_norm == "headers":
    chunks = chunk_by_headers(active_document, pattern=pattern or r"^#{1,3} ")
elif strategy_norm == "timestamps":
    chunks = chunk_by_timestamps(active_document, pattern=pattern or r"^\\d{4}-\\d{2}-\\d{2}[T ]")
elif strategy_norm == "json_keys":
    chunks = chunk_by_json_keys(active_document)
else:
    raise ValueError(f"Unsupported strategy: {strategy_norm}")

for chunk in chunks:
    add_buffer(buffer_name, chunk)

SUBMIT(
    status="ok",
    strategy=strategy_norm,
    chunk_count=len(chunks),
    buffer_name=buffer_name,
)
"""
        variables = {
            variable_name: text,
            "active_document": text,
            "strategy_norm": strategy_norm,
            "buffer_name": buffer_name,
            "size": size,
            "overlap": overlap,
            "pattern": pattern,
        }
        return execute_submit(agent, code, variables=variables)

    # -- Assemble tool list --------------------------------------------------

    from dspy import Tool
    from .tools_sandbox import build_sandbox_tools

    tools: list[Tool] = [
        Tool(
            load_document,
            name="load_document",
            desc="Load a text document from host filesystem into agent document memory",
        ),
        Tool(
            set_active_document,
            name="set_active_document",
            desc="Set which loaded document alias should be used by default tools",
        ),
        Tool(
            list_documents,
            name="list_documents",
            desc="List loaded document aliases and active document metadata",
        ),
        Tool(
            list_files,
            name="list_files",
            desc="List files on the host filesystem matching a glob pattern",
        ),
        Tool(
            read_file_slice,
            name="read_file_slice",
            desc="Read a range of lines from a host file without loading the full document",
        ),
        Tool(
            find_files,
            name="find_files",
            desc="Search file contents on the host using regex pattern (ripgrep)",
        ),
        Tool(
            chunk_host,
            name="chunk_host",
            desc="Chunk document on host using size/headers/timestamps/json-keys strategies",
        ),
        Tool(
            chunk_sandbox,
            name="chunk_sandbox",
            desc="Chunk active document inside sandbox and store chunks in a buffer",
        ),
    ]
    # Add sandbox / RLM / buffer tools from dedicated module
    tools.extend(build_sandbox_tools(agent))
    # Wrap extra tools with dspy.Tool if not already wrapped
    if extra_tools:
        for et in extra_tools:
            if isinstance(et, Tool):
                tools.append(et)
            else:
                tools.append(Tool(et))
    return tools


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def list_react_tool_names(tools: Iterable[Any]) -> list[str]:
    """Return stable tool names for display / debugging.

    Handles both raw callables (``__name__``) and ``dspy.Tool`` wrappers
    (``.name``).
    """
    names: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        names.append(name)
    return names
