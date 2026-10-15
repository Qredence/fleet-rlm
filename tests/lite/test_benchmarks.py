from collections import Counter

import httpx

from fleet_rlm import benchmarks


def _events(case):
    rows = []
    for line in case.files["files/events.log"].splitlines():
        fields = dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)
        if "action" in fields:
            rows.append(fields)
    return rows


def test_long_context_sizes_follow_tiers_and_gold_matches_recomputation():
    cases = benchmarks.long_context_cases(seed=5, tiers=(20_000, 50_000), per_tier=4)
    assert len(cases) == 8
    for case in cases:
        tier = int(case.case_id.split("-")[1].rstrip("k")) * 1000
        assert tier <= case.context_chars < tier + 2_000
        rows = _events(case)
        if case.category.endswith("count"):
            assert case.expected_values == (str(sum(r["action"] == "refund" for r in rows)),)
        if case.category.endswith("list"):
            fails = Counter(r["user"] for r in rows if r["action"] == "failed_login")
            assert case.expected_values, "list cases must have a non-empty gold list"
            assert set(case.expected_values) == {u for u, n in fails.items() if n >= 2}
            assert case.answer_type == "list"
        if case.category.endswith("needle"):
            assert f"ALERT code={case.expected_values[0]} " in case.files["files/events.log"]


def test_long_context_is_deterministic():
    assert [c.record() for c in benchmarks.long_context_cases(seed=2, per_tier=1)] == [
        c.record() for c in benchmarks.long_context_cases(seed=2, per_tier=1)
    ]


def _client(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)))


def test_oolong_adapter_parses_labels_and_skips_truncated_rows():
    row = {
        "id": 7,
        "question": "Which label is most common?",
        "task_group": "counting",
        "answer": "['spam']",
        "context_window_text": "text",
        "context_len": 1024,
        "context_window_id": 3,
    }
    payload = {
        "rows": [
            {"row": row, "truncated_cells": []},
            {"row": {**row, "id": 8}, "truncated_cells": ["context_window_text"]},
        ]
    }
    cases = benchmarks.oolong_cases(_client(payload), limit=5, max_pages=3, pause=0)
    assert [c.case_id for c in cases] == ["oolong-7"]
    case = cases[0]
    assert case.expected_values == ("spam",) and case.review_status == "benchmark"
    assert case.files == {"files/context.txt": "text"} and case.source_group == "oolong-window-3"
    assert case.record()["expectations"]["benchmark"] == "oolong-synth"


def test_longcot_adapter_keeps_only_scalar_and_flat_list_answers():
    def row(qid, answer):
        return {"row": {"question_id": qid, "prompt": "p", "answer": answer, "template": "T"}}

    payload = {"rows": [row("a", '{"q1": 1}'), row("b", "null"), row("c", '["c4", "bxc4"]'), row("d", '"8/r7 w"')]}
    cases = benchmarks.longcot_cases(_client(payload), per_domain=5, domains=("chess",))
    assert [c.case_id for c in cases] == ["longcot-c", "longcot-d"]
    assert cases[0].answer_type == "list" and cases[0].expected_values == ("c4", "bxc4")
    assert cases[1].answer_type == "scalar" and cases[1].benchmark == "longcot"
