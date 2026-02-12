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

import dspy
from dspy.primitives.code_interpreter import FinalOutput

from .chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from .signatures import AnalyzeLongDocument, ExtractFromLogs, SummarizeLongDocument

if TYPE_CHECKING:
    from .react_agent import RLMReActChatAgent

logger = logging.getLogger(__name__)

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
) -> dict[str, Any]:
    """Run *code* in the sandbox and return the SUBMIT() result."""
    agent.start()
    result = agent.interpreter.execute(code, variables=variables or {})
    if isinstance(result, FinalOutput):
        output = result.output
        if isinstance(output, dict):
            return output
        return {"output": output}
    return {"output": str(result)}


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_tool_list(
    agent: RLMReActChatAgent,
    extra_tools: list[Callable[..., Any]] | None = None,
) -> list[Callable[..., Any]]:
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
        content = docs_path.read_text()
        agent._set_document(alias, content)
        agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "path": str(docs_path),
            "chars": len(content),
            "lines": len(content.splitlines()),
        }

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

        lines = file_path.read_text().splitlines()
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
            from ripgrepy import Ripgrepy
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

    # -- Long-context analysis -----------------------------------------------

    def parallel_semantic_map(
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        buffer_name: str = "findings",
    ) -> dict[str, Any]:
        """Run parallel semantic analysis over chunks via llm_query_batched."""
        text = resolve_document(agent, "active")
        chunks = chunk_text(
            text, chunk_strategy, size=80_000, overlap=1_000, pattern=""
        )
        chunk_texts = [chunk_to_text(c) for c in chunks][:max_chunks]

        prompts = []
        for idx, chunk_item in enumerate(chunk_texts):
            prompts.append(
                (
                    f"Query: {query}\n"
                    f"Chunk index: {idx}\n"
                    "Return concise findings as plain text.\n\n"
                    f"{chunk_item[:6000]}"
                )
            )

        code = """
clear_buffer(buffer_name)
responses = llm_query_batched(prompts)
for idx, response in enumerate(responses):
    add_buffer(buffer_name, {"chunk_index": idx, "response": response})

SUBMIT(
    status="ok",
    strategy=chunk_strategy,
    chunk_count=len(prompts),
    findings_count=len(responses),
    buffer_name=buffer_name,
)
"""
        return execute_submit(
            agent,
            code,
            variables={
                "prompts": prompts,
                "buffer_name": buffer_name,
                "chunk_strategy": chunk_strategy,
            },
        )

    def analyze_long_document(query: str, alias: str = "active") -> dict[str, Any]:
        """Analyze a long document with the AnalyzeLongDocument RLM signature."""
        agent.start()
        document = resolve_document(agent, alias)
        rlm = dspy.RLM(
            signature=AnalyzeLongDocument,
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
        result = rlm(document=document, query=query)
        return {
            "status": "ok",
            "findings": result.findings,
            "answer": result.answer,
            "sections_examined": result.sections_examined,
            "doc_chars": len(document),
        }

    def summarize_long_document(focus: str, alias: str = "active") -> dict[str, Any]:
        """Summarize a long document with the SummarizeLongDocument RLM signature."""
        agent.start()
        document = resolve_document(agent, alias)
        rlm = dspy.RLM(
            signature=SummarizeLongDocument,
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
        result = rlm(document=document, focus=focus)
        return {
            "status": "ok",
            "summary": result.summary,
            "key_points": result.key_points,
            "coverage_pct": result.coverage_pct,
            "doc_chars": len(document),
        }

    def extract_from_logs(query: str, alias: str = "active") -> dict[str, Any]:
        """Extract structured patterns from log text via ExtractFromLogs RLM signature."""
        agent.start()
        logs = resolve_document(agent, alias)
        rlm = dspy.RLM(
            signature=ExtractFromLogs,
            interpreter=agent.interpreter,
            max_iterations=agent.rlm_max_iterations,
            max_llm_calls=agent.rlm_max_llm_calls,
            verbose=agent.verbose,
        )
        result = rlm(logs=logs, query=query)
        return {
            "status": "ok",
            "matches": result.matches,
            "patterns": result.patterns,
            "time_range": result.time_range,
        }

    # -- Buffer & volume management ------------------------------------------

    def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = execute_submit(
            agent, "SUBMIT(items=get_buffer(name))", variables={"name": name}
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return execute_submit(agent, code, variables=variables)

    def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to Modal Volume storage as JSON."""
        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        return execute_submit(agent, code, variables={"name": name, "path": path})

    def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from Modal Volume into host-side document memory."""
        result = execute_submit(
            agent,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": path},
        )
        text = str(result.get("text", ""))
        agent._set_document(alias, text)
        agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    # -- Assemble tool list --------------------------------------------------

    tools: list[Callable[..., Any]] = [
        load_document,
        set_active_document,
        list_documents,
        list_files,
        read_file_slice,
        find_files,
        chunk_host,
        chunk_sandbox,
        parallel_semantic_map,
        analyze_long_document,
        summarize_long_document,
        extract_from_logs,
        read_buffer,
        clear_buffer,
        save_buffer_to_volume,
        load_text_from_volume,
    ]
    if extra_tools:
        tools.extend(extra_tools)
    return tools


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def list_react_tool_names(tools: Iterable[Callable[..., Any]]) -> list[str]:
    """Return stable tool names for display / debugging."""
    return [getattr(tool, "__name__", str(tool)) for tool in tools]
