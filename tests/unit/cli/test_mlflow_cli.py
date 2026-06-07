from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests.fixtures.env import apply_mlflow_env


def _load_cli_module():
    return importlib.import_module("scripts.mlflow_cli")


def _fake_mlflow_module(
    *,
    scorers: list[object] | None = None,
    deleted: list[dict[str, object]] | None = None,
    scorer: object | None = None,
):
    module = ModuleType("mlflow")
    module.tracking_uris = []  # type: ignore[attr-defined]

    def set_tracking_uri(uri: str) -> None:
        module.tracking_uris.append(uri)  # type: ignore[attr-defined]

    def set_experiment(*, experiment_name: str):
        module.experiment_name = experiment_name  # type: ignore[attr-defined]
        return SimpleNamespace(experiment_id="exp-active")

    def list_scorers(*, experiment_id: str | None = None):
        module.list_experiment_id = experiment_id  # type: ignore[attr-defined]
        return list(scorers or [])

    def delete_scorer(*, name: str, experiment_id: str | None = None, version: int | str | None = None) -> None:
        if deleted is not None:
            deleted.append({"name": name, "experiment_id": experiment_id, "version": version})

    def get_scorer(*, name: str, experiment_id: str | None = None, version: int | str | None = None):
        module.get_scorer_args = {"name": name, "experiment_id": experiment_id, "version": version}  # type: ignore[attr-defined]
        return scorer

    class ScorerSamplingConfig:
        def __init__(self, *, sample_rate: float | None = None, filter_string: str | None = None) -> None:
            self.sample_rate = sample_rate
            self.filter_string = filter_string

    module.set_tracking_uri = set_tracking_uri  # type: ignore[attr-defined]
    module.set_experiment = set_experiment  # type: ignore[attr-defined]
    scorers_module = ModuleType("mlflow.genai.scorers")
    scorers_module.ScorerSamplingConfig = ScorerSamplingConfig  # type: ignore[attr-defined]
    genai_module = ModuleType("mlflow.genai")
    genai_module.list_scorers = list_scorers  # type: ignore[attr-defined]
    genai_module.get_scorer = get_scorer  # type: ignore[attr-defined]
    genai_module.delete_scorer = delete_scorer  # type: ignore[attr-defined]
    genai_module.scorers = scorers_module  # type: ignore[attr-defined]
    module.genai = genai_module  # type: ignore[attr-defined]
    return module


def test_scorers_list_prints_persisted_scorers(
    monkeypatch: pytest.MonkeyPatch,
    clean_runtime_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apply_mlflow_env(clean_runtime_env)
    fake_mlflow = _fake_mlflow_module(
        scorers=[
            SimpleNamespace(name="Trace Judge", id="s-1", version=2, model="openai/test"),
            {"scorer_name": "retired", "scorer_id": "s-2", "scorer_version": 1, "model_uri": "models:/judge"},
        ]
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    cli = _load_cli_module()
    result = cli.do_scorers_list(SimpleNamespace(experiment_id=None))

    output = capsys.readouterr().out
    assert result == 0
    assert "tracking_uri=http://127.0.0.1:6001" in output
    assert "experiment=fleet-rlm-test" in output
    assert "experiment_id=exp-active" in output
    assert "scorer_count=2" in output
    assert "scorer name=Trace Judge id=s-1 version=2 model=openai/test" in output
    assert "scorer name=retired id=s-2 version=1 model=models:/judge" in output
    assert fake_mlflow.list_experiment_id == "exp-active"  # type: ignore[attr-defined]


def test_scorers_delete_requires_explicit_yes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _load_cli_module()

    result = cli.do_scorers_delete(SimpleNamespace(name="Trace Judge", experiment_id=None, version=None, yes=False))

    assert result == 2
    assert "Refusing to delete scorer without --yes." in capsys.readouterr().out


def test_scorers_delete_calls_mlflow_delete_scorer(
    monkeypatch: pytest.MonkeyPatch,
    clean_runtime_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apply_mlflow_env(clean_runtime_env)
    deleted: list[dict[str, object]] = []
    fake_mlflow = _fake_mlflow_module(deleted=deleted)
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    cli = _load_cli_module()
    result = cli.do_scorers_delete(
        SimpleNamespace(name="Trace Judge", experiment_id="exp-override", version="7", yes=True)
    )

    output = capsys.readouterr().out
    assert result == 0
    assert deleted == [{"name": "Trace Judge", "experiment_id": "exp-override", "version": "7"}]
    assert "deleted_scorer=Trace Judge" in output
    assert "experiment_id=exp-override" in output


def test_scorers_stop_calls_registered_scorer_stop(
    monkeypatch: pytest.MonkeyPatch,
    clean_runtime_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apply_mlflow_env(clean_runtime_env)
    stopped: list[str] = []

    scorer = SimpleNamespace(stop=lambda: stopped.append("called"))
    fake_mlflow = _fake_mlflow_module(scorer=scorer)
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    cli = _load_cli_module()
    result = cli.do_scorers_stop(SimpleNamespace(name="Trace Judge", experiment_id=None))

    output = capsys.readouterr().out
    assert result == 0
    assert stopped == ["called"]
    assert "stopped_scorer=Trace Judge" in output


def test_scorers_start_calls_registered_scorer_start(
    monkeypatch: pytest.MonkeyPatch,
    clean_runtime_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    apply_mlflow_env(clean_runtime_env)
    started: list[dict[str, object]] = []

    def start(*, sampling_config) -> None:
        started.append(
            {
                "sample_rate": sampling_config.sample_rate,
                "filter_string": sampling_config.filter_string,
            }
        )

    scorer = SimpleNamespace(start=start)
    fake_mlflow = _fake_mlflow_module(scorer=scorer)
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.genai", fake_mlflow.genai)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", fake_mlflow.genai.scorers)  # type: ignore[attr-defined]

    cli = _load_cli_module()
    result = cli.do_scorers_start(
        SimpleNamespace(
            name="Trace Judge",
            experiment_id="exp-override",
            sample_rate=0.5,
            filter_string="status = 'OK'",
        )
    )

    output = capsys.readouterr().out
    assert result == 0
    assert started == [
        {
            "sample_rate": 0.5,
            "filter_string": "status = 'OK'",
        }
    ]
    assert "started_scorer=Trace Judge" in output
    assert "sample_rate=0.5" in output
