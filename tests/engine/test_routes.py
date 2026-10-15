"""One route vocabulary: direct / flex / rlm, with legacy names readable but never produced."""

from types import SimpleNamespace

import pytest

from fleet_rlm.engine.routes import Route, parse_route
from fleet_rlm.engine.signatures import TRIAGE_CRITERIA, TRIAGE_ROUTE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("direct", Route.DIRECT),
        ("flex", Route.FLEX),
        ("rlm", Route.RLM),
        ("direct_cot", Route.DIRECT),
        ("single_rlm", Route.FLEX),
        ("flex_pipeline", Route.FLEX),
        ("delegated_rlm", Route.RLM),
        ("deep_rlm", Route.RLM),
        (" rlm ", Route.RLM),
        (SimpleNamespace(value="flex"), Route.FLEX),
        (None, None),
        ("unknown", None),
    ],
)
def test_parse_route_reads_current_and_legacy_names(raw, expected):
    assert parse_route(raw) is expected


def test_triage_choices_are_exactly_the_route_values():
    assert set(TRIAGE_ROUTE.criteria()) == {route.value for route in Route}
    assert set(TRIAGE_CRITERIA) == {route.value for route in Route}
