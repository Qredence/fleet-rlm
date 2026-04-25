from __future__ import annotations

import pytest

from fleet_rlm.integrations.daytona import run_daytona_smoke
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

pytestmark = [pytest.mark.integration, pytest.mark.live_daytona]


def test_daytona_smoke_live(require_daytona_runtime) -> None:
    del require_daytona_runtime

    result = run_daytona_smoke(repo="https://github.com/qredence/fleet-rlm.git")

    assert result.driver_started is True
    assert result.persisted_state_value == 5
    assert result.finalization_mode == "SUBMIT"
    assert result.termination_phase == "completed"
    assert result.error_category is None
    assert result.error_message is None
    assert "cleanup" in result.phase_timings_ms
    assert result.sandbox_id
    assert result.workspace_path


def test_daytona_recursive_child_lifecycle_live(require_daytona_runtime) -> None:
    del require_daytona_runtime

    parent = DaytonaInterpreter(timeout=120, child_isolation_mode="auto")
    child = None
    try:
        parent.start()
        child = parent.build_delegate_child(remaining_llm_budget=2)
        child.start()
        metadata = child.current_runtime_metadata()

        assert metadata["sandbox_active"] is True
        assert metadata["sandbox_id"]
        assert metadata["child_isolation"]["mode"] == "auto"
        assert metadata["child_isolation"]["strategy"] in {"fork", "clean"}
    finally:
        if child is not None:
            child.shutdown()
        parent.shutdown()
