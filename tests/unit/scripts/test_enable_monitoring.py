from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.benchmarks.enable_monitoring import (
    MonitoringError,
    build_parser,
    main,
    start,
    status,
    stop,
)


class _FakeScorer:
    def __init__(self, name: str, calls: SimpleNamespace) -> None:
        self.name = name
        self._calls = calls
        self.sampling_config = None

    def register(self, *, experiment_id: str):
        self._calls.registered.append((self.name, experiment_id))
        return self

    def start(self, *, sampling_config, **_kwargs):
        self.sampling_config = sampling_config
        self._calls.started.append((self.name, sampling_config.sample_rate))
        return self

    def stop(self, **_kwargs):
        self._calls.stopped.append(self.name)
        return self


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch, *, registered: list[str] | None = None):
    monkeypatch.delenv("FLEET_MLFLOW_EXPERIMENT_NAME", raising=False)
    calls = SimpleNamespace(started=[], stopped=[], registered=[])
    registry = {name: _FakeScorer(name, calls) for name in (registered or [])}

    class _SamplingConfig:
        def __init__(self, sample_rate=None, filter_string=None):
            self.sample_rate = sample_rate
            self.filter_string = filter_string

    def get_scorer(name=None, **_kwargs):
        return registry[name]

    class _Safety(_FakeScorer):
        def __init__(self):
            super().__init__("safety", calls)

        def register(self, *, experiment_id: str):
            super().register(experiment_id=experiment_id)
            registry["safety"] = self
            return self

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.get_experiment_by_name = lambda name: SimpleNamespace(experiment_id="exp-1") if name == "fleet-rlm" else None  # type: ignore[attr-defined]

    scorers_mod = ModuleType("mlflow.genai.scorers")
    scorers_mod.ScorerSamplingConfig = _SamplingConfig  # type: ignore[attr-defined]
    scorers_mod.get_scorer = get_scorer  # type: ignore[attr-defined]
    scorers_mod.list_scorers = lambda **_kwargs: list(registry.values())  # type: ignore[attr-defined]
    scorers_mod.Safety = _Safety  # type: ignore[attr-defined]

    genai_mod = ModuleType("mlflow.genai")
    genai_mod.scorers = scorers_mod  # type: ignore[attr-defined]
    mlflow.genai = genai_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_mod)
    return calls


def _args(argv: list[str], tmp_path) -> object:
    return build_parser().parse_args([*argv, "--output", str(tmp_path / "receipt.json")])


def test_start_registers_safety_and_starts_fleet_judges(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, registered=["correctness", "evidence_coverage"])

    receipt = start(_args(["start", "--sample-rate", "0.25"], tmp_path))

    by_name = {action["name"]: action for action in receipt["scorers"]}
    assert by_name["correctness"]["sample_rate"] == 0.25
    assert by_name["evidence_coverage"]["sample_rate"] == 0.25
    assert by_name["safety"]["sample_rate"] == 1.0
    assert calls.registered == [("safety", "exp-1")]
    assert sorted(name for name, _rate in calls.started) == ["correctness", "evidence_coverage", "safety"]


def test_start_refuses_non_databricks_tracking_uri(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch, registered=[])

    with pytest.raises(MonitoringError, match="databricks"):
        start(_args(["start", "--mlflow-url", "http://127.0.0.1:5001"], tmp_path))


def test_start_requires_registered_fleet_judges(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch, registered=[])

    with pytest.raises(MonitoringError, match="not registered"):
        start(_args(["start"], tmp_path))


def test_status_reports_sampling_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, registered=["correctness", "evidence_coverage"])
    args = _args(["start"], tmp_path)
    start(args)

    report = status(args)

    assert report["experiment_id"] == "exp-1"
    scorer_rows = {row["name"]: row for row in report["scorers"]}
    assert scorer_rows["correctness"]["sample_rate"] == 0.1
    assert scorer_rows["safety"]["sample_rate"] == 1.0
    assert calls.registered == [("safety", "exp-1")]


def test_stop_halts_registered_scorers_and_notes_missing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, registered=["correctness"])

    receipt = stop(_args(["stop"], tmp_path))

    by_name = {action["name"]: action["action"] for action in receipt["scorers"]}
    assert by_name == {"correctness": "stopped", "evidence_coverage": "not_registered", "safety": "not_registered"}
    assert calls.stopped == ["correctness"]


def test_main_writes_failure_receipt_for_out_of_range_sample_rate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    output = tmp_path / "failed.json"
    assert main(["start", "--sample-rate", "2.0", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    assert payload.pop("generated_at")
    assert payload == {
        "schema": "fleet.monitoring-config/v1",
        "command": "start",
        "status": "failed",
        "error_category": "MonitoringError",
    }
