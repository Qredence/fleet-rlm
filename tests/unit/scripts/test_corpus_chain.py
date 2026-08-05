from __future__ import annotations

import json

from scripts.benchmarks.corpus_chain import (
    CORPUS_SIZE,
    corpus_line,
    corpus_workload,
    iter_corpus_lines,
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


def test_fixture_generator_is_streamable_and_places_exact_lookup_data() -> None:
    case = make_corpus_case(1, size=512)
    lines = iter_corpus_lines(case)

    first_lines = [next(lines) for _ in range(3)]

    assert len(first_lines) == 3
    assert f"[E{case.path[0]}]" in list(iter_corpus_lines(case))[case.path[0]]
    assert "field=N333/SYNC/S777" in corpus_line(case, case.lookup_index)
    assert len(case.write_indices) == 132
    assert sum("payload=65535" in line for line in iter_corpus_lines(case)) == 5


def test_validator_accepts_only_the_data_derived_structured_report() -> None:
    case = make_corpus_case(0)

    result = validate_corpus_report(json.dumps(case.expected_report), case)

    assert result.passed is True
    assert result.errors == ()


def test_workload_requires_hidden_attachment_reads_and_json_string_submission() -> None:
    case = make_corpus_case(0)
    prompt = corpus_workload(case)

    assert "read_attachment(attachment_id=...)" in prompt
    assert "SUBMIT(answer=json.dumps(report))" in prompt
    assert str(case.path[0]) not in prompt
    assert str(case.path[1]) not in prompt
    assert str(case.path[2]) not in prompt
    assert str(case.path[3]) not in prompt
    assert str(case.terminal_index) not in prompt
    assert "S444" not in prompt
    assert "S555" not in prompt
    assert "65535" not in prompt


def test_validator_rejects_python_repr_from_string_typed_submit() -> None:
    case = make_corpus_case(0)

    result = validate_corpus_report(str(case.expected_report), case)

    assert result.passed is False
    assert result.errors == ("answer is not one JSON object",)


def test_validator_rejects_bool_and_float_values_for_integer_fields() -> None:
    case = make_corpus_case(0)

    bool_report = dict(case.expected_report)
    bool_report["payload_count"] = True
    float_report = dict(case.expected_report)
    float_report["payload_count"] = float(case.expected_report["payload_count"])

    assert validate_corpus_report(json.dumps(bool_report), case).passed is False
    assert validate_corpus_report(json.dumps(float_report), case).passed is False


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


def test_validator_rejects_unexpected_fields() -> None:
    case = make_corpus_case(0, size=512)
    report = dict(case.expected_report)
    report["debug_path"] = list(case.path)

    result = validate_corpus_report(json.dumps(report), case)

    assert result.passed is False
    assert "answer contains unexpected fields: debug_path" in result.errors


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
