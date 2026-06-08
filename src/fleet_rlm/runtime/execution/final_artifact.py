"""Structured final artifacts for chat and workbench completion payloads."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fleet_rlm.runtime.task_intent import implies_code_deliverable, implies_detailed_deliverable

_FENCE_RE = re.compile(r"```([^\n`]*)\n([\s\S]*?)```", re.MULTILINE)
_FILENAME_RE = re.compile(
    r"(?:^|[\s`'\"])([\w./-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|rb|sql|sh|zsh|yaml|yml|json|md|toml))(?:$|[\s`'\",:])",
    re.IGNORECASE,
)

_RLM_ROUTES = frozenset({"forced_rlm", "large_context_rlm", "url_document_rlm"})

_LANG_EXTENSIONS: dict[str, str] = {
    "python": "py",
    "py": "py",
    "typescript": "ts",
    "ts": "ts",
    "javascript": "js",
    "js": "js",
    "tsx": "tsx",
    "jsx": "jsx",
    "rust": "rs",
    "go": "go",
    "java": "java",
    "ruby": "rb",
    "sql": "sql",
    "bash": "sh",
    "shell": "sh",
    "sh": "sh",
    "zsh": "sh",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "markdown": "md",
    "md": "md",
}


def _markdown_min_chars() -> int:
    raw = os.environ.get("FLEET_RLM_MARKDOWN_MIN_CHARS", "1200")
    try:
        return max(400, int(raw))
    except (TypeError, ValueError):
        return 1200


def _looks_like_markdown(text: str) -> bool:
    return bool(
        re.search(r"^#{1,6}\s", text, re.MULTILINE)
        or re.search(r"^[-*+]\s", text, re.MULTILINE)
        or re.search(r"^\d+\.\s", text, re.MULTILINE)
        or "```" in text
        or re.search(r"\[[^\]]+\]\([^)]+\)", text)
    )


def _looks_structured_prose(text: str) -> bool:
    numbered = len(re.findall(r"(?m)^\s*\d+\.\s", text))
    bullets = len(re.findall(r"(?m)^\s*[-*+]\s", text))
    paragraphs = len([block for block in re.split(r"\n\s*\n", text.strip()) if block.strip()])
    return numbered >= 2 or bullets >= 3 or paragraphs >= 3


def _collapse_summary(text: str, *, max_len: int = 320) -> str:
    collapsed = re.sub(r"\s+", " ", text.strip())
    if len(collapsed) <= max_len:
        return collapsed
    return f"{collapsed[: max_len - 1].rstrip()}…"


def _guess_filename(*, language: str, task: str | None) -> str:
    if task:
        match = _FILENAME_RE.search(task)
        if match:
            return match.group(1).split("/")[-1]
    extension = _LANG_EXTENSIONS.get(language.lower(), "txt")
    return f"solution.{extension}"


def _extract_dominant_code_fence(text: str) -> tuple[str, str] | None:
    blocks: list[tuple[str, str]] = []
    for match in _FENCE_RE.finditer(text):
        language = (match.group(1) or "").strip().lower() or "text"
        content = match.group(2).strip()
        if content:
            blocks.append((language, content))

    if not blocks:
        return None

    if len(blocks) == 1:
        language, content = blocks[0]
        return language, content

    language, content = max(blocks, key=lambda item: len(item[1]))
    non_ws = re.sub(r"\s+", "", text)
    fence_chars = sum(len(content) for _, content in blocks)
    if fence_chars >= max(120, int(len(non_ws) * 0.45)):
        return language, content
    return None


def infer_expected_output_format(task: str | None) -> str:
    """Predict the preferred deliverable shape from the user task."""
    if implies_code_deliverable(task):
        return "code_file"
    if implies_detailed_deliverable(task):
        return "markdown"
    return "assistant_response"


def output_format_guidance_for_task(task: str | None) -> str:
    """Short REPL-facing instructions for how to shape SUBMIT(...) output."""
    fmt = infer_expected_output_format(task)
    if fmt == "code_file":
        return (
            "Output format:\n"
            "- Put the deliverable in one fenced code block (```language ... ```).\n"
            "- Keep prose outside the fence to a short summary (one or two sentences)."
        )
    if fmt == "markdown":
        return (
            "Output format:\n"
            "- SUBMIT structured Markdown for detailed answers (## headings, bullets, tables).\n"
            "- Use numbered lists for parameter/default enumerations; avoid one long paragraph."
        )
    return ""


def _normalize_answer_text(
    text: str,
    *,
    task: str | None = None,
    artifact_kind: str,
) -> str:
    """Ensure detailed answers have lightweight Markdown structure when needed."""
    if artifact_kind != "markdown" or _looks_like_markdown(text):
        return text
    if not implies_detailed_deliverable(task) and not _looks_structured_prose(text):
        return text
    title = "Answer"
    if task:
        collapsed = re.sub(r"\s+", " ", task.strip())
        if collapsed:
            title = collapsed[:80].rstrip(" .,;:")
    return f"## {title}\n\n{text}"


@dataclass(frozen=True, slots=True)
class _FormatContext:
    text: str
    task: str | None
    min_chars: int
    fence: tuple[str, str] | None
    detailed: bool
    rlm_route: bool
    structured: bool


def _build_format_context(
    answer: str,
    *,
    task: str | None = None,
    routing_decision: str | None = None,
) -> _FormatContext | None:
    text = (answer or "").strip()
    if not text:
        return None
    return _FormatContext(
        text=text,
        task=task,
        min_chars=_markdown_min_chars(),
        fence=_extract_dominant_code_fence(text),
        detailed=implies_detailed_deliverable(task),
        rlm_route=(routing_decision or "").strip() in _RLM_ROUTES,
        structured=_looks_structured_prose(text),
    )


def _is_code_file(ctx: _FormatContext) -> bool:
    return ctx.fence is not None and (implies_code_deliverable(ctx.task) or len(ctx.fence[1]) >= 240)


_FORMAT_RULES: tuple[tuple[Callable[[_FormatContext], bool], str], ...] = (
    (_is_code_file, "code_file"),
    (lambda ctx: len(ctx.text) >= ctx.min_chars or (ctx.detailed and len(ctx.text) >= 500), "markdown"),
    (lambda ctx: ctx.rlm_route and len(ctx.text) >= 700, "markdown"),
    (lambda ctx: _looks_like_markdown(ctx.text) and len(ctx.text) >= 500, "markdown"),
    (lambda ctx: ctx.detailed and _looks_like_markdown(ctx.text), "markdown"),
    (lambda ctx: ctx.detailed and ctx.structured and len(ctx.text) >= 250, "markdown"),
    (lambda ctx: ctx.rlm_route and ctx.structured and len(ctx.text) >= 200, "markdown"),
    (lambda ctx: ctx.detailed and len(ctx.text) >= 400, "markdown"),
)


def classify_answer_format(
    answer: str,
    *,
    task: str | None = None,
    routing_decision: str | None = None,
) -> str:
    """Return ``code_file``, ``markdown``, or ``assistant_response``."""
    ctx = _build_format_context(answer, task=task, routing_decision=routing_decision)
    if ctx is None:
        return "assistant_response"

    for predicate, kind in _FORMAT_RULES:
        if predicate(ctx):
            return kind
    return "assistant_response"


def build_final_artifact_from_answer(
    answer: str,
    *,
    task: str | None = None,
    routing_decision: str | None = None,
    finalization_mode: str = "SUBMIT",
) -> dict[str, Any] | None:
    """Build a workbench/chat ``final_artifact`` envelope from the assistant answer."""
    text = (answer or "").strip()
    if not text:
        return None

    artifact_kind = classify_answer_format(
        text,
        task=task,
        routing_decision=routing_decision,
    )
    normalized_text = _normalize_answer_text(text, task=task, artifact_kind=artifact_kind)
    summary = _collapse_summary(normalized_text)

    if artifact_kind == "code_file":
        fence = _extract_dominant_code_fence(text)
        language = fence[0] if fence else "text"
        content = fence[1] if fence else text
        filename = _guess_filename(language=language, task=task)
        return {
            "kind": "code_file",
            "value": {
                "filename": filename,
                "language": language,
                "content": content,
                "summary": summary,
                "text": summary,
            },
            "finalization_mode": finalization_mode,
        }

    if artifact_kind == "markdown":
        return {
            "kind": "markdown",
            "value": {
                "final_markdown": normalized_text,
                "summary": summary,
                "text": summary,
            },
            "finalization_mode": finalization_mode,
        }

    return {
        "kind": "assistant_response",
        "value": {
            "text": normalized_text,
            "final_markdown": normalized_text,
            "summary": summary,
        },
        "finalization_mode": finalization_mode,
    }


__all__ = [
    "build_final_artifact_from_answer",
    "classify_answer_format",
    "infer_expected_output_format",
    "output_format_guidance_for_task",
]
