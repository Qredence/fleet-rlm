"""Simplified delegate_to_rlm tool for RLM delegation in a Daytona sandbox.

Registers a single module-level ``delegate_to_rlm`` function marked with
``@tool_fn`` so that ``discover_tools()`` can collect it.

The tool uses a ``contextvars.ContextVar`` to hold the active Daytona
interpreter for the current agent turn.  Call ``set_delegate_interpreter()``
before invoking the tool to inject the interpreter.  Calling without a set
interpreter raises ``RuntimeError``.

Usage within an agent runtime::

    from fleet_rlm.runtime.tools.rlm_delegate import set_delegate_interpreter
    token = set_delegate_interpreter(interpreter)
    try:
        result = delegate_to_rlm("my query", "optional context")
    finally:
        _delegate_interpreter.reset(token)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm
from fleet_rlm.runtime.tools._marker import tool_fn

logger = logging.getLogger(__name__)

_BROKER_ERROR_MARKER = "Broker server failed to start"

# Context variable holding the Daytona interpreter for the active agent turn.
# Set by the agent runtime before invoking the agent so tool calls can
# access the interpreter without requiring a closure.
_delegate_interpreter: ContextVar[Any | None] = ContextVar(
    "rlm_delegate_interpreter", default=None
)


def set_delegate_interpreter(interpreter: Any | None) -> Any:
    """Set the active Daytona interpreter for RLM delegation.

    The returned token can be passed to ``_delegate_interpreter.reset(token)``
    to restore the previous value (useful in tests and nested contexts).

    Args:
        interpreter: Daytona interpreter instance, or ``None`` to clear.

    Returns:
        A ``contextvars.Token`` for resetting the variable.
    """
    return _delegate_interpreter.set(interpreter)


@tool_fn
def delegate_to_rlm(
    query: str,
    context: str = "",
    document_url: str = "",
) -> dict[str, Any]:
    """Delegate a query to a recursive dspy.RLM running in a Daytona sandbox.

    Creates or reuses an existing Daytona sandbox session, constructs a
    ``dspy.RLM`` with the active interpreter, executes the query, and returns
    a structured result dict with ``status`` and ``answer``.

    The interpreter must be set via :func:`set_delegate_interpreter` before
    invoking this tool (or via the surrounding agent runtime context that
    initialises the Daytona interpreter).

    When ``document_url`` is provided, the document is fetched and extracted on
    the host before the RLM runs.  The full text is injected into the RLM
    context so sandbox code can access it without a separate download step.
    This is the correct way to analyse remote documents: pass the URL here
    rather than using ``load_document`` first (which only stores to the host
    document cache, not the sandbox).

    Args:
        query: The query to execute in the recursive RLM.
        context: Optional additional context string for the query.
        document_url: Optional HTTP(S) URL of a document to fetch and inject
            into the RLM context before execution.

    Returns:
        A dict with:
        - ``status``: ``"ok"`` on success, ``"error"`` on failure.
        - ``answer``: The RLM result string (present when ``status == "ok"``).
        - ``error``: Error message string (present when ``status == "error"``).

    Raises:
        RuntimeError: When called without a bound interpreter in the
            current context (i.e., ``set_delegate_interpreter`` was not called).
    """
    interpreter = _delegate_interpreter.get()
    if interpreter is None:
        raise RuntimeError(
            "delegate_to_rlm requires a bound Daytona interpreter. "
            "Set the interpreter via set_delegate_interpreter() or run within "
            "an agent runtime context that initialises the interpreter."
        )

    remaining_budget = _remaining_llm_budget(interpreter)
    if remaining_budget <= 0:
        return {
            "status": "error",
            "reason": "budget_exhausted",
            "error": "LLM call budget exhausted - cannot spawn delegate_to_rlm child.",
        }

    child = None
    try:
        child = interpreter.build_delegate_child(remaining_llm_budget=remaining_budget)
        resolved_context = _resolve_delegate_context(
            child=child,
            query=query,
            base_context=context,
            document_url=document_url,
        )

        if not getattr(child, "_started", False):
            child.start()
        _record_child_sandbox_id(child)

        max_iterations = max(
            1,
            min(
                int(getattr(child, "rlm_max_iterations", 20)),
                int(remaining_budget),
            ),
        )
        rlm = build_recursive_subquery_rlm(
            interpreter=child,
            max_iterations=max_iterations,
            max_llm_calls=remaining_budget,
            verbose=bool(
                getattr(child, "verbose", getattr(interpreter, "verbose", False))
            ),
            sub_lm=getattr(child, "sub_lm", None),
        )

        logger.info(
            "delegate_to_rlm: running child RLM with isolation=%s",
            getattr(child, "child_isolation_metadata", {}),
        )
        prediction = rlm(prompt=query, context=resolved_context)
        raw_answer = getattr(prediction, "answer", None)
        answer = "" if raw_answer is None else str(raw_answer)
    except Exception as exc:
        logger.warning("delegate_to_rlm execution failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        if child is not None:
            try:
                child.shutdown()
                metadata = getattr(child, "child_isolation_metadata", None)
                if isinstance(metadata, dict):
                    metadata["cleanup_status"] = "deleted"
                logger.info("delegate_to_rlm: child cleanup complete: %s", metadata)
            except Exception as cleanup_exc:
                metadata = getattr(child, "child_isolation_metadata", None)
                if isinstance(metadata, dict):
                    metadata["cleanup_status"] = "failed"
                    metadata["cleanup_error"] = str(cleanup_exc)
                logger.warning("delegate_to_rlm: child cleanup failed: %s", cleanup_exc)

    failure = _delegate_failure(prediction=prediction, raw_answer=raw_answer)
    if failure is not None:
        metadata = getattr(child, "child_isolation_metadata", None)
        if isinstance(metadata, dict):
            metadata["error_reason"] = failure["reason"]
        logger.warning("delegate_to_rlm: child failure detected: %s", failure)
        return {"status": "error", **failure}

    return {"status": "ok", "answer": answer}


def _remaining_llm_budget(interpreter: Any) -> int:
    remaining_fn = getattr(interpreter, "_remaining_llm_budget", None)
    if callable(remaining_fn):
        remaining = int(remaining_fn())
    else:
        remaining = int(getattr(interpreter, "max_llm_calls", 50))
    return max(0, remaining)


def _resolve_delegate_context(
    *,
    child: Any,
    query: str,
    base_context: str,
    document_url: Any,
) -> str:
    resolved_context = base_context
    embed_threshold = 100_000  # chars
    stripped_url = (document_url or "").strip()
    if not stripped_url.startswith(("http://", "https://")):
        if not _uses_local_host_checkout(child):
            return resolved_context
        return _append_local_workspace_context(
            child=child,
            query=query,
            resolved_context=resolved_context,
        )

    from fleet_rlm.runtime.tools.document_tools import fetch_document_text

    fetch_result = fetch_document_text(stripped_url)
    if fetch_result.get("status") != "ok":
        err = fetch_result.get("error", "unknown error")
        logger.warning(
            "delegate_to_rlm: failed to pre-fetch document %s: %s",
            stripped_url,
            err,
        )
        return (
            base_context
            + f"\n\nNote: Attempted to pre-fetch {stripped_url} but failed: {err}"
        ).strip()

    doc_text = fetch_result["text"]
    char_count = int(fetch_result["char_count"])
    if char_count <= embed_threshold:
        logger.info(
            "delegate_to_rlm: embedding document from %s (%d chars)",
            stripped_url,
            char_count,
        )
        doc_snippet = (
            f"\n\n--- Document fetched from {stripped_url} "
            f"({char_count} chars) ---\n{doc_text}\n--- End of document ---"
        )
        return (base_context + doc_snippet).strip()

    doc_hash = hashlib.sha256(stripped_url.encode()).hexdigest()[:12]
    doc_path = f"artifacts/rlm-inputs/doc_{doc_hash}.txt"
    if not getattr(child, "_started", False):
        child.start()
    session = child._ensure_session_sync()
    _record_child_sandbox_id(child)
    written_path = session.write_file(doc_path, doc_text)
    logger.info(
        "delegate_to_rlm: persisted document from %s to child path %s (%d chars)",
        stripped_url,
        written_path,
        char_count,
    )
    return (
        base_context + f"\n\nA document ({char_count} chars) from {stripped_url} "
        f"is available in this child sandbox at: {written_path}\n"
        f"Read it with Python file I/O (e.g., open('{written_path}').read())."
    ).strip()


def _delegate_failure(
    *,
    prediction: Any,
    raw_answer: Any,
) -> dict[str, str] | None:
    if _contains_marker(prediction, _BROKER_ERROR_MARKER):
        return {
            "reason": "broker_unavailable",
            "error": "Daytona broker unavailable during child RLM execution.",
        }
    if raw_answer is None:
        return {
            "reason": "null_answer",
            "error": "Child RLM completed without SUBMIT(answer=...).",
        }
    return None


def _contains_marker(value: Any, marker: str, *, _depth: int = 0) -> bool:
    if _depth > 6:
        return False
    if isinstance(value, str):
        return marker in value
    if value is None or isinstance(value, (bool, int, float)):
        return False
    if isinstance(value, Mapping):
        return any(
            _contains_marker(item, marker, _depth=_depth + 1)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_marker(item, marker, _depth=_depth + 1) for item in value)
    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        filtered = {
            key: item
            for key, item in value_dict.items()
            if key
            in {"answer", "reasoning", "code", "trajectory", "repl_history", "history"}
        }
        if _contains_marker(filtered, marker, _depth=_depth + 1):
            return True
    if not isinstance(value, Mock):
        for attr in (
            "answer",
            "reasoning",
            "code",
            "trajectory",
            "repl_history",
            "history",
        ):
            try:
                attr_value = getattr(value, attr)
            except Exception:
                continue
            if _contains_marker(attr_value, marker, _depth=_depth + 1):
                return True
    return False


def _append_local_workspace_context(
    *,
    child: Any,
    query: str,
    resolved_context: str,
) -> str:
    snapshot = _build_local_workspace_snapshot(query=query, context=resolved_context)
    if snapshot is None:
        return resolved_context
    if not getattr(child, "_started", False):
        child.start()
    session = child._ensure_session_sync()
    _record_child_sandbox_id(child)
    snapshot_path = session.write_file(
        "artifacts/rlm-inputs/local_workspace_snapshot.txt",
        snapshot,
    )
    metadata = getattr(child, "child_isolation_metadata", None)
    if isinstance(metadata, dict):
        metadata["local_workspace_snapshot_path"] = snapshot_path
    return (
        resolved_context
        + "\n\n--- Local workspace snapshot ---\n"
        + "The parent runtime is using a local host workspace that is not mounted "
        + "inside this isolated child sandbox. A curated text snapshot of relevant "
        + f"repository files is available at: {snapshot_path}\n"
        + "Inspect it with Python file I/O and cite the original file paths shown "
        + "inside the snapshot. Do not cite dependency-cache files as project evidence."
    ).strip()


def _uses_local_host_checkout(child: Any) -> bool:
    repo_url = str(getattr(child, "repo_url", "") or "").strip()
    if repo_url:
        return False
    session = getattr(child, "_session", None)
    session_repo_url = str(getattr(session, "repo_url", "") or "").strip()
    return not session_repo_url


def _build_local_workspace_snapshot(*, query: str, context: str) -> str | None:
    if not _needs_local_workspace_snapshot(query + "\n" + context):
        return None
    root = Path.cwd()
    if not _looks_like_project_root(root):
        return None

    candidates = _workspace_snapshot_candidates(root)
    if not candidates:
        return None

    terms = _snapshot_terms(query + "\n" + context)
    scored_candidates = [(_snapshot_score(path, terms), path) for path in candidates]
    ranked = sorted(scored_candidates, key=lambda item: item[0], reverse=True)
    selected = [path for score, path in ranked if score > 0][:24]
    if not selected:
        selected = [path for _, path in ranked[:12]]

    manifest = "\n".join(str(path.relative_to(root)) for path in candidates[:400])
    sections = [
        "# Fleet-RLM Local Workspace Snapshot",
        f"Repository root: {root}",
        "",
        "## File manifest",
        manifest,
        "",
        "## Selected file excerpts",
    ]
    remaining_chars = 180_000
    for path in selected:
        rel = path.relative_to(root)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                excerpt = handle.read(12_000)
        except OSError:
            continue
        section = f"\n\n--- FILE: {rel} ---\n{excerpt}"
        if len(section) > remaining_chars:
            break
        sections.append(section)
        remaining_chars -= len(section)
    return "\n".join(sections)


def _looks_like_project_root(path: Path) -> bool:
    return any((path / marker).exists() for marker in ("pyproject.toml", ".git"))


def _needs_local_workspace_snapshot(text: str) -> bool:
    lowered = text.lower()
    indicators = {
        "codebase",
        "repository",
        "repo",
        "source",
        "implementation",
        "inspect",
        "files",
        "architecture",
        "sandbox",
        "budget",
        "session",
        "restore",
        "recursive",
        "sub_rlm",
        "delegate_to_rlm",
        "daytona",
    }
    return any(indicator in lowered for indicator in indicators)


def _workspace_snapshot_candidates(root: Path) -> list[Path]:
    allowed_roots = ["src", "tests", "scripts"]
    top_level = ["pyproject.toml", "AGENTS.md", "README.md"]
    suffixes = {".py", ".toml", ".yaml", ".yml", ".md"}
    ignored_parts = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }

    paths: list[Path] = []
    for name in top_level:
        path = root / name
        if path.is_file():
            paths.append(path)
    for root_name in allowed_roots:
        base = root / root_name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [name for name in dirnames if name not in ignored_parts]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix in suffixes:
                    paths.append(path)
    return sorted(paths, key=lambda p: str(p.relative_to(root)))


def _snapshot_terms(text: str) -> set[str]:
    terms = {
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())
        if token
        not in {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "into",
            "whether",
        }
    }
    if "rlm" in text.lower() or "recursive" in text.lower():
        terms.update(
            {
                "rlm",
                "recursive",
                "delegate",
                "sub_rlm",
                "budget",
                "sandbox",
                "interpreter",
                "session",
                "persistence",
                "restore",
            }
        )
    return terms


def _snapshot_score(path: Path, terms: set[str]) -> int:
    rel = str(path).lower()
    score = sum(5 for term in terms if term in rel)
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return score
    for term in terms:
        score += min(text.count(term), 20)
    return score


def _record_child_sandbox_id(child: Any) -> None:
    metadata = getattr(child, "child_isolation_metadata", None)
    if not isinstance(metadata, dict):
        return
    session = getattr(child, "_session", None)
    sandbox_id = getattr(session, "sandbox_id", None)
    if sandbox_id:
        metadata.setdefault("child_sandbox_id", sandbox_id)


__all__ = ["delegate_to_rlm", "set_delegate_interpreter"]
