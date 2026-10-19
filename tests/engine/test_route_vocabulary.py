"""One route vocabulary: direct / flex / rlm."""

from types import SimpleNamespace

import pytest

from fleet_rlm.engine.signatures import TRIAGE_CRITERIA, TRIAGE_ROUTE, Route, parse_route


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
