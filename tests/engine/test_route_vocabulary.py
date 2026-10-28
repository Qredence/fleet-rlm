"""One route vocabulary: direct / flex / rlm."""

from types import SimpleNamespace

import pytest

from fleet_rlm.contracts import TRIAGE_CRITERIA, TRIAGE_ROUTE, Route, parse_route


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("direct", Route.DIRECT),
        ("flex", Route.FLEX),
        ("rlm", Route.RLM),
        (" rlm ", Route.RLM),
        (SimpleNamespace(value="flex"), Route.FLEX),
        (None, None),
        ("unknown", None),
        ("direct_cot", None),
        ("deep_rlm", None),
    ],
)
def test_parse_route_reads_only_current_names(raw, expected):
    assert parse_route(raw) is expected


def test_triage_choices_are_exactly_the_route_values():
    assert set(TRIAGE_ROUTE.criteria()) == {route.value for route in Route}
    assert set(TRIAGE_CRITERIA) == {route.value for route in Route}


def test_route_wording_has_one_source():
    from fleet_rlm.contracts import JUDGE_ROUTE_CHOICES, ROUTE_DESCRIPTIONS, ROUTE_SUMMARIES

    assert set(ROUTE_DESCRIPTIONS) == set(Route)
    assert dict(JUDGE_ROUTE_CHOICES) == {route.value: item["judge"] for route, item in ROUTE_DESCRIPTIONS.items()}
    assert {route.value: item["summary"] for route, item in ROUTE_DESCRIPTIONS.items()} == ROUTE_SUMMARIES
    for route, item in ROUTE_DESCRIPTIONS.items():
        assert {"triage", "judge", "definition", "examples", "not", "summary"} <= set(item), route


def test_triage_instruction_text_is_unchanged():
    # The router's output description is a GEPA-tuned instruction; its wording is contract, not prose.
    from fleet_rlm.contracts import TRIAGE_INSTRUCTION
    from fleet_rlm.engine.signatures import TurnTriage

    assert TRIAGE_INSTRUCTION == (
        "Choose direct for a simple response, flex for one bounded Python or Skill workflow, "
        "or rlm for a multi-part task needing independent investigation."
    )
    assert TurnTriage.model_fields["route"].json_schema_extra["desc"] == TRIAGE_INSTRUCTION


def test_catalog_parser_is_shared_and_reports_malformed_xml():
    from fleet_rlm.contracts import catalog_entries

    catalog = '<available_skills>\n  <skill name="alpha">Use for A</skill>\n</available_skills>'
    assert catalog_entries(catalog) == [("alpha", "Use for A")]
    assert catalog_entries("<available_skills>No Skills installed.</available_skills>") == []
    assert catalog_entries("not xml") is None


def test_curated_examples_share_the_engine_input_contract():
    from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, LINEAGE_INPUT_FIELDS
    from fleet_rlm.optimize.dataset import _INPUTS

    assert (*ENGINE_INPUT_FIELDS, *LINEAGE_INPUT_FIELDS) == _INPUTS
