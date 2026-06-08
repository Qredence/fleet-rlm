"""Shared task-intent heuristics for routing, skill selection, and deliverable shaping."""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s)\],;]+", flags=re.IGNORECASE)

_ANALYSIS_TERMS = (
    "analyze",
    "analyse",
    "analysis",
    "summarize",
    "summarise",
    "summary",
    "read",
    "documentation",
    "docs",
    "document",
    "page",
    "inspect",
    "extract",
)

_DETAIL_TASK_TERMS = (
    "in depth",
    "in-depth",
    "detailed",
    "comprehensive",
    "thorough",
    "report",
    "analysis",
    "analyze",
    "analyse",
    "summarize",
    "summarise",
    "document",
    "explain",
    "architecture",
    "breakdown",
    "overview",
    "walkthrough",
    "guide",
    "list the",
    "list all",
    "enumerate",
    "parameters",
    "defaults",
    "compare",
    "describe",
)

_CODE_TASK_TERMS = (
    "implement",
    "write code",
    "create script",
    "refactor",
    "fix bug",
    "generate code",
    "code file",
    "python script",
    "typescript",
    "full file",
    "complete file",
)

_QUOTE_RETRIEVAL_TRIGGERS = (
    "exact quote",
    "quote from",
    "quote verbatim",
    "return the quote",
    "include the quote",
    "verbatim",
    "what did ",
    "what does ",
    " said ",
)

_QUOTE_RETRIEVAL_REPL_GUIDANCE = """Exact quote retrieval (mandatory):
- Return exactly ONE quote block in SUBMIT — never a numbered list or multiple quotes.
- Locate the speaker in document_text (document_text.find(...) or re.search(...)).
- Extract the quoted span in typographic quotes (“…” or "...") immediately before the speaker name/title attribution line.
- SUBMIT only characters copied verbatim from document_text via a Python slice; no paraphrase, no summarization, no wording changes.
- Do not invent or substitute words from nearby headings (e.g. section titles).
- Print the extracted slice in the REPL and confirm it matches SUBMIT character-for-character.
- If no quoted span exists for that speaker, say you could not find a verbatim quote.
- Do not open host context_paths or absolute filesystem paths in the sandbox.
"""


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def extract_first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,;]") if match else None


def has_url(text: str) -> bool:
    return _URL_RE.search(text) is not None


def has_analysis_intent(text: str) -> bool:
    lowered = _normalize(text)
    return any(term in lowered for term in _ANALYSIS_TERMS)


def has_url_document_intent(text: str) -> bool:
    url = extract_first_url(text)
    if url is None:
        return False
    remainder = text.replace(url, "", 1).strip()
    if not remainder:
        return False
    return has_analysis_intent(remainder)


def implies_detailed_deliverable(task: str | None) -> bool:
    normalized = _normalize(task)
    return any(term in normalized for term in _DETAIL_TASK_TERMS)


def implies_code_deliverable(task: str | None) -> bool:
    normalized = _normalize(task)
    return any(term in normalized for term in _CODE_TASK_TERMS)


def implies_quote_retrieval(task: str | None) -> bool:
    normalized = _normalize(task)
    return any(trigger in normalized for trigger in _QUOTE_RETRIEVAL_TRIGGERS)


def quote_retrieval_repl_guidance() -> str:
    """Static REPL instructions for exact-quote / speaker attribution tasks."""
    return _QUOTE_RETRIEVAL_REPL_GUIDANCE


__all__ = [
    "extract_first_url",
    "has_analysis_intent",
    "has_url",
    "has_url_document_intent",
    "implies_code_deliverable",
    "implies_detailed_deliverable",
    "implies_quote_retrieval",
    "quote_retrieval_repl_guidance",
]
