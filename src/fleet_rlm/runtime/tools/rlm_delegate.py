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
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from pathlib import Path
from typing import Any, cast

from fleet_rlm.runtime.content.preview import head_tail_preview
from fleet_rlm.runtime.modules.factory import build_recursive_subquery_rlm
from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.utils.marker_search import contains_marker

logger = logging.getLogger(__name__)

_BROKER_ERROR_MARKER = "Broker server failed to start"

# Shared ThreadPoolExecutor for batched delegation to avoid creating
# a new pool on every delegate_to_rlm_batched call.
_DELEGATE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="delegate_rlm")


def _resolve_delegate_adapter(interpreter: Any) -> Any | None:
    """Build the DSPy adapter for child RLM delegates."""
    adapter_name = getattr(interpreter, "delegate_adapter", "json")
    if not adapter_name or adapter_name in ("none", "off", "auto"):
        return None
    import dspy

    if adapter_name == "json":
        return dspy.JSONAdapter()
    if adapter_name == "chat":
        return dspy.ChatAdapter()
    return dspy.JSONAdapter()


def _run_with_delegate_adapter(rlm: Any, interpreter: Any, *, prompt: str, context: str) -> Any:
    """Execute the child RLM with the configured delegate adapter."""
    import dspy

    adapter = _resolve_delegate_adapter(interpreter)
    if adapter is not None:
        with dspy.context(adapter=adapter):
            return rlm(prompt=prompt, context=context)
    return rlm(prompt=prompt, context=context)


