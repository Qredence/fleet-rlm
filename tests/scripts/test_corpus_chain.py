from __future__ import annotations

import json

from scripts.benchmarks.corpus_chain import (
    CORPUS_SIZE,
    corpus_line,
    make_corpus_case,
    validate_corpus_evidence,
    validate_corpus_report,
)


def test_trace_seed_reproduces_the_observed_chain_without_materializing_the_corpus() -> None:
    case = make_corpus_case(0)

    assert case.size == CORPUS_SIZE
    assert case.path == (40_000, 187_653, 287_653, 350_500, 499_999)
    assert case.lookup_index == 350_500
    assert case.expected_report["payload_count"] == 5
    assert "payload=65535" in corpus_line(case, case.terminal_index)


def test_validator_accepts_only_the_data_derived_structured_report() -> None:
    case = make_corpus_case(0)

    result = validate_corpus_report(json.dumps(case.expected_report), case)

    assert result.passed is True
    assert result.errors == ()


def test_validator_rejects_first_number_parsing_and_terminal_decoy() -> None:
    case = make_corpus_case(0)
    report = case.expected_report
    report["path"] = [40_000, 65_535, 287_653, 350_500, 499_999]
    report["computed_answer"] = "S555"
    report["terminal_discrepancy"] = False

    result = validate_corpus_report(json.dumps(report), case)

    assert result.passed is False
    assert "path does not match the fixture" in result.errors
    assert "computed_answer does not match the fixture" in result.errors
    assert "terminal_discrepancy does not match the fixture" in result.errors


def test_alternate_seed_rejects_the_original_hardcoded_path() -> None:
    case = make_corpus_case(1, size=512)
    report = dict(case.expected_report)
    report["path"] = [40_000, 187_653, 287_653, 350_500, 499_999]

    result = validate_corpus_report(json.dumps(report), case)

    assert result.passed is False
    assert "path does not match the fixture" in result.errors


def test_evidence_rejects_hardcoded_or_unobserved_submissions() -> None:
    hardcoded = {
        "codes": ["SUBMIT(answer=json.dumps(report))"],
        "outputs": ["FINAL submitted"],
    }
    no_access = {
        "codes": ["source = read_attachment(attachment_id=attachments[0]['id'])"],
        "outputs": ["FINAL submitted"],
    }

    assert validate_corpus_evidence(hardcoded, attachment_accessed=False).passed is False
    assert validate_corpus_evidence(no_access, attachment_accessed=False).passed is False


def test_evidence_accepts_bounded_attachment_backed_trajectory() -> None:
    evidence = validate_corpus_evidence(
        {
            "codes": [
                "source = read_attachment(attachment_id=attachments[0]['id'])",
                "report = {}\nSUBMIT(answer=json.dumps(report))",
            ],
            "outputs": ["FINAL submitted"],
        },
        attachment_accessed=True,
    )

    assert evidence.passed is True
    assert evidence.errors == ()
