"""Simplified delegate_to_rlm tool for RLM delegation in a Daytona sandbox.

Registers module-level ``delegate_to_rlm`` tools marked with ``@tool_fn`` so
that ``discover_tools()`` can collect them.

The ``delegate_to_rlm`` and ``delegate_to_rlm_batched`` functions require a
Daytona ``interpreter`` to be passed directly.  The caller (e.g. the agent
runtime or test harness) is responsible for providing the interpreter
instance.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

from fleet_rlm.runtime.content.preview import head_tail_preview
from fleet_rlm.runtime.modules.factory import build_recursive_subquery_rlm
from fleet_rlm.runtime.tools._marker import tool_fn

logger = logging.getLogger(__name__)

_BROKER_ERROR_MARKER = "Broker server failed to start"


@tool_fn
def delegate_to_rlm(
    query: str,
    context: str = "",
    document_url: str | None = None,
    *,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Delegate a query to a recursive dspy.RLM running in a Daytona sandbox.

    Creates or reuses an existing Daytona sandbox session, constructs a
    ``dspy.RLM`` with the provided interpreter, executes the query, and returns
    a structured result dict with ``status`` and ``answer``.

    Use this for one child task. For multiple independent child tasks, prefer
    ``delegate_to_rlm_batched`` so siblings can run concurrently. When work is
    already inside one Daytona sandbox, prefer ``execute_code`` with
    ``llm_query_batched()`` or ``sub_rlm_batched()`` so Python can batch and
    aggregate inside the RLM loop.

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
        interpreter: Daytona interpreter instance.  Must be provided as a
            keyword argument.

    Returns:
        A dict with:
        - ``status``: ``"ok"`` on success, ``"error"`` on failure.
        - ``answer``: The RLM result string (present when ``status == "ok"``).
        - ``error``: Error message string (present when ``status == "error"``).

    Raises:
        RuntimeError: When called without a bound interpreter.
    """
    if interpreter is None:
        raise RuntimeError(
            "delegate_to_rlm requires a Daytona interpreter. "
            "Pass the interpreter as a keyword argument."
        )

    llm_budget = _remaining_llm_budget(interpreter)
    if llm_budget <= 0:
        return {
            "status": "error",
            "reason": "budget_exhausted",
            "error": "LLM call budget exhausted - cannot spawn delegate_to_rlm child.",
        }

    return _run_delegate_child(
        interpreter,
        query,
        context,
        document_url,
        llm_budget,
    )


