"""Grounded-answer synthesis module with chunking and evidence selection."""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.runtime.agent.signatures import GroundedAnswerWithCitations
from fleet_rlm.runtime.content.chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from fleet_rlm.runtime.modules.factory import (
    _create_configured_runtime_rlm,
    build_runtime_module_config,
)


def _normalize_chunk_strategy(strategy: str) -> str:
    """Normalize a user-provided chunk strategy name."""
    normalized = str(strategy).strip().lower().replace("-", "_")
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


def _chunk_document(text: str, strategy: str) -> list[Any]:
    """Split a document into chunks using the specified strategy."""
    strategy_norm = _normalize_chunk_strategy(strategy)
    if strategy_norm == "size":
        return chunk_by_size(text, size=80_000, overlap=1_000)
    if strategy_norm == "headers":
        return chunk_by_headers(text, pattern=r"^#{1,3} ")
    if strategy_norm == "timestamps":
        return chunk_by_timestamps(text, pattern=r"^\d{4}-\d{2}-\d{2}[T ]")
    return chunk_by_json_keys(text)


def _chunk_to_text(chunk: Any) -> str:
    """Convert a chunk (dict or str) into plain text."""
    if isinstance(chunk, str):
        return chunk
    if not isinstance(chunk, dict):
        return str(chunk)
    if "header" in chunk:
        return f"{chunk.get('header', '')}\n{chunk.get('content', '')}".strip()
    if "timestamp" in chunk:
        return str(chunk.get("content", ""))
    if "key" in chunk:
        return f"{chunk.get('key', '')}\n{chunk.get('content', '')}".strip()
    return str(chunk)


def _coerce_bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse an integer, clamping to [minimum, maximum]."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


class GroundedAnswerSynthesisModule(dspy.Module):
    """Compose chunking + evidence selection before the grounded-answer RLM."""

    def __init__(
        self,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> None:
        super().__init__()
        self._grounded_answer = _create_configured_runtime_rlm(
            build_runtime_module_config(
                interpreter=interpreter,
                max_iterations=max_iterations,
                max_llm_calls=max_llm_calls,
                verbose=verbose,
            ),
            signature=GroundedAnswerWithCitations,
        )

    def forward(
        self,
        *,
        document: str,
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        response_style: str = "concise",
    ) -> dspy.Prediction:
        """Chunk the document, select evidence, and synthesize a grounded answer."""
        max_chunks_int = _coerce_bounded_int(max_chunks, default=24, minimum=1)
        try:
            chunks = _chunk_document(document, chunk_strategy)
        except ValueError as exc:
            return dspy.Prediction(
                answer="",
                citations=[],
                confidence=0,
                coverage_notes=str(exc),
            )

        evidence_chunks = [_chunk_to_text(chunk) for chunk in chunks][:max_chunks_int]
        if not evidence_chunks:
            return dspy.Prediction(
                answer="",
                citations=[],
                confidence=0,
                coverage_notes="No evidence chunks available.",
            )

        return self._grounded_answer(
            query=query,
            evidence_chunks=evidence_chunks,
            response_style=response_style,
        )


__all__ = [
    "GroundedAnswerSynthesisModule",
    "_chunk_document",
    "_chunk_to_text",
    "_coerce_bounded_int",
    "_normalize_chunk_strategy",
]
