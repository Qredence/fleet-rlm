from collections import Counter

from fleet_rlm.optimize import benchmarks


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
