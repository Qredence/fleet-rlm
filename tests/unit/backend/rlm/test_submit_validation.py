"""Wrap-up grammar is deliberately narrower than general interpreter code."""

import pytest

from fleet_rlm.rlm.submit_validation import is_submit_only_code


@pytest.mark.parametrize(
    "code",
    [
        'SUBMIT(answer="done")',
        "```python\nSUBMIT(answer=str(evidence[0]))\n```",
        'SUBMIT(answer=json.dumps({"items": items}), count=len(items))',
        'SUBMIT(answer=f"Found {count}")',
    ],
)
def test_accepts_finalization_expressions(code: str) -> None:
    assert is_submit_only_code(code)


@pytest.mark.parametrize(
    "code",
    [
        None,
        "",
        "SUBMIT(",
        'print("explore"); SUBMIT(answer="done")',
        'SUBMIT(answer=llm_query("more work"))',
        "SUBMIT(answer=[lookup(item) for item in items])",
        "SUBMIT(**outputs)",
        'SUBMIT("positional")',
        "SUBMIT(answer=obj.__dict__)",
        '```javascript\nSUBMIT(answer="done")\n```',
    ],
)
def test_rejects_non_finalization_syntax(code: object) -> None:
    assert not is_submit_only_code(code)
