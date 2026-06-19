from __future__ import annotations

from typing import get_args


def test_trace_mode_literal_values_are_stable() -> None:
    from fleet_rlm.runtime.schemas import TraceMode

    assert get_args(TraceMode) == ("compact", "verbose", "off")
