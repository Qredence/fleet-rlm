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
    from fleet_rlm.optimize.validation import IsolatedFleetProgram

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


@pytest.mark.parametrize("adapter_type", [dspy.ChatAdapter, dspy.JSONAdapter])
def test_canary_instruction_probe_uses_reflection_lm(monkeypatch, tmp_path, adapter_type):
    import json
    import sys

    namespace = _canary_namespace()
    main = namespace["main"]
    serving = DummyLM([{"answer": "4"}])
    reflection_adapter = adapter_type()
    reflection = DummyLM([{"new_instruction": "Add the integers correctly."}], adapter=reflection_adapter)
    ambient = DummyLM([{"answer": "unexpected ambient call"}])
    settings = SimpleNamespace(
        fleet_root_model="serving-model",
        fleet_reflection_model="reflection-model",
        fleet_daytona_api_key="offline",
        fleet_daytona_org_id="offline",
        fleet_daytona_volume_id="offline",
        build_judge_lm=lambda: serving,
        resolved_model=lambda role: SimpleNamespace(
            build_adapter=lambda: reflection_adapter if role == "reflection" else dspy.ChatAdapter()
        ),
    )
    settings.with_explicit_overrides = lambda **_kwargs: settings
    monkeypatch.setitem(
        main.__globals__, "build_lm", lambda _settings, _model_id, role: reflection if role == "reflection" else serving
    )
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

    def stop_before_search(**kwargs):
        assert dspy.settings.adapter is reflection_adapter
        assert kwargs["instruction_proposer"].adapter is reflection_adapter
        raise RuntimeError("Offline test stops after the two native probes")

    monkeypatch.setattr(dspy, "GEPA", stop_before_search)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["verify_gepa_live.py", "--live", "--output", str(report_path)])
    ambient_adapter = dspy.ChatAdapter()
    with dspy.context(lm=ambient, adapter=ambient_adapter):
        with pytest.raises(SystemExit, match="1"):
            main()
        assert dspy.settings.adapter is ambient_adapter

    report = json.loads(report_path.read_text())
    assert report["serving_probe"] is True
    assert report["native_instruction_json_probe"] is True
    assert len(serving.history) == 1
    assert reflection.history
    assert not ambient.history