@tool_fn
def delegate_to_rlm_batched(
    queries: list[str],
    context: str = "",
    document_url: str | None = None,
    *,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Delegate independent child RLM tasks concurrently.

    Use this when the top-level agent has already identified independent
    analyses (for example Child A, Child B, Child C) and should fan them out
    directly instead of making several sequential ``delegate_to_rlm`` calls.

    Prefer sandbox-side batching when the work is already inside one Daytona
    RLM: use ``execute_code`` with ``llm_query_batched()`` for lightweight
    semantic prompts over sandbox data, or ``sub_rlm_batched()`` for multiple
    recursive child RLM tasks from generated Python code.

    Args:
        queries: Ordered list of independent child RLM prompts.
        context: Shared context supplied to every child.
        document_url: Optional HTTP(S) document to stage for each child.
        interpreter: Daytona interpreter instance.  Must be provided as a
            keyword argument.

    Returns:
        A dict with ``status`` and ordered successful ``results``. When one or
        more children fail, ``status`` is ``"error"`` and ``errors`` contains
        per-query diagnostics while successful siblings remain in ``results``.

    Raises:
        RuntimeError: When called without a bound interpreter.
    """
    if interpreter is None:
        raise RuntimeError(
            "delegate_to_rlm_batched requires a Daytona interpreter. "
            "Pass the interpreter as a keyword argument."
        )

    normalized_queries = [str(query) for query in queries or []]
    if not normalized_queries:
        return {"status": "ok", "results": []}
    blank_indexes = [
        index for index, query in enumerate(normalized_queries) if not query.strip()
    ]
    if blank_indexes:
        return {
            "status": "error",
            "reason": "invalid_query",
            "results": [],
            "errors": [
                {
                    "index": index,
                    "query": normalized_queries[index],
                    "reason": "invalid_query",
                    "error": "delegate_to_rlm_batched queries cannot be empty.",
                }
                for index in blank_indexes
            ],
        }

    remaining_budget = _remaining_llm_budget(interpreter)
    try:
        leases = _delegate_budget_leases(remaining_budget, len(normalized_queries))
    except RuntimeError as exc:
        return {
            "status": "error",
            "reason": "budget_exhausted",
            "results": [],
            "errors": [
                {
                    "index": index,
                    "query": query,
                    "reason": "budget_exhausted",
                    "error": str(exc),
                }
                for index, query in enumerate(normalized_queries)
            ],
        }

    result_by_index: dict[int, dict[str, Any]] = {}
    error_by_index: dict[int, dict[str, Any]] = {}
    workers = max(1, min(len(normalized_queries), 4))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {
            pool.submit(
                copy_context().run,
                _run_delegate_child,
                interpreter,
                query,
                context,
                document_url,
                leases[index],
            ): index
            for index, query in enumerate(normalized_queries)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            query = normalized_queries[index]
            try:
                raw_child_result = future.result()
                child_result = (
                    raw_child_result
                    if isinstance(raw_child_result, dict)
                    else {"status": "error", "error": str(raw_child_result)}
                )
            except Exception as exc:
                child_result = {"status": "error", "error": str(exc)}
            child_result = cast(dict[str, Any], child_result)

            if child_result.get("status") == "ok":
                result_by_index[index] = {
                    "query": query,
                    "answer": str(child_result.get("answer", "")),
                }
                continue

            error_by_index[index] = {
                "index": index,
                "query": query,
                "reason": str(child_result.get("reason", "child_error")),
                "error": str(child_result.get("error", "unknown child error")),
            }

    results = [
        result_by_index[index]
        for index in range(len(normalized_queries))
        if index in result_by_index
    ]
    errors = [
        error_by_index[index]
        for index in range(len(normalized_queries))
        if index in error_by_index
    ]
    if errors:
        return {"status": "error", "results": results, "errors": errors}
    return {"status": "ok", "results": results}


def _run_delegate_child(
    interpreter: Any,
    query: str,
    context: str,
    document_url: str | None,
    llm_budget: int,
) -> dict[str, Any]:
    """Build, run, validate, and clean up one delegated child RLM."""
    child = None
    started_at = time.time()
    try:
        child = interpreter.build_delegate_child(remaining_llm_budget=llm_budget)
        _install_delegate_budget_lease(interpreter, child, llm_budget)
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
                int(llm_budget),
            ),
        )
        rlm = build_recursive_subquery_rlm(
            interpreter=child,
            max_iterations=max_iterations,
            max_llm_calls=llm_budget,
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

        _persist_child_trace(
            interpreter,
            query,
            answer,
            prediction,
            started_at,
        )
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


def _delegate_budget_leases(remaining: int, child_count: int) -> list[int]:
    if child_count <= 0:
        return []
    if remaining < child_count:
        raise RuntimeError(
            "LLM call budget exhausted - cannot spawn "
            f"{child_count} delegate_to_rlm children with only {remaining} "
            "semantic call(s) remaining."
        )
    base, extra = divmod(remaining, child_count)
    return [base + (1 if index < extra else 0) for index in range(child_count)]


def _install_delegate_budget_lease(
    interpreter: Any,
    child: Any,
    lease: int,
) -> None:
    install = getattr(interpreter, "_install_child_budget_lease", None)
    if callable(install):
        install(child, lease)
    else:
        setattr(child, "max_llm_calls", max(1, int(lease)))
    metadata = getattr(child, "child_isolation_metadata", None)
    if isinstance(metadata, dict):
        metadata["llm_budget_lease"] = max(1, int(lease))


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
    try:
        session = child._ensure_session_sync()
        _record_child_sandbox_id(child)
        written_path = session.write_file(doc_path, doc_text)
    except Exception as exc:
        logger.warning(
            "delegate_to_rlm: failed to persist document from %s to child: %s",
            stripped_url,
            exc,
        )
        fallback = doc_text[:embed_threshold]
        doc_snippet = (
            f"\n\n--- Document fetched from {stripped_url} "
            f"({char_count} chars; truncated after {len(fallback)} chars because "
            "sandbox staging failed) ---\n"
            f"{fallback}\n--- End of truncated document ---"
        )
        return (base_context + doc_snippet).strip()
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
    volume_name = str(getattr(child, "volume_name", "") or "").strip()
    if volume_name:
        return False
    session = getattr(child, "_session", None)
    session_repo_url = str(getattr(session, "repo_url", "") or "").strip()
    session_volume_name = str(getattr(session, "volume_name", "") or "").strip()
    return not session_repo_url and not session_volume_name


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
        "implementation",
        "inspect",
        "architecture",
        "sandbox",
        "budget",
        "sub_rlm",
        "delegate_to_rlm",
        "daytona",
        "dspy",
        "fleet",
        "interpreter",
        "rlm",
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


def _persist_child_trace(
    interpreter: Any,
    query: str,
    answer: str,
    prediction: Any,
    started_at: float,
) -> None:
    """Persist child RLM trajectory to NeonDB via the host repository."""
    import asyncio
    import uuid as _uuid

    repository = getattr(interpreter, "_host_repository", None)
    identity = getattr(interpreter, "_host_identity", None)
    run_id = getattr(interpreter, "_host_run_id", None)
    if repository is None or identity is None or run_id is None:
        return

    trajectory = getattr(prediction, "trajectory", None)
    payload: dict[str, Any] = {"query": query}
    answer_preview: str | None = None
    if answer:
        answer_preview, answer_length = head_tail_preview(answer, max_chars=500)
        payload["answer_preview"] = answer_preview
        payload["answer_length"] = answer_length
    if isinstance(trajectory, dict):
        payload["trajectory"] = trajectory
    elif isinstance(trajectory, list):
        payload["trajectory"] = trajectory

    latency_ms = int((time.time() - started_at) * 1000)
    trace_id = f"rlm-child-{_uuid.uuid4().hex[:12]}"

    try:
        asyncio.run(
            repository.store_rlm_trace(
                tenant_id=identity.tenant_id,
                run_id=run_id,
                trace_id=trace_id,
                workspace_id=identity.workspace_id,
                summary_text=answer_preview,
                payload_json=payload,
                latency_ms=latency_ms,
            )
        )
    except Exception as exc:
        logger.warning("Failed to persist RLM child trace: %s", exc)


__all__ = ["delegate_to_rlm", "delegate_to_rlm_batched"]
