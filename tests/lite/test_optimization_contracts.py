"""Offline contracts shared by experiment HTTP control and standalone workers."""

from __future__ import annotations

import argparse
import io
import json

import pytest
from pydantic import ValidationError

from fleet_rlm.config import FleetSettings
from fleet_rlm.optimization_runs import (
    ExperimentOptions,
    add_optimization_arguments,
    read_worker_settings,
    worker_settings_payload,
)


def test_worker_snapshot_retains_effective_settings_and_sources_without_rereading_configuration(monkeypatch, tmp_path):
    settings = FleetSettings(_env_file=None, databricks_token="private-worker-token").with_explicit_overrides(
        turn_timeout_seconds=123
    )
    snapshot = worker_settings_payload(settings)
    assert b"private-worker-token" in snapshot
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "not-a-config.toml"))
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "invalid-env-must-not-be-read")
    received = read_worker_settings(io.StringIO(snapshot.decode()))
    assert received.configuration() == settings.configuration()
    assert received.databricks_token.get_secret_value() == "private-worker-token"
    assert received.configuration_source("turn_timeout_seconds") == "explicit"
    assert "private-worker-token" not in json.dumps(received.configuration())


def test_worker_snapshot_rejects_unbounded_or_invalid_input():
    with pytest.raises(ValueError, match="too large"):
        read_worker_settings(io.StringIO(" " * 1_000_001))
    with pytest.raises(ValueError, match="invalid worker"):
        read_worker_settings(io.StringIO('{"settings":{}}'))


def test_worker_snapshot_ignores_unrelated_dotenv_keys(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER_APP_SECRET=unrelated-secret\nFLEET_TURN_TIMEOUT_SECONDS=123\n")
    settings = FleetSettings(_env_file=dotenv)
    assert "other_app_secret" in settings._configuration_sources
    snapshot = worker_settings_payload(settings)
    received = read_worker_settings(io.StringIO(snapshot.decode()))
    assert b"other_app_secret" not in snapshot and b"unrelated-secret" not in snapshot
    assert received.configuration() == settings.configuration()
    assert received.configuration_source("turn_timeout_seconds") == "dotenv"


@pytest.mark.parametrize("ratio", [0.1, 0.5])
def test_ratio_bounds_agree_across_entry_points(ratio, monkeypatch):
    from fleet_rlm.api.schemas import StartExperiment
    from fleet_rlm.optimize import dataset
    from fleet_rlm.tui.commands import Context, SessionState, _parse_optimizer_arguments

    parser = argparse.ArgumentParser()
    add_optimization_arguments(parser)
    options = ExperimentOptions.from_namespace(parser.parse_args(["--session", "s1", "--validation-ratio", str(ratio)]))
    assert options.validation_ratio == ratio
    assert StartExperiment(request_id="request", validation_ratio=ratio).validation_ratio == ratio
    payload, review = _parse_optimizer_arguments(Context(None, "s1", SessionState()), f"--validation-ratio {ratio}")
    assert payload["validation_ratio"] == ratio and review is None
    monkeypatch.setattr(dataset, "load_examples_from_postgres", lambda *_args: [])
    monkeypatch.setattr(dataset, "load_examples_from_mlflow", lambda *_args, **_kwargs: [])
    with pytest.raises(ValueError, match="approved examples"):
        dataset.curate_dataset(None, "offline", "s1", validation_ratio=ratio)


@pytest.mark.parametrize("ratio", [0, 0.09, 0.51, 1, float("nan"), float("inf")])
def test_invalid_ratio_rejected_by_shared_and_direct_contract(ratio):
    from fleet_rlm.optimize.dataset import curate_dataset

    with pytest.raises(ValidationError):
        ExperimentOptions(validation_ratio=ratio)
    with pytest.raises(ValueError, match="validation_ratio"):
        curate_dataset(None, "offline", "s1", validation_ratio=ratio)


def test_cli_and_http_search_controls_have_one_validated_contract():
    parser = argparse.ArgumentParser()
    add_optimization_arguments(parser)
    args = parser.parse_args(["--session", "s1", "--max-metric-calls", "10", "--trace-search"])
    options = ExperimentOptions.from_namespace(args)
    assert options.gepa_budget is None and options.max_metric_calls == 10
    assert options.trace_search
    assert ExperimentOptions.model_validate(options.model_dump()) == options
    assert "--max-metric-calls" in options.cli_arguments() and "--gepa-budget" not in options.cli_arguments()
    with pytest.raises(ValidationError):
        ExperimentOptions(max_metric_calls=10, gepa_budget="light")
    with pytest.raises(ValidationError):
        ExperimentOptions(threads=True)
    with pytest.raises(ValidationError):
        ExperimentOptions.model_validate({"executable": "untrusted"})
