"""Offline agent identity contracts; no tracking server or provider is contacted."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
from types import SimpleNamespace

import pytest

from fleet_rlm import observability as obs


class Span:
    def __init__(self):
        self.attributes = {}

    def set_attributes(self, values):
        self.attributes.update(values)

    def set_attribute(self, key, value):
        self.attributes[key] = value


@pytest.fixture
def registry(monkeypatch):
    models, associations = [], []
    monkeypatch.setattr(
        obs, "_STARTUP_IDENTITY", {"fleet.agent.source_digest": "source", "fleet.agent.git_dirty": "true"}
    )
    monkeypatch.setattr(obs, "_AGENT_MODELS", {})
    monkeypatch.setattr(obs.mlflow, "get_tracking_uri", lambda: "http://local.invalid")

    def search(**kwargs):
        return [m for m in models if m.tags["fleet.agent.fingerprint"] in kwargs["filter_string"]]

    def create(**kwargs):
        model = SimpleNamespace(model_id=f"m-{len(models)}", **kwargs)
        models.append(model)
        return model

    monkeypatch.setattr(obs.mlflow, "search_logged_models", search)
    monkeypatch.setattr(obs.mlflow, "create_external_model", create)
    monkeypatch.setattr(obs.mlflow, "update_current_trace", lambda **kwargs: associations.append(kwargs))
    monkeypatch.setattr(obs.mlflow, "set_active_model", lambda **_kw: pytest.fail("Global model context changed"))
    return models, associations


def engine(instruction="first", credential="private"):
    return SimpleNamespace(dump_state=lambda: {"predict": {"signature": instruction, "lm": {"api_key": credential}}})


def test_reuses_version_without_uploading_state_and_excludes_credentials(registry):
    models, associations = registry
    span = Span()
    first = obs.associate_turn_agent(engine(), "config", "1", span)
    assert obs.associate_turn_agent(engine(credential="rotated"), "config", "1", Span()) == first
    assert len(models) == 1 and len(associations) == 2
    assert "private" not in str(models[0].tags) and "signature" not in str(models[0].tags)
    assert models[0].name.startswith("fleet-rlm-")
    assert span.attributes["fleet.agent.model_id"] == first
    obs._AGENT_MODELS.clear()
    assert obs.associate_turn_agent(engine(), "config", "1", Span()) == first
    assert len(models) == 1  # Native search reuse after the process cache is empty.


def test_benchmark_trace_uses_requested_model_and_retains_automatic_identity(registry, monkeypatch):
    _, associations = registry
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "m-benchmark")
    monkeypatch.setenv(
        "FLEET_MLFLOW_BENCHMARK_TAGS",
        '{"fleet.benchmark.campaign":"cold-start-2026-11","fleet.benchmark.condition":"baseline"}',
    )
    span = Span()

    automatic_model_id = obs.associate_turn_agent(engine(), "config", "1", span)

    assert associations[-1]["model_id"] == "m-benchmark"
    assert associations[-1]["tags"] == {
        "fleet.benchmark.campaign": "cold-start-2026-11",
        "fleet.benchmark.condition": "baseline",
    }
    assert span.attributes["fleet.agent.model_id"] == automatic_model_id
    assert span.attributes["fleet.benchmark.model_id"] == "m-benchmark"


def test_benchmark_tags_attach_without_benchmark_model_id(registry, monkeypatch):
    _, associations = registry
    tags = {"fleet.benchmark.campaign": "cold-start-2026-11"}
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", '{"fleet.benchmark.campaign":"cold-start-2026-11"}')
    span = Span()
    automatic_model_id = obs.associate_turn_agent(engine(), "config", "1", span)
    assert associations[-1] == {"model_id": automatic_model_id, "tags": tags}
    assert span.attributes["fleet.agent.model_id"] == automatic_model_id
    assert span.attributes["fleet.benchmark.campaign"] == "cold-start-2026-11"
    assert "fleet.benchmark.model_id" not in span.attributes

    def fail(**_kw):
        raise RuntimeError("offline")

    monkeypatch.setattr(obs.mlflow, "search_logged_models", fail)
    unversioned = Span()
    assert obs.associate_turn_agent(engine("unversioned"), "config", "1", unversioned) is None
    assert associations[-1] == {"tags": tags}
    assert unversioned.attributes["fleet.agent.identity_status"] == "unversioned"
    assert unversioned.attributes["fleet.benchmark.campaign"] == "cold-start-2026-11"


def test_benchmark_env_strips_whitespace_and_rejects_empty_tag_suffix(registry, monkeypatch):
    _, associations = registry
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "   ")
    span = Span()
    automatic = obs.associate_turn_agent(engine(), "config", "1", span)
    assert associations[-1] == {"model_id": automatic}
    assert "fleet.benchmark.model_id" not in span.attributes

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "  m-bench  ")
    padded = Span()
    obs.associate_turn_agent(engine(), "config", "1", padded)
    assert associations[-1] == {"model_id": "m-bench"}
    assert padded.attributes["fleet.benchmark.model_id"] == "m-bench"

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", '{"fleet.benchmark.":"missing-name"}')
    invalid = Span()
    assert obs.associate_turn_agent(engine(), "config", "1", invalid) is None
    assert invalid.attributes["fleet.agent.identity_status"] == "unversioned"


def test_config_loaded_program_destination_and_source_affect_identity(registry, monkeypatch):
    models, _ = registry
    values = [obs.associate_turn_agent(engine(), "config", "1", Span())]
    values.append(obs.associate_turn_agent(engine("changed"), "config", "1", Span()))
    values.append(obs.associate_turn_agent(engine(), "changed", "1", Span()))
    monkeypatch.setattr(obs, "_STARTUP_IDENTITY", {"fleet.agent.source_digest": "another dirty snapshot"})
    values.append(obs.associate_turn_agent(engine(), "config", "1", Span()))
    assert len(set(values)) == len(models) == 4
    monkeypatch.setattr(obs.mlflow, "get_tracking_uri", lambda: "http://another.invalid")
    obs.associate_turn_agent(engine(), "config", "1", Span())
    assert len(obs._AGENT_MODELS) == 5


@pytest.mark.usefixtures("registry")
def test_parallel_sessions_associate_only_their_current_trace(monkeypatch):
    current = ContextVar("trace")
    recorded = {}
    monkeypatch.setattr(obs.mlflow, "update_current_trace", lambda **kwargs: recorded.update({current.get(): kwargs}))

    def run(index):
        current.set(index)
        model = obs.associate_turn_agent(engine(str(index)), "config", "1", Span())
        return index, model

    with ThreadPoolExecutor(max_workers=4) as pool:
        expected = dict(pool.map(run, range(8)))
    assert {index: value["model_id"] for index, value in recorded.items()} == expected
    assert len(set(expected.values())) == 8


@pytest.mark.usefixtures("registry")
def test_observation_failure_is_sanitized_and_does_not_raise(monkeypatch, caplog):
    def fail(**_kwargs):
        raise RuntimeError("credential-that-must-never-be-logged")

    monkeypatch.setattr(obs.mlflow, "search_logged_models", fail)
    span = Span()
    assert obs.associate_turn_agent(engine(), "config", "1", span) is None
    assert span.attributes["fleet.agent.identity_status"] == "unversioned"
    assert "credential-that-must-never-be-logged" not in caplog.text


@pytest.mark.usefixtures("registry")
def test_slow_lookup_does_not_block_other_cached_identities(monkeypatch):
    cached = obs.associate_turn_agent(engine("cached"), "config", "1", Span())
    entered, release = Event(), Event()
    original = obs.mlflow.search_logged_models

    def slow_search(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(obs.mlflow, "search_logged_models", slow_search)
    monkeypatch.setattr(obs, "_AGENT_LOOKUP_WAIT_SECONDS", 0.01)
    span = Span()
    try:
        assert obs.associate_turn_agent(engine("slow"), "config", "1", span) is None
        assert entered.is_set()
        assert span.attributes["fleet.agent.identity_status"] == "unversioned"
        assert obs.associate_turn_agent(engine("cached"), "config", "1", Span()) == cached
    finally:
        release.set()


def test_startup_snapshot_is_frozen_and_wheel_needs_no_git(tmp_path, monkeypatch):
    package = tmp_path / "wheel" / "fleet_rlm"
    package.mkdir(parents=True)
    (package / "engine.py").write_text("behavior = 1")
    (package / ".env").write_text("private configuration")
    (package / "memory.json").write_text("private memory")
    monkeypatch.setattr(obs.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()))
    first = obs.serving_snapshot(package)
    (package / ".env").write_text("rotated private configuration")
    assert obs.serving_snapshot(package) == first
    (package / "engine.py").write_text("behavior = 2")
    second = obs.serving_snapshot(package)
    assert first["fleet.agent.source_digest"] != second["fleet.agent.source_digest"]
    assert "fleet.agent.git_commit" not in first
    monkeypatch.setattr(obs, "_STARTUP_IDENTITY", None)
    monkeypatch.setattr(obs, "serving_snapshot", lambda: first)
    obs.capture_serving_snapshot()
    monkeypatch.setattr(obs, "serving_snapshot", lambda: second)
    obs.capture_serving_snapshot()
    assert first == obs._STARTUP_IDENTITY


def test_bundled_resource_changes_are_included_but_optimizer_and_data_are_excluded(tmp_path):
    (tmp_path / "engine.py").write_text("pass")
    resource = tmp_path / "bundled_skills" / "example" / "SKILL.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("first instruction")
    first = obs.serving_snapshot(tmp_path)
    resource.write_text("second instruction")
    second = obs.serving_snapshot(tmp_path)
    assert first["fleet.agent.source_digest"] != second["fleet.agent.source_digest"]
    (tmp_path / "optimize").mkdir()
    (tmp_path / "optimize" / "scorer.py").write_text("pass")
    (tmp_path / "dataset.csv").write_text("sensitive")
    assert obs.serving_snapshot(tmp_path) == second


def test_native_logged_model_search_reuse_and_persisted_trace_association(tmp_path):
    """Exercise pinned MLflow against a disposable local store in an isolated process."""
    import os
    import subprocess
    import sys

    program = """
