"""RLM REPL prompt assembly for variable-mode execution."""

from __future__ import annotations

import os

from fleet_rlm.runtime.execution.final_artifact import output_format_guidance_for_task
from fleet_rlm.runtime.task_intent import implies_quote_retrieval, quote_retrieval_repl_guidance

_URL_REPL_ONLY_ENV = "FLEET_RLM_URL_REPL_ONLY"

RLM_REPL_GUIDANCE = """RLM REPL guidance:
- Keep the task visible: solve the task stated at the top and repeated at the bottom of this prompt.
- Use Python variables instead of printing large inputs. Inspect slices, lengths, keywords, and structure with code.
- Treat available tools as ordinary Python callables. Their type hints and docstrings are the contract.
- For documentation URLs, first inspect headings, links, llms.txt, sitemap entries, and section samples with Python. Do not send an entire document to one semantic callback.
- If semantic callbacks such as llm_query are unavailable, finish from Python document inspection.
- Keep intermediate output bounded; print summaries or small samples, then call SUBMIT(...) for the final answer.
- Do not print or return credentials, environment variables, or hidden configuration values.
"""


def url_repl_only_enabled() -> bool:
    """When true, URL-document RLM disables llm_query (debug/smoke only)."""
    return os.environ.get(_URL_REPL_ONLY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def build_rlm_prompt_context(
    *,
    user_request: str,
    recent_history: str,
    compressed_history: str,
    core_memory: str,
    url_document_mode: bool,
    large_context_mode: bool = False,
) -> str:
    """Build a Fast-RLM-style prompt envelope for variable-mode DSPy RLM."""
    sections = [
        "Task:\n" + user_request,
        RLM_REPL_GUIDANCE,
    ]
    output_guidance = output_format_guidance_for_task(user_request)
    if output_guidance:
        sections.append(output_guidance)
    if url_document_mode:
        llm_guidance = (
            "- llm_query and llm_query_batched are disabled in this URL-document path; synthesize from Python inspection.\n"
            if url_repl_only_enabled()
            else "- Use llm_query/llm_query_batched on focused snippets after Python inspection; do not send the full document_text to one call.\n"
        )
        sections.append(
            "URL document variables:\n"
            "- source_url: canonical fetched URL string.\n"
            "- document_text: extracted source text; inspect it with Python rather than printing it wholesale.\n"
            "- source_metadata: fetch status, source metadata, and any bundled llms.txt/sitemap companions.\n"
            f"{llm_guidance}"
            "- history: structured dspy.History for prior turns."
        )
    if large_context_mode:
        sections.append(
            "Large context variables:\n"
            "- document_text: extracted local document body as a REPL variable when host extraction succeeded.\n"
            "- context_paths: host file paths (metadata only); do not open these paths directly in the sandbox.\n"
            "- When document_text is empty, read workspace .fleet-rlm/context/manifest.json and open each staged_path .extracted.txt file.\n"
            "- context_manifest: host path to size metadata for staged files.\n"
            "- source_metadata: extraction status, char counts, and staging hints.\n"
            "- Inspect with Python slices/regex; use llm_query on focused excerpts; do not print wholesale.\n"
            "- history: structured dspy.History for prior turns."
        )
        if implies_quote_retrieval(user_request):
            sections.append(quote_retrieval_repl_guidance())
    if recent_history:
        sections.append(recent_history)
    if compressed_history:
        sections.append("Compressed conversation context:\n" + compressed_history)
    if core_memory:
        sections.append("Core memory and active skill guidance:\n" + core_memory)
    sections.append("Repeat task:\n" + user_request)
    return "\n\n".join(section for section in sections if section.strip())


__all__ = [
    "RLM_REPL_GUIDANCE",
    "build_rlm_prompt_context",
    "url_repl_only_enabled",
]
