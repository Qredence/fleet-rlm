"""Compiled programs reach the engine through one loader, from both engine and wrapper saves."""

from __future__ import annotations

import json
import os

import dspy
import pytest

from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.optimize.validation import IsolatedFleetProgram
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


@pytest.mark.parametrize("kind", ["predictor", "flex"])
def test_component_diff_distinguishes_absence_from_existing_empty(kind):
    from fleet_rlm.optimize.artifacts import component_diffs

    class Program(dspy.Module):
        def __init__(self, present):
            super().__init__()
            if present:
                if kind == "predictor":
                    self.component = dspy.Predict("task -> answer")
                    self.component.signature.instructions = ""
                else:
                    self.component = dspy.Flex("task -> answer")
                    self.component._module_src = ""

    absent, empty = Program(False), Program(True)
    changes = component_diffs(absent, empty)
    assert changes == [{"component": "component", "before": "", "after": "", "diff": ""}]
    assert component_diffs(empty, empty.deepcopy()) == []
    # The existing comparison policy ignores removed candidate keys.
    assert component_diffs(empty, absent) == []


LM = dspy.LM("openai/test", engine="lm15", cache=False)


def engine(name="sess_art", compiled_path=None) -> AdaptiveFleetEngine:
    return AdaptiveFleetEngine(DaytonaSandboxManager("key", "org", "volume", name), LM, LM, compiled_path=compiled_path)


def tuned() -> AdaptiveFleetEngine:
    program = engine()
    program.triage.signature = program.triage.signature.with_instructions("TUNED triage")
    return program


@pytest.mark.parametrize("saved_from", ["engine", "wrapper"])
def test_saved_candidate_loads_into_a_fresh_engine(tmp_path, saved_from):
    candidate = tuned()
    path = tmp_path / "candidate.json"
    if saved_from == "engine":
        candidate.save(str(path))
    else:
        IsolatedFleetProgram(candidate, lambda _example, program: program).save(str(path))
        assert next(iter(json.loads(path.read_text()))).startswith("program.")

    fresh = engine("sess_fresh", compiled_path=str(path))

    assert fresh.triage.signature.instructions == "TUNED triage"
    assert fresh.compiled.digest


def test_turn_time_reload_picks_up_a_changed_file(tmp_path):
    path = tmp_path / "sess_reload.json"
    engine().save(str(path))
    live = engine("sess_reload", compiled_path=str(path))
    assert live.triage.signature.instructions != "TUNED triage"

    tuned().save(str(path))
    os.utime(path, ns=(1, 1))  # distinct mtime even on coarse filesystems
    live.compiled.reload_if_changed(live)

    assert live.triage.signature.instructions == "TUNED triage"


def test_a_symlinked_artifact_is_refused(tmp_path):
    real = tmp_path / "real.json"
    engine().save(str(real))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="regular file"):
        engine("sess_link", compiled_path=str(link))
