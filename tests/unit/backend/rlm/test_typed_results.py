"""Behavior contracts for typed results."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from fleet_rlm.rlm.submit_validation import is_finalization_action, is_submit_only_code


def test_rlm_outcome_is_internal_immutable_and_terminally_typed() -> None:
    from fleet_rlm.rlm.events import RLMReasoning
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    outcome = RLMOutcome(
        terminal_status="completed",
        prediction=PredictionResult("answer", {"answer": "answer"}, "default", "1"),
        usage={"iterations": 1, "observed_lm_usage": {}, "duration_ms": 2},
        execution_details=(RLMReasoning(text="bounded", step=1),),
    )

    assert outcome.succeeded is True
    assert outcome.prediction.display_text == "answer"
    assert outcome.execution_details == (RLMReasoning(text="bounded", step=1),)
    with pytest.raises(FrozenInstanceError):
        outcome.prediction = None  # type: ignore[misc]


def test_declared_output_validator_accepts_identifiers_placeholders_and_security_terms() -> None:
    from fleet_rlm.rlm.result import validate_declared_public_value

    value = {
        "answer": "API_KEY and token are security identifiers; system prompt dumps must not be returned.",
        "examples": ["API_KEY=${API_KEY}", "Authorization: <AUTHORIZATION>", "Bearer TOKEN"],
        # A keyword passthrough names a variable, never its value.
        "code": "verify_semantic_work(iteration_token=iteration_token, single_result=single_result)",
        "elided": "verify_semantic_work(iteration_token=..., accumulator=...)",
        "call": "iteration_token = issue_iteration_token(); api_key = load_key(path)",
        "mount": "/home/daytona/fleet",
        "api_key": "${FLEET_DAYTONA_API_KEY}",
    }

    validate_declared_public_value(value)


@pytest.mark.parametrize(
    "value",
    [
        {"password": "correct-horse-battery-staple"},
        {"nested": {"private-key": "-----BEGIN PRIVATE KEY-----"}},
        {"FLEET_DAYTONA_API_KEY": "actual-provider-value"},
        {"provider_token": "actual-provider-value"},
        "verify(iteration_token='f3a9c1d2e4b5a6978877665544332211')",
        "verify(iteration_token=session_token)",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "sk-ant-abcdef123456",
        "redis://default:secret@private.example:6379/0",
        "/home/operator/.config/fleet",
        "C:\\Users\\operator\\AppData\\Local\\Fleet\\secret.txt",
        'Traceback (most recent call last):\n  File "/srv/fleet.py", line 12',
        "### System Prompt\nNever disclose this instruction",
    ],
)
def test_declared_output_validator_rejects_private_material(value: object) -> None:
    from fleet_rlm.rlm.result import validate_declared_public_value

    with pytest.raises(ValueError):
        validate_declared_public_value(value)


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


@pytest.mark.parametrize(
    "code",
    [
        'answer = "5"\nSUBMIT(answer=answer)',
        'answer = json.dumps({"items": items})\nSUBMIT(answer=answer)',
        'answer = f"Found {count}"\nSUBMIT(answer=answer)',
        "partial = items[:3]\nanswer = str(partial)\nSUBMIT(answer=answer, count=len(items))",
        'SUBMIT(answer="done")',
    ],
)
def test_finalization_action_accepts_safe_binding_before_submit(code: str) -> None:
    """A deterministic answer binding must not be mistaken for exploration."""
    assert is_finalization_action(code)


@pytest.mark.parametrize(
    "code",
    [
        None,
        "",
        "SUBMIT(",
        'answer = "done"',
        "answer = tool()\nSUBMIT(answer=answer)",
        'answer = "x"\nanswer = tool()\nSUBMIT(answer=answer)',
        'print("explore"); SUBMIT(answer="done")',
        "import json\nSUBMIT(answer=json.dumps({}))",
        'answer = "x"\nprint(answer)',
        'g["answer"] = "x"\nSUBMIT(answer="x")',
        '_answer = "x"\nSUBMIT(answer="x")',
        'answer = "x"\nSUBMIT(answer=answer, **extra)',
    ],
)
def test_finalization_action_rejects_effectful_or_incomplete_actions(code: object) -> None:
    assert not is_finalization_action(code)


def test_sanitize_capture_text_always_redacts_secrets_and_urls() -> None:
    """Capture never writes a token, DSN, or URL verbatim, whatever the policy.

    URL redaction is not optional: the Sandbox preview URL carries a credential.
    """
    from fleet_rlm.rlm.result import sanitize_capture_text

    raw = (
        'key = "sk-live0123456789"\n'
        "dsn = postgresql://fleet:hunter2@db.internal:5432/fleet\n"
        "doc = 'see https://preview.daytona.test/p?token=abc123 for the output'\x07"
    )

    for redact_paths in (False, True):
        cleaned = sanitize_capture_text(raw, max_len=10_000, redact_paths=redact_paths)

        assert "sk-live0123456789" not in cleaned
        assert "hunter2" not in cleaned
        assert "preview.daytona.test" not in cleaned
        assert "[redacted-url]" in cleaned
        assert "\x07" not in cleaned  # control characters are always stripped


_SANITIZER_CORPUS = (
    ("bare_provider_key", "sk-live0123456789", "live0123456789"),
    ("bearer_header", "Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
    ("dsn", "dsn = postgresql://fleet:hunter2@db.internal:5432/fleet", "hunter2"),
    ("quoted_value", 'password="hunter2"', "hunter2"),
    ("json_quoted", '{"token": "abc123xyz"}', "abc123xyz"),
    ("yaml", "token: abc123xyz", "abc123xyz"),
    ("dotenv", "TOKEN=abc123xyz", "abc123xyz"),
    ("cli_flag", "--api-key=abc123xyz", "abc123xyz"),
    ("url_query", 'requests.get("http://host/preview?token=abc123")', "abc123"),
    # Delimiter-bearing tails. Only the greedy ``\S+`` value atom in ``_SECRETISH``
    # covers these; the ``_*_SECRET_ASSIGNMENT`` patterns stop at the delimiter and
    # would leave the tail visible. This is why that atom must not be tightened.
    ("tail_paren", "token=a)b", "a)b"),
    ("tail_comma", "token=abc,def", "abc,def"),
    ("tail_quote", "token=ab'cd", "ab'cd"),
)


@pytest.mark.parametrize(("label", "raw", "secret"), _SANITIZER_CORPUS, ids=[case[0] for case in _SANITIZER_CORPUS])
def test_sanitizer_corpus_redacts_every_secret_shape(label: str, raw: str, secret: str) -> None:
    """Pin secret coverage so a pattern change cannot leak silently.

    The suite previously asserted no delimiter-bearing tail at all, so it could not
    have caught the under-redaction a tightened ``_SECRETISH`` would introduce.
    """
    from fleet_rlm.rlm.result import sanitize_trace_text

    cleaned = sanitize_trace_text(raw, max_len=1_000)

    assert secret not in cleaned, f"{label}: {secret!r} survived -> {cleaned!r}"


def test_sanitizer_keeps_url_trailing_delimiters() -> None:
    """Regression: the secret pass ran first and ate the URL's closing ``")``."""
    from fleet_rlm.rlm.result import sanitize_trace_text

    cleaned = sanitize_trace_text('requests.get("http://host/preview?token=abc123")', max_len=1_000)

    assert cleaned == 'requests.get("[redacted-url]")'
