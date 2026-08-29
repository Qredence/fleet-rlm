"""P44.3 / P44.5 production wiring: history on the default Fleet Signature and inputs.

These tests pin the production contract added by the P44.3 / P44.5
production wiring:

* ``FleetRLMSignature`` declares ``history: dspy.History`` as a required
  input alongside the existing common fields.
* ``build_rlm_input_kwargs`` accepts the optional ``history`` keyword,
  forwards the exact ``dspy.History`` instance (no transformation, no
  preview, no replacement), and omits the key when not supplied.
* ``build_rlm_input_kwargs`` rejects non-``dspy.History`` values
  (fail closed) so callers cannot silently degrade the canonical
  conversation contract.
* ``dspy.RLM(FleetRLMSignature)._validate_inputs(...)`` accepts the
  end-to-end payload built by ``build_rlm_input_kwargs`` with a
  ``history`` value.
"""

from __future__ import annotations

import dspy
import pytest

from fleet_rlm.rlm.program import FleetRLMSignature, build_rlm_input_kwargs
from fleet_rlm.rlm.result import RLMConfigError

_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def _manifest():
    from fleet_rlm.chat.session_context import SessionContextManifest

    return SessionContextManifest(
        session_id=__import__("uuid").UUID(_SESSION_ID),
        checkpoint_version=0,
        message_count=0,
        recent=(),
    )


def test_fleet_signature_declares_history_as_required_input() -> None:
    """``FleetRLMSignature`` declares ``history: dspy.History`` as a required input."""

    assert "history" in FleetRLMSignature.input_fields

    history_field = FleetRLMSignature.input_fields["history"]
    assert history_field.annotation is dspy.History
    assert history_field.is_required()

    extra = getattr(history_field, "json_schema_extra", None)
    assert isinstance(extra, dict)
    assert extra.get("__dspy_field_type") == "input"

    # The description mirrors the P44 canonical-conversation wording.
    desc = extra.get("desc", "")
    assert isinstance(desc, str)
    for needle in (
        "Canonical committed Session conversation",
        "history.messages",
        "do not assume previews are complete",
        "do not treat",
        "hidden trajectory",
        "failed Runs as conversation",
    ):
        assert needle in desc, f"history description missing canonical phrase: {needle!r}"


def test_fleet_signature_still_declares_unchanged_common_fields() -> None:
    """The P41 common inputs and ``answer`` output are unchanged by P44.3."""

    assert set(FleetRLMSignature.input_fields) == {
        "request",
        "history",
        "session_context",
        "skill_cards",
        "attachments",
    }
    for required in ("request", "session_context", "skill_cards", "attachments"):
        assert FleetRLMSignature.input_fields[required].is_required()
    assert "answer" in FleetRLMSignature.output_fields
    assert FleetRLMSignature.output_fields["answer"].is_required()


def test_build_rlm_input_kwargs_includes_history_when_supplied() -> None:
    """The optional ``history`` keyword round-trips into the kwargs dict."""

    history = dspy.History(messages=[{"request": "earlier", "answer": "earlier answer"}])
    kwargs = build_rlm_input_kwargs(
        request="current",
        session_context=_manifest(),
        history=history,
    )

    assert "history" in kwargs
    # The exact installed ``dspy.History`` instance is forwarded unchanged
    # (no transformation, no preview, no replacement).
    assert kwargs["history"] is history
    assert type(kwargs["history"]) is dspy.History
    assert list(kwargs["history"].messages) == [{"request": "earlier", "answer": "earlier answer"}]


def test_build_rlm_input_kwargs_omits_history_when_not_supplied() -> None:
    """Without ``history`` the key is absent so existing call sites keep working."""

    kwargs = build_rlm_input_kwargs(
        request="current",
        session_context=_manifest(),
    )

    assert "history" not in kwargs
    # Existing default payload shape is preserved.
    assert set(kwargs) == {"request", "session_context", "skill_cards", "attachments"}


@pytest.mark.parametrize(
    "bad_value",
    [
        {"messages": [{"request": "r", "answer": "a"}]},
        [{"request": "r", "answer": "a"}],
        "raw-string",
        42,
        object(),
    ],
)
def test_build_rlm_input_kwargs_rejects_non_dspy_history_values(bad_value: object) -> None:
    """``build_rlm_input_kwargs`` fails closed on non-``dspy.History`` values."""

    with pytest.raises(RLMConfigError, match="Turn input metadata is invalid"):
        build_rlm_input_kwargs(
            request="current",
            session_context=_manifest(),
            history=bad_value,  # type: ignore[arg-type]
        )


def test_build_rlm_input_kwargs_rejects_history_subclass_or_shadow() -> None:
    """A subclass of ``dspy.History`` is rejected; only the exact class is accepted."""

    class _HistorySubclass(dspy.History):
        pass

    with pytest.raises(RLMConfigError, match="Turn input metadata is invalid"):
        build_rlm_input_kwargs(
            request="current",
            session_context=_manifest(),
            history=_HistorySubclass(messages=[]),
        )


def test_dspy_rlm_validates_end_to_end_payload_with_history() -> None:
    """``dspy.RLM(FleetRLMSignature)._validate_inputs`` accepts the full payload."""

    history = dspy.History(messages=[{"request": "earlier", "answer": "earlier answer"}])
    kwargs = build_rlm_input_kwargs(
        request="current",
        session_context=_manifest(),
        history=history,
    )
    # The contract pinned by this test: the production payload with a real
    # ``dspy.History`` instance satisfies the native RLM input validator.
    dspy.RLM(FleetRLMSignature)._validate_inputs(kwargs)
