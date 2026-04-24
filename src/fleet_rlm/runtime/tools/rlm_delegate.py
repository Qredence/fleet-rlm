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
from contextvars import ContextVar
from typing import Any

from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm
from fleet_rlm.runtime.tools._marker import tool_fn

logger = logging.getLogger(__name__)

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

    # If a document URL is provided, fetch it on the host and make it
    # available to the sandbox RLM. Small documents are embedded directly
    # in context; large documents are written to the Daytona volume.
    resolved_context = context
    _VOLUME_EMBED_THRESHOLD = 100_000  # chars
    if document_url and document_url.strip().startswith(("http://", "https://")):
        from fleet_rlm.runtime.tools.document_tools import fetch_document_text

        fetch_result = fetch_document_text(document_url.strip())
        if fetch_result.get("status") == "ok":
            doc_text = fetch_result["text"]
            char_count = fetch_result["char_count"]

            if char_count <= _VOLUME_EMBED_THRESHOLD:
                # Small document: embed directly in context (fast path)
                logger.info(
                    "delegate_to_rlm: embedding document from %s (%d chars)",
                    document_url,
                    char_count,
                )
                doc_snippet = (
                    f"\n\n--- Document fetched from {document_url} "
                    f"({char_count} chars) ---\n{doc_text}\n--- End of document ---"
                )
                resolved_context = (context + doc_snippet).strip()
            else:
                # Large document: persist to Daytona volume for sandbox access
                doc_hash = hashlib.sha256(document_url.strip().encode()).hexdigest()[
                    :12
                ]
                doc_filename = f"doc_{doc_hash}.txt"
                volume_path = (
                    f"{interpreter.volume_mount_path}/artifacts/{doc_filename}"
                )

                # Ensure session is started so we have a sandbox handle
                if not getattr(interpreter, "_started", False):
                    interpreter.start()

                try:
                    session = interpreter._ensure_session_sync()
                    session.write_file(volume_path, doc_text)
                    logger.info(
                        "delegate_to_rlm: persisted document from %s to %s (%d chars)",
                        document_url,
                        volume_path,
                        char_count,
                    )
                    resolved_context = (
                        context
                        + f"\n\nA document ({char_count} chars) from {document_url} "
                        f"is available in the sandbox at: {volume_path}\n"
                        f"Read it with Python file I/O (e.g., "
                        f"open('{volume_path}').read())."
                    ).strip()
                except Exception as exc:
                    logger.warning(
                        "delegate_to_rlm: failed to write document to volume: %s",
                        exc,
                    )
                    # Fallback: embed what we can
                    truncated = doc_text[:_VOLUME_EMBED_THRESHOLD]
                    resolved_context = (
                        context + f"\n\n--- Document from {document_url} "
                        f"({char_count} chars, truncated to {_VOLUME_EMBED_THRESHOLD}) ---\n"
                        f"{truncated}\n--- End of truncated document ---"
                    ).strip()
        else:
            err = fetch_result.get("error", "unknown error")
            logger.warning(
                "delegate_to_rlm: failed to pre-fetch document %s: %s",
                document_url,
                err,
            )
            resolved_context = (
                context
                + f"\n\nNote: Attempted to pre-fetch {document_url} but failed: {err}"
            ).strip()

    # Ensure the sandbox session is started (creates or reuses the session).
    if not getattr(interpreter, "_started", False):
        interpreter.start()

    rlm = build_recursive_subquery_rlm(
        interpreter=interpreter,
        max_iterations=20,
        max_llm_calls=50,
        verbose=bool(getattr(interpreter, "verbose", False)),
    )

    try:
        prediction = rlm(prompt=query, context=resolved_context)
        answer = str(getattr(prediction, "answer", "") or "")
    except Exception as exc:
        logger.warning("delegate_to_rlm execution failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    # The broker swallows infrastructure failures as "[Error] ..." strings
    # inside the RLM trajectory.  Detect them here so the calling agent gets
    # an actionable error rather than a misleading status:ok with a useless
    # answer, which would otherwise waste several minutes of retries.
    broker_error_marker = "Broker server failed to start"
    if broker_error_marker in answer:
        error_msg = f"Daytona broker unavailable: {answer[:200]}"
        logger.warning(
            "delegate_to_rlm: broker failure detected in answer: %s", error_msg
        )
        return {"status": "error", "error": error_msg}

    return {"status": "ok", "answer": answer}


__all__ = ["delegate_to_rlm", "set_delegate_interpreter"]
