"""Keep the canary's native instruction probe offline and bound to reflection."""

from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import dspy
import pytest
from dspy.utils import DummyLM


def _canary_namespace():
    return run_path(str(Path(__file__).resolve().parents[2] / "scripts/verify_gepa_live.py"))


def test_canary_rollout_satisfies_isolated_program_runtime_contract():
    from fleet_rlm.optimize.artifacts import IsolatedFleetProgram

    namespace = _canary_namespace()
    cleanup = []
    manager = SimpleNamespace(
        interpreter_factory=lambda: lambda: None,
        reset_turn_budget=lambda: None,
        consume_budget=lambda *_args: None,
        turn_usage=lambda: {"lm_calls": 0, "sandbox_errors": 0},
        end_turn=lambda: None,
        shutdown=lambda: cleanup.append("shutdown"),
    )
    program = namespace["AdditionProgram"](manager, DummyLM([]))

    class Answer(dspy.Module):
        def forward(self, task):
            return dspy.Prediction(answer=task)

    program.flex_agent = Answer()
    isolated = IsolatedFleetProgram(program, lambda _example, _candidate: program)
    assert isolated(task="fixture").answer == "fixture"
    assert cleanup == ["shutdown"]


def test_canary_instruction_probe_uses_reflection_lm(monkeypatch, tmp_path):
    import json
    import sys

    namespace = _canary_namespace()
    main = namespace["main"]
    serving = DummyLM([{"answer": "4"}])
    reflection = DummyLM([{"new_instruction": "Add the integers correctly."}], adapter=dspy.JSONAdapter())
    ambient = DummyLM([{"answer": "unexpected ambient call"}])
    settings = SimpleNamespace(
        fleet_daytona_api_key="offline",
        fleet_daytona_org_id="offline",
        fleet_daytona_volume_id="offline",
        build_lms=lambda: (serving, serving, reflection, None),
        build_judge_lm=lambda: serving,
    )
    settings.with_explicit_overrides = lambda **_kwargs: settings
    monkeypatch.setitem(main.__globals__, "FleetSettings", lambda **_kwargs: settings)
    monkeypatch.setitem(
        main.__globals__,
        "DaytonaSandboxManager",
        lambda *_args, **_kwargs: SimpleNamespace(_client=None, shutdown=lambda: None),
    )
    monkeypatch.setitem(
        main.__globals__,
        "AdditionProgram",
        lambda *_args: SimpleNamespace(flex_agent=SimpleNamespace(load_state=lambda _state: None)),
    )
    monkeypatch.setitem(main.__globals__, "IsolatedFleetProgram", lambda seed, _factory: seed)

    def stop_before_search(**_kwargs):
        raise RuntimeError("Offline test stops after the two native probes")

    monkeypatch.setattr(dspy, "GEPA", stop_before_search)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["verify_gepa_live.py", "--live", "--output", str(report_path)])
    with dspy.context(lm=ambient), pytest.raises(SystemExit, match="1"):
        main()

    report = json.loads(report_path.read_text())
    assert report["serving_probe"] is True
    assert report["native_instruction_json_probe"] is True
    assert len(serving.history) == 1
    assert reflection.history
    assert not ambient.history
