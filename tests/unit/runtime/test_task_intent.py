"""Tests for shared task-intent heuristics."""

from __future__ import annotations

from fleet_rlm.runtime.task_intent import (
    extract_first_url,
    has_analysis_intent,
    has_url_document_intent,
    implies_code_deliverable,
    implies_detailed_deliverable,
    implies_quote_retrieval,
    quote_retrieval_repl_guidance,
)


def test_has_url_document_intent_requires_url_and_analysis() -> None:
    assert has_url_document_intent("Summarize https://example.com/docs")
    assert not has_url_document_intent("Summarize the quarterly report")
    assert not has_url_document_intent("https://example.com/docs")


def test_has_analysis_intent_includes_inspect_and_page() -> None:
    assert has_analysis_intent("Inspect the PDF for revenue figures")
    assert has_analysis_intent("Read page 3 of the appendix")


def test_implies_detailed_and_code_deliverables() -> None:
    assert implies_detailed_deliverable("Give me a comprehensive architecture breakdown")
    assert implies_code_deliverable("Implement a python script to parse logs")
    assert not implies_code_deliverable("What is the capital of France?")


def test_implies_quote_retrieval() -> None:
    assert implies_quote_retrieval("What is the exact quote from Chad Gates?")
    assert not implies_quote_retrieval("Summarize the document")


def test_quote_retrieval_repl_guidance_is_non_empty() -> None:
    guidance = quote_retrieval_repl_guidance()
    assert "SUBMIT" in guidance
    assert "document_text" in guidance


def test_extract_first_url_strips_trailing_punctuation() -> None:
    assert extract_first_url("See https://example.com/doc.") == "https://example.com/doc"
