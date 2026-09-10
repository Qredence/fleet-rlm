"""Behavior contracts for typed results."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from fleet_rlm.rlm.submit_validation import is_submit_only_code


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


def test_success_requires_prediction_and_failure_forbids_it() -> None:
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    with pytest.raises(ValueError, match="prediction"):
        RLMOutcome(terminal_status="completed")
    with pytest.raises(ValueError, match="prediction"):
        RLMOutcome(
            terminal_status="failed",
            prediction=PredictionResult("done", {"answer": "done"}, "default", "1"),
        )


def test_declared_output_validator_accepts_identifiers_placeholders_and_security_terms() -> None:
    from fleet_rlm.rlm.result import validate_declared_public_value

    value = {
        "answer": "API_KEY and token are security identifiers; system prompt dumps must not be returned.",
        "examples": ["API_KEY=${API_KEY}", "Authorization: <AUTHORIZATION>", "Bearer TOKEN"],
        "mount": "/home/daytona/fleet",
        "api_key": "${FLEET_DAYTONA_API_KEY}",
    }

    validate_declared_public_value(value)


def test_declared_output_validator_accepts_benign_lowercase_bearer_prose() -> None:
    from fleet_rlm.rlm.result import validate_declared_public_value

    validate_declared_public_value("Use a bearer token supplied by the caller.")


@pytest.mark.parametrize(
    "value",
    [
        {"password": "correct-horse-battery-staple"},
        {"nested": {"private-key": "-----BEGIN PRIVATE KEY-----"}},
        {"FLEET_DAYTONA_API_KEY": "actual-provider-value"},
        {"provider_token": "actual-provider-value"},
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