import sys
from pathlib import Path
from types import SimpleNamespace
import mlflow
from fleet_rlm import observability as obs
root = Path(sys.argv[1])
mlflow.set_tracking_uri(f"sqlite:///{root / 'tracking.db'}")
experiment = mlflow.create_experiment("agent-contract", artifact_location=(root / "artifacts").as_uri())
mlflow.set_experiment(experiment_id=experiment)
obs._STARTUP_IDENTITY = {"fleet.agent.source_digest": "offline-source"}
benchmark_model = mlflow.create_external_model(name="benchmark", experiment_id=experiment, model_type="agent")
import os
os.environ["FLEET_MLFLOW_BENCHMARK_MODEL_ID"] = benchmark_model.model_id
os.environ["FLEET_MLFLOW_BENCHMARK_TAGS"] = '{"fleet.benchmark.campaign":"offline"}'
engine = SimpleNamespace(dump_state=lambda: {"predict": {"signature": "loaded-program", "lm": None}})
with mlflow.start_span(name="fleet_turn") as span:
    first = obs.associate_turn_agent(engine, "config", experiment, span)
    assert first is not None
    assert span.attributes["fleet.agent.model_id"] == first
    trace_id = span.trace_id
obs._AGENT_MODELS.clear()
with mlflow.start_span(name="fleet_turn") as span:
    assert obs.associate_turn_agent(engine, "config", experiment, span) == first
models = mlflow.search_logged_models(experiment_ids=[experiment], output_format="list")
assert {model.model_id for model in models} == {benchmark_model.model_id, first}
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
assert trace.info.trace_metadata["mlflow.modelId"] == benchmark_model.model_id
assert trace.info.tags["fleet.benchmark.campaign"] == "offline"
assert trace.data.spans[0].attributes["fleet.agent.model_id"] == first
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"},
    )
    assert result.returncode == 0, result.stderr
