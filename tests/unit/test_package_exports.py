"""Package export guards for fleet_rlm root module."""

from __future__ import annotations

import fleet_rlm


def test_root_all_exports_are_resolvable() -> None:
    for name in fleet_rlm.__all__:
        assert hasattr(fleet_rlm, name)
        assert getattr(fleet_rlm, name) is not None


def test_root_all_matches_declared_lazy_exports() -> None:
    expected_exports = {"__version__"} | set(fleet_rlm._LAZY_ATTRS)
    assert set(fleet_rlm.__all__) == expected_exports
