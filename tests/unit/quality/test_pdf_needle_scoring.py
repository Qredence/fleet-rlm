"""Unit tests for PDF needle retrieval scorers."""

from __future__ import annotations

from fleet_rlm.quality.pdf_needle_scoring import (
    aggregate_needle_results,
    normalize_text,
    quote_fidelity_score,
    routing_score,
    score_needle_result,
    token_overlap_ratio,
    trajectory_grounding_score,
)

CHAD_QUOTE = (
    "By 2030, insight will be everywhere. Interfaces will be radically different, "
    "and AI will act as the business intelligence system, decision engine, "
    "and a participant in operations."
)


def test_normalize_text_collapses_whitespace_and_smart_quotes():
    assert normalize_text("  Hello\u2019s  World  ") == "hello's world"


def test_routing_score_large_context_pass():
    assert routing_score(route="large_context_rlm") == 1.0
    assert routing_score(route="auto", execution_mode="rlm_only") == 1.0


def test_routing_score_auto_chain_of_thought_fail():
    assert routing_score(route="auto") == 0.0
    assert routing_score(route="") == 0.0


def test_quote_fidelity_exact_pass():
    answer = f'Chad Gates said: "{CHAD_QUOTE}"'
    score, verdict, _ = quote_fidelity_score(
        answer,
        expected_exact_quote=CHAD_QUOTE,
        expected_substrings=["insight will be everywhere"],
    )
    assert score == 1.0
    assert verdict == "PASS"


def test_quote_fidelity_partial_substrings():
    score, verdict, details = quote_fidelity_score(
        "insight will be everywhere but wrong attribution",
        expected_exact_quote=CHAD_QUOTE,
        expected_substrings=["insight will be everywhere", "business intelligence system"],
    )
    assert score == 0.5
    assert verdict == "PARTIAL"
    assert details["substring_coverage"] == 0.5


def test_negative_control_abstention_pass():
    score, verdict, _ = quote_fidelity_score(
        "I could not find any mention of Antarctica in the document.",
        negative_control=True,
    )
    assert score == 1.0
    assert verdict == "ABSTAIN_PASS"


def test_negative_control_abstention_fail():
    score, verdict, _ = quote_fidelity_score(
        "Chad Gates discusses Antarctica's quantum policy in detail.",
        negative_control=True,
        forbidden_substrings=["antarctica"],
    )
    assert score == 0.0
    assert verdict == "ABSTAIN_FAIL"


def test_trajectory_grounding_document_text_pass():
    traj = 'idx = document_text.find("Chad Gates")\nprint(document_text[idx:idx+200])'
    score, verdict, details = trajectory_grounding_score(traj)
    assert score == 1.0
    assert verdict == "PASS"
    assert "document_text" in details["grounded_hits"]


def test_trajectory_grounding_host_path_fail():
    traj = 'import fitz\ndoc = fitz.open("/Volumes/SSD-T7/report.pdf")'
    score, verdict, details = trajectory_grounding_score(traj)
    assert score == 0.0
    assert verdict == "FAIL"
    assert details["host_path_access"] is True


def test_token_overlap_ratio():
    ratio = token_overlap_ratio(CHAD_QUOTE, CHAD_QUOTE.replace("Interfaces", "interfaces"))
    assert ratio >= 0.95


def test_score_needle_result_aggregate():
    item = {
        "id": "chad_gates_quote",
        "negative_control": False,
        "expected_exact_quote": CHAD_QUOTE,
        "expected_substrings": ["insight will be everywhere"],
        "forbidden_substrings": [],
    }
    scores = score_needle_result(
        item,
        answer=CHAD_QUOTE,
        route="large_context_rlm",
        trajectory='document_text.find("Chad Gates")',
    )
    assert scores.routing == 1.0
    assert scores.quote == 1.0
    assert scores.trajectory == 1.0


def test_aggregate_needle_results():
    results = [
        {
            "item": {"negative_control": False},
            "condition": "path_every_turn",
            "verdict": "PASS",
            "scores": {
                "routing": 1.0,
                "quote": 1.0,
                "quote_verdict": "PASS",
                "trajectory": 1.0,
                "abstention": 0.0,
            },
        },
        {
            "item": {"negative_control": True},
            "condition": "pathless_followup",
            "verdict": "FAIL",
            "scores": {
                "routing": 0.0,
                "quote": 0.0,
                "abstention": 1.0,
                "trajectory": 0.0,
            },
        },
    ]
    agg = aggregate_needle_results(results)
    assert agg["total_items"] == 2
    assert agg["routing_accuracy"] == 0.5
    assert agg["abstention_accuracy"] == 1.0
