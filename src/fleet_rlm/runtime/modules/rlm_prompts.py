"""RLM core-context assembly for sandboxed turn execution.

The task itself travels as the ``user_request`` input field (a native REPL
variable); this module only assembles the ``core_memory`` envelope: dynamic
turn guidance, compressed conversation context, and persisted core memory.
Static REPL guidance lives in the RLM signature docstrings
(:mod:`fleet_rlm.runtime.agent.signatures`).
"""

from __future__ import annotations

import os

from fleet_rlm.runtime.execution.final_artifact import output_format_guidance_for_task
from fleet_rlm.runtime.task_intent import implies_quote_retrieval, quote_retrieval_repl_guidance

_URL_REPL_ONLY_ENV = "FLEET_RLM_URL_REPL_ONLY"


def url_repl_only_enabled() -> bool:
    """When true, URL-document RLM disables llm_query (debug/smoke only)."""
    return os.environ.get(_URL_REPL_ONLY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def build_rlm_core_context(
    *,
    user_request: str,
    compressed_history: str,
    core_memory: str,
    url_document_mode: bool = False,
    large_context_mode: bool = False,
) -> str:
    """Assemble the ``core_memory`` envelope for one sandboxed RLM turn."""
    sections: list[str] = []
    output_guidance = output_format_guidance_for_task(user_request)
    if output_guidance:
        sections.append(output_guidance)
    if url_document_mode and url_repl_only_enabled():
        sections.append(
            "llm_query and llm_query_batched are disabled in this URL-document path; "
            "synthesize from Python inspection of the document variable."
        )
    if large_context_mode and implies_quote_retrieval(user_request):
        sections.append(quote_retrieval_repl_guidance())
    if compressed_history:
        sections.append("Compressed conversation context:\n" + compressed_history)
    if core_memory:
        sections.append("Core memory and active skill guidance:\n" + core_memory)
    return "\n\n".join(section for section in sections if section.strip())


__all__ = [
    "build_rlm_core_context",
    "url_repl_only_enabled",
]
