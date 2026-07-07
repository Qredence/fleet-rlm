"""Tests for large-context routing helpers."""

from __future__ import annotations

from pathlib import Path

import dspy

from fleet_rlm.runtime.agent.turn_context import TurnContext
from fleet_rlm.runtime.modules.context_routing import (
    build_turn_context,
    estimate_turn_context_chars,
    extract_inline_context_payload,
    load_large_context_rlm_kwargs,
    resolve_effective_context_paths,
    should_auto_route_large_context,
)
from fleet_rlm.runtime.modules.factory import VARIABLE_MODE_THRESHOLD


def test_estimate_turn_context_includes_message_and_history() -> None:
    history = dspy.History(messages=[{"user_message": "x" * 1000, "response": "y" * 500}])
    total, sources = estimate_turn_context_chars(user_request="hello", history=history)
    assert total >= 1505
    assert any(source.startswith("history:") for source in sources)


def test_should_auto_route_when_over_threshold(tmp_path: Path) -> None:
    doc = tmp_path / "large.txt"
    doc.write_text("a" * (VARIABLE_MODE_THRESHOLD + 100))
    turn_context = build_turn_context(user_request="analyze", docs_path=str(doc))
    assert turn_context.estimated_chars >= VARIABLE_MODE_THRESHOLD
    assert should_auto_route_large_context(execution_mode="auto", turn_context=turn_context)


def test_should_not_auto_route_small_context() -> None:
    turn_context = TurnContext(estimated_chars=100, threshold_chars=VARIABLE_MODE_THRESHOLD)
    assert not should_auto_route_large_context(execution_mode="auto", turn_context=turn_context)


def test_load_large_context_kwargs_populates_document_text_for_single_file(tmp_path: Path) -> None:
    doc = tmp_path / "report.md"
    body = "## Enterprise 2030\n\n" + ("Theme line.\n" * 200)
    doc.write_text(body, encoding="utf-8")
    turn_context = build_turn_context(user_request="summarize", context_paths=[str(doc)])
    kwargs = load_large_context_rlm_kwargs(turn_context)
    assert kwargs["document_text"] == body
    assert kwargs["source_metadata"]["char_count"] == str(len(body))
    assert str(doc) in kwargs["context_manifest"]


def test_resolve_effective_context_paths_prefers_message_paths(tmp_path: Path) -> None:
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"x" * 100)
    explicit = resolve_effective_context_paths(
        message_context_paths=[str(doc)],
        loaded_document_paths=["/other/path.pdf"],
        session_context_paths=["/session/path.pdf"],
    )
    assert explicit == [str(doc)]


def test_resolve_effective_context_paths_reuses_session_paths() -> None:
    merged = resolve_effective_context_paths(
        message_context_paths=None,
        loaded_document_paths=[],
        session_context_paths=["/session/report.pdf"],
    )
    assert merged == ["/session/report.pdf"]


def test_build_turn_context_pathless_followup_keeps_large_context(tmp_path: Path) -> None:
    doc = tmp_path / "enterprise.pdf"
    doc.write_text("a" * (VARIABLE_MODE_THRESHOLD + 500))
    turn_context = build_turn_context(
        user_request="What is the exact quote from Chad Gates?",
        context_paths=None,
        session_context_paths=[str(doc)],
    )
    assert turn_context.context_paths == [str(doc)]
    assert should_auto_route_large_context(execution_mode="auto", turn_context=turn_context)


def test_load_large_context_kwargs_includes_sandbox_staged_paths(tmp_path: Path) -> None:
    doc = tmp_path / "oolong.bin"
    doc.write_bytes(b"x" * 2000)
    turn_context = build_turn_context(user_request="count", context_paths=[str(doc)])
    interpreter = type(
        "Interpreter",
        (),
        {
            "context_sources": [
                type(
                    "Source",
                    (),
                    {"staged_path": ".fleet-rlm/context/01-oolong/extracted.txt"},
                )()
            ]
        },
    )()
    kwargs = load_large_context_rlm_kwargs(turn_context, interpreter=interpreter)
    assert kwargs["source_metadata"]["sandbox_staged_paths"] == [".fleet-rlm/context/01-oolong/extracted.txt"]
    assert "host filesystem paths" in kwargs["source_metadata"]["context_staging_hint"].lower()


def test_load_large_context_kwargs_staging_hint_without_extractable_single_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"
    turn_context = build_turn_context(user_request="analyze", context_paths=[str(missing)])
    kwargs = load_large_context_rlm_kwargs(turn_context)
    assert "document_text" not in kwargs
    assert "context_staging_hint" in kwargs["source_metadata"]


def test_extract_inline_context_payload_prefers_context_delimiter() -> None:
    request = "Count labels and report totals.\n\nCONTEXT:\n" + ("alpha\n" * VARIABLE_MODE_THRESHOLD)

    payload = extract_inline_context_payload(request, threshold_chars=VARIABLE_MODE_THRESHOLD)

    assert payload is not None
    assert payload.text.startswith("CONTEXT:")
    assert "Count labels" in payload.shortened_user_request
    assert 'context["document_text"]' in payload.shortened_user_request
    assert "alpha\nalpha\nalpha" not in payload.shortened_user_request
    assert payload.metadata["inline_context_staged"] == "true"
    assert payload.metadata["inline_context_extraction_kind"] == "context_delimiter"


def test_load_large_context_kwargs_stages_oversized_inline_request() -> None:
    request = "Classify entries.\n\nCONTEXT:\n" + ("row: accepted\n" * VARIABLE_MODE_THRESHOLD)
    turn_context = build_turn_context(user_request=request)

    kwargs = load_large_context_rlm_kwargs(turn_context)

    assert kwargs["document_text"].startswith("CONTEXT:")
    assert kwargs["shortened_user_request"] == turn_context.shortened_user_request
    assert kwargs["source_metadata"]["inline_context_staged"] == "true"
    assert kwargs["source_metadata"]["original_user_request_chars"] == str(len(request))
    assert kwargs["context_manifest"]["inline_context_payload"] == str(len(turn_context.inline_context_text))
