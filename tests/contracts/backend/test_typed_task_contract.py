"""Optional Skill Signature structured-result contract."""

from types import SimpleNamespace

import dspy

from fleet_rlm.rlm.dspy_contract import prediction_result
from fleet_rlm.skills.signatures import DataAnalysisSignature


def test_custom_signature_outputs_are_validated_with_skill_schema_identity() -> None:
    prediction = dspy.Prediction(
        answer="Revenue increased.",
        findings=["Revenue increased 10%."],
        metrics=[{"name": "growth", "value": 0.1}],
        anomalies=[],
    )
    result = prediction_result(
        prediction,
        DataAnalysisSignature,
        schema_id="skill.data-analysis",
        schema_version="1.0.0",
    )
    assert result.display_text == "Revenue increased."
    assert result.schema_id == "skill.data-analysis"
    assert result.schema_version == "1.0.0"
    assert result.outputs["metrics"] == ({"name": "growth", "value": 0.1},)


def test_default_signature_schema_remains_fleet_default() -> None:
    from fleet_rlm.rlm.signature import FleetRLMSignature

    result = prediction_result(SimpleNamespace(answer="done"), FleetRLMSignature)
    assert (result.schema_id, result.schema_version) == ("fleet.default", "1")


def test_custom_signature_uses_the_same_json_compatible_common_inputs() -> None:
    from tests.unit.backend.rlm.test_signature_inputs import _payload

    payload = _payload()
    assert set(payload) == {"request", "session_context", "skill_cards", "attachments"}
    assert all(isinstance(value, (str, dict, list)) for value in payload.values())

    prediction = dspy.Prediction(
        answer="Analysis complete.",
        findings=["One finding"],
        metrics=[{"name": "coverage", "value": 1.0}],
        anomalies=[],
    )
    result = prediction_result(
        prediction,
        DataAnalysisSignature,
        schema_id="skill.data-analysis",
        schema_version="1.0.0",
    )
    assert result.display_text == "Analysis complete."