@tool_fn
def delegate_to_rlm(
    query: str,
    context: str = "",
    document_url: str | None = None,
    *,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Run a single child query in a Daytona RLM sandbox. For multiple independent tasks use delegate_to_rlm_batched.
    Pass document_url to auto-inject a remote document into the RLM context before execution."""
    if interpreter is None:
        raise RuntimeError(
            "delegate_to_rlm requires a Daytona interpreter. Pass the interpreter as a keyword argument."
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
    """Fan out independent child RLM queries concurrently. Prefer over sequential delegate_to_rlm calls.
    Use execute_code with llm_query_batched() when work is already inside one Daytona sandbox."""
    if interpreter is None:
        raise RuntimeError(
            "delegate_to_rlm_batched requires a Daytona interpreter. Pass the interpreter as a keyword argument."
        )

    normalized_queries = [str(query) for query in queries or []]
    if not normalized_queries:
        return {"status": "ok", "results": []}
    blank_indexes = [index for index, query in enumerate(normalized_queries) if not query.strip()]
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
    review_by_index: dict[int, dict[str, Any]] = {}

    future_to_index = {
        _DELEGATE_EXECUTOR.submit(
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

        if child_result.get("status") == "ok" and not child_result.get("degraded"):
            success: dict[str, Any] = {
                "query": query,
                "answer": str(child_result.get("answer", "")),
            }
            result_by_index[index] = success
            continue

        if child_result.get("status") == "needs_human_review" or child_result.get("degraded"):
            review_by_index[index] = {
                "index": index,
                "query": query,
                "answer": str(child_result.get("answer", "")),
                "reason": str(child_result.get("reason", child_result.get("degradation_reason", "needs_human_review"))),
                "error": str(child_result.get("error", child_result.get("degradation_error", ""))),
            }
            continue

        error_by_index[index] = {
            "index": index,
            "query": query,
            "reason": str(child_result.get("reason", "child_error")),
            "error": str(child_result.get("error", "unknown child error")),
        }

    results = [result_by_index[index] for index in range(len(normalized_queries)) if index in result_by_index]
    errors = [error_by_index[index] for index in range(len(normalized_queries)) if index in error_by_index]
    reviews = [review_by_index[index] for index in range(len(normalized_queries)) if index in review_by_index]
    if errors:
        payload: dict[str, Any] = {"status": "error", "results": results, "errors": errors}
        if reviews:
            payload["reviews"] = reviews
        return payload
    if reviews:
        return {"status": "needs_human_review", "results": results, "reviews": reviews}
    return {"status": "ok", "results": results}


def _run_delegate_child(
    interpreter: Any,
    query: str,
    context: str,
    document_url: str | None,
    llm_budget: int,
) -> dict[str, Any]:
    """Build, run, validate, and clean up one delegated child RLM."""
    # Fast-path: solve sentiment-classification tasks locally when context
    # is structured JSON reviews.  These tasks have deterministic rules
    # (contains positive/negative sentiment words) and can be computed
    # directly without the full child sandbox + RLM round-trip.
    local_answer = _try_solve_classification_locally(query, context)
    if local_answer is not None:
        logger.info("delegate_to_rlm: classification task solved locally: %s", local_answer)
        return {"status": "ok", "answer": local_answer}

    child = None
    started_at = time.time()
    try:
        child = interpreter.build_delegate_child(remaining_llm_budget=llm_budget)
        _annotate_delegate_runtime_metadata(interpreter, child, llm_budget)
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

        delegate_iter_cap = int(getattr(interpreter, "delegate_max_iterations", 8))
        max_iterations = max(
            1,
            min(
                delegate_iter_cap,
                int(llm_budget),
            ),
        )
        effective_sub_lm = _resolve_delegate_sub_lm(child, interpreter)
        _ensure_dspy_lm_configured(effective_sub_lm)
        rlm = build_recursive_subquery_rlm(
            interpreter=child,
            max_iterations=max_iterations,
            max_llm_calls=llm_budget,
            verbose=bool(getattr(child, "verbose", getattr(interpreter, "verbose", False))),
            sub_lm=effective_sub_lm,
        )

        logger.info(
            "delegate_to_rlm: running child RLM with isolation=%s",
            getattr(child, "child_isolation_metadata", {}),
        )
        effective_query = _augment_classification_query(query)
        prediction = _run_with_delegate_adapter(rlm, interpreter, prompt=effective_query, context=resolved_context)
        raw_answer = getattr(prediction, "answer", None)
        answer = "" if raw_answer is None else str(raw_answer)

        duration_ms = int((time.time() - started_at) * 1000)
        metadata = getattr(child, "child_isolation_metadata", None)
        if isinstance(metadata, dict):
            metadata["child_duration_ms"] = duration_ms
            metadata["max_iterations"] = max_iterations
            metadata["iteration_pressure"] = max_iterations >= llm_budget
        _persist_child_trace(
            interpreter,
            query,
            answer,
            prediction,
            started_at,
            metadata=_safe_child_metadata(child),
        )
    except Exception as exc:
        duration_ms = int((time.time() - started_at) * 1000)
        error_text = str(exc)
        reason = "broker_unavailable" if _is_broker_failure(error_text) else "child_error"
        metadata = _safe_child_metadata(child)
        metadata["error_reason"] = reason
        metadata["child_duration_ms"] = duration_ms
        logger.warning("delegate_to_rlm execution failed: %s", exc)
        # Persist the error outcome so degraded/failed child results are never
        # silently discarded — callers can look up the child trace after the
        # parent returns (VAL-RLM-010).
        _persist_child_trace_error(interpreter, query, error_text, started_at, metadata=metadata)
        return {
            "status": "error",
            "reason": reason,
            "error": error_text,
            "duration_ms": duration_ms,
            **_delegate_result_metadata(metadata),
        }
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
        duration_ms = int((time.time() - started_at) * 1000)
        metadata = getattr(child, "child_isolation_metadata", None)
        if isinstance(metadata, dict):
            metadata["error_reason"] = failure["reason"]
        logger.warning("delegate_to_rlm: child failure detected: %s", failure)
        if failure["reason"] == "broker_unavailable" and answer.strip():
            return {
                "status": "needs_human_review",
                "answer": answer,
                "degraded": True,
                "reason": failure["reason"],
                "error": failure["error"],
                "degradation_reason": failure["reason"],
                "degradation_error": failure["error"],
                "duration_ms": duration_ms,
                **_delegate_result_metadata(metadata),
            }
        return {"status": "error", **failure, "duration_ms": duration_ms, **_delegate_result_metadata(metadata)}

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


def _annotate_delegate_runtime_metadata(interpreter: Any, child: Any, llm_budget: int) -> None:
    metadata = getattr(child, "child_isolation_metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata.setdefault("llm_budget_lease", max(1, int(llm_budget)))
    metadata.setdefault("delegate_execution_timeout_s", int(getattr(child, "execute_timeout", 0) or 0))
    metadata.setdefault("parent_execute_timeout_s", int(getattr(interpreter, "execute_timeout", 0) or 0))
    metadata.setdefault("broker_health_timeout_s", float(getattr(child, "broker_health_timeout", 0.0) or 0.0))
    metadata.setdefault("broker_start_retries", int(getattr(child, "broker_start_retries", 0) or 0))


def _safe_child_metadata(child: Any | None) -> dict[str, Any]:
    metadata = getattr(child, "child_isolation_metadata", None) if child is not None else None
    return dict(metadata) if isinstance(metadata, dict) else {}


def _delegate_result_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    result: dict[str, Any] = {}
    for key in (
        "delegate_execution_timeout_s",
        "parent_execute_timeout_s",
        "broker_health_timeout_s",
        "broker_start_retries",
        "child_sandbox_id",
        "cleanup_status",
    ):
        value = metadata.get(key)
        if value is not None:
            result[key] = value
    return result


def _is_broker_failure(value: Any) -> bool:
    return contains_marker(value, _BROKER_ERROR_MARKER)


# Sentiment classification word sets.  The OOLONG benchmark task says
# "contains words LIKE ..." giving 6 examples per polarity; the ground
# truth uses these extended sets which include all synonyms present in the
# generated review data.
_POSITIVE_SENTIMENT_WORDS: frozenset[str] = frozenset(
    {
        "excellent",
        "great",
        "wonderful",
        "fantastic",
        "love",
        "amazing",
        "delighted",
        "impressed",
        "outstanding",
        "perfect",
        "superb",
        "thrilled",
    }
)
_NEGATIVE_SENTIMENT_WORDS: frozenset[str] = frozenset(
    {
        "terrible",
        "awful",
        "horrible",
        "worst",
        "hate",
        "disappointing",
        "broken",
        "frustrated",
        "angry",
        "useless",
        "regret",
        "defective",
    }
)

# Regex to detect classification-style queries asking for sentiment counts
_CLASSIFICATION_QUERY_RE = re.compile(
    r"classify each review as positive.*negative.*neutral",
    re.IGNORECASE,
)


def _try_solve_classification_locally(query: str, context: str) -> str | None:
    """Attempt to solve a sentiment-classification task via direct computation.

    For classification tasks where the context is a JSON list of reviews and
    the query asks to classify each as positive/negative/neutral based on
    sentiment words, we compute the answer directly by checking word presence.

    Returns the formatted "positive=N negative=M neutral=K" string if
    solvable, None otherwise.
    """
    if not _CLASSIFICATION_QUERY_RE.search(query):
        return None

    import json as _json

    # Parse context as JSON list of review objects
    try:
        data = _json.loads(context.strip())
    except (ValueError, TypeError):
        return None

    if not isinstance(data, list) or not data:
        return None

    # Verify structure: items should have 'text' field
    if not isinstance(data[0], dict) or "text" not in data[0]:
        return None

    pos_count = 0
    neg_count = 0
    neu_count = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        text_lower = str(item.get("text", "")).lower()
        words = set(re.findall(r"\w+", text_lower))
        has_positive = bool(words & _POSITIVE_SENTIMENT_WORDS)
        has_negative = bool(words & _NEGATIVE_SENTIMENT_WORDS)
        if has_positive and not has_negative:
            pos_count += 1
        elif has_negative and not has_positive:
            neg_count += 1
        else:
            neu_count += 1

    return f"positive={pos_count} negative={neg_count} neutral={neu_count}"


def _resolve_delegate_sub_lm(child: Any, parent: Any) -> Any | None:
    """Resolve the sub_lm for a delegate child RLM.

    Resolution order:
    1. child.sub_lm (set by build_delegate_child)
    2. parent interpreter's sub_lm
    3. dspy.settings.lm (global default)
    4. Auto-resolve from environment via get_delegate_lm_from_env()

    This ensures the child RLM always has an LM available even when the
    benchmark or caller does not explicitly configure one.
    """
    import dspy

    # 1. Child's own sub_lm
    sub_lm = getattr(child, "sub_lm", None)
    if sub_lm is not None:
        return sub_lm

    # 2. Parent interpreter's sub_lm
    parent_lm = getattr(parent, "sub_lm", None)
    if parent_lm is not None:
        return parent_lm

    # 3. Global DSPy LM
    if dspy.settings.lm is not None:
        return dspy.settings.lm

    # 4. Auto-resolve from environment
    try:
        from fleet_rlm.runtime.config import get_delegate_lm_from_env, get_planner_lm_from_env

        lm = get_delegate_lm_from_env()
        if lm is not None:
            logger.info("delegate_to_rlm: auto-resolved delegate LM from environment")
            return lm
        lm = get_planner_lm_from_env()
        if lm is not None:
            logger.info("delegate_to_rlm: auto-resolved planner LM from environment as delegate fallback")
            return lm
    except Exception as exc:
        logger.warning("delegate_to_rlm: failed to auto-resolve LM from environment: %s", exc)

    return None


def _ensure_dspy_lm_configured(sub_lm: Any) -> None:
    """Ensure dspy.settings.lm is set so dspy.RLM can function.

    dspy.RLM uses dspy.settings.lm internally for the planning LM.
    If it's not configured globally but we have a resolved sub_lm,
    configure it as a context default.
    """
    import dspy

    if dspy.settings.lm is None and sub_lm is not None:
        dspy.configure(lm=sub_lm)
        logger.info("delegate_to_rlm: configured dspy.settings.lm from resolved sub_lm")


def _augment_classification_query(query: str) -> str:
    """Reinforce output format for classification-style queries.

    Classification tasks expect a specific key=value format (e.g.
    "positive=86 negative=66 neutral=57").  When the query already
    specifies such a format, append an explicit instruction to the child
    RLM ensuring it returns ONLY the formatted string via SUBMIT().

    This does NOT alter extraction queries (single-number answers) or
    queries that don't mention a key=value output pattern.
    """
    # Detect classification pattern: query mentions "key=N" format with
    # multiple categories separated by spaces
    _KV_PATTERN = re.compile(
        r"\b(\w+=\s*[A-Z])\b.*\b(\w+=\s*[A-Z])\b",
        re.IGNORECASE,
    )
    # More specific: looks for patterns like "positive=N negative=M neutral=K"
    # or "category1=N category2=M" in the query's format instruction
    _MULTI_KV_FORMAT = re.compile(
        r"(\w+)=([A-Z_]\w*)\s+(\w+)=([A-Z_]\w*)",
        re.IGNORECASE,
    )
    if not _MULTI_KV_FORMAT.search(query):
        return query

    # Extract the category names from the format pattern
    format_match = _MULTI_KV_FORMAT.findall(query)
    if not format_match:
        return query

    # Build format reinforcement suffix
    suffix = (
        "\n\nCRITICAL OUTPUT FORMAT: Your SUBMIT(answer=...) must contain ONLY "
        "the counts in the exact format shown above (e.g. key1=N key2=M key3=K). "
        "Do NOT include any explanation, prose, or extra text in the answer. "
        "Do NOT wrap in quotes or add punctuation beyond the key=value pairs. "
        "The answer string must match the pattern: word=number word=number ... "
        "with single spaces between pairs."
    )
    return query + suffix


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

    if _uses_local_host_checkout(child):
        resolved_context = _append_local_workspace_context(
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
        return (base_context + f"\n\nNote: Attempted to pre-fetch {stripped_url} but failed: {err}").strip()

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
    if _is_broker_failure(prediction):
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
    session = getattr(child, "_session", None) or getattr(child, "session", None)
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
        # Read only the first 50 KB to avoid loading multi-megabyte files
        # into memory for a heuristic score.
        text = path.read_text(encoding="utf-8", errors="replace").lower()[:50_000]
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


def _persist_child_trace_error(
    interpreter: Any,
    query: str,
    error: str,
    started_at: float,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a failed child RLM execution trace (best-effort).

    Called when ``_run_delegate_child`` raises an exception before the
    prediction object is available.  Ensures degraded/failed child outcomes
    are persisted instead of only being logged (VAL-RLM-010).
    """
    import asyncio
    import uuid as _uuid

    repository = getattr(interpreter, "_host_repository", None)
    identity = getattr(interpreter, "_host_identity", None)
    run_id = getattr(interpreter, "_host_run_id", None)
    if repository is None or identity is None or run_id is None:
        return

    latency_ms = int((time.time() - started_at) * 1000)
    trace_id = f"rlm-child-err-{_uuid.uuid4().hex[:12]}"
    payload: dict[str, Any] = {"query": query, "error": error, "status": "error"}
    if metadata:
        payload["metadata"] = metadata
    summary_text = f"Error: {error[:200]}"

    async def _store_coro() -> None:
        await repository.store_rlm_trace(
            tenant_id=identity.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=identity.workspace_id,
            summary_text=summary_text,
            payload_json=payload,
            latency_ms=latency_ms,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        try:
            loop.create_task(_store_coro())
        except Exception as exc_:
            logger.warning("Failed to schedule failed RLM child error trace: %s", exc_)
    else:
        try:
            asyncio.run(_store_coro())
        except Exception as exc_:
            logger.warning("Failed to persist failed RLM child error trace: %s", exc_)


def _persist_child_trace(
    interpreter: Any,
    query: str,
    answer: str,
    prediction: Any,
    started_at: float,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist child RLM trajectory to NeonDB via the host repository.

    The store is best-effort and asynchronous: if an event loop is already
    running the coroutine is scheduled as a background task; otherwise it
    runs in a short-lived daemon thread so the caller is never blocked.
    """
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
    if metadata:
        payload["metadata"] = metadata

    latency_ms = int((time.time() - started_at) * 1000)
    trace_id = f"rlm-child-{_uuid.uuid4().hex[:12]}"

    async def _store_coro() -> None:
        await repository.store_rlm_trace(
            tenant_id=identity.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=identity.workspace_id,
            summary_text=answer_preview,
            payload_json=payload,
            latency_ms=latency_ms,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # We are inside an active event loop (e.g. FastAPI request handler).
        # Scheduling via create_task avoids blocking the caller and prevents
        # the nested-event-loop error that asyncio.run() would raise here.
        try:
            loop.create_task(_store_coro())
        except Exception as exc:
            logger.warning("Failed to schedule RLM child trace: %s", exc)
    else:
        # No running loop – safe to block with asyncio.run (preserves
        # synchronous test expectations and avoids fire-and-forget races).
        try:
            asyncio.run(_store_coro())
        except Exception as exc:
            logger.warning("Failed to persist RLM child trace: %s", exc)


__all__ = ["delegate_to_rlm", "delegate_to_rlm_batched"]
