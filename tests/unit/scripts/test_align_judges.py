from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from scripts.benchmarks.align_judges import (
    AlignmentError,
    _label_schema_definition,
    align,
    build_parser,
    main,
)


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tagged_traces: list | None = None,
    semantic_memory: list | None = None,
    sampling: dict[str, tuple[float | None, str | None]] | None = None,
) -> SimpleNamespace:
    calls = SimpleNamespace(updated=[])
    sampling = sampling or {
        "correctness": (0.1, "tag.correctness = 'true'"),
        "evidence_coverage": (0.0, None),
    }

    class _AlignedJudge:
        _semantic_memory = semantic_memory
        version = 7

        def update(self, *, experiment_id=None, sampling_config=None, **_kwargs):
            calls.updated.append(
                {
                    "experiment_id": experiment_id,
                    "sample_rate": sampling_config.sample_rate,
                    "filter_string": sampling_config.filter_string,
                }
            )
            return self

    class _BaseJudge:
        def __init__(self, name: str):
            self.sampling_config = _SamplingConfig(*sampling[name])

        def align(self, *, traces, optimizer=None):
            assert traces
            assert optimizer is not None
            return _AlignedJudge()

    class _SamplingConfig:
        def __init__(self, sample_rate=None, filter_string=None):
            self.sample_rate = sample_rate
            self.filter_string = filter_string

    class _MemAlignOptimizer:
        def __init__(self, reflection_lm=None, retrieval_k=5, embedding_model=None, **_kwargs):
            self.reflection_lm = reflection_lm
            self.retrieval_k = retrieval_k
            self.embedding_model = embedding_model

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.set_experiment = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    mlflow.get_experiment_by_name = lambda _name: SimpleNamespace(experiment_id="exp-1")  # type: ignore[attr-defined]
    mlflow.search_traces = lambda **_kwargs: list(tagged_traces or [])  # type: ignore[attr-defined]

    scorers_mod = ModuleType("mlflow.genai.scorers")
    scorers_mod.get_scorer = lambda *_args, **kwargs: _BaseJudge(kwargs["name"])  # type: ignore[attr-defined]
    scorers_mod.ScorerSamplingConfig = _SamplingConfig  # type: ignore[attr-defined]

    optimizers_mod = ModuleType("mlflow.genai.judges.optimizers")
    optimizers_mod.MemAlignOptimizer = _MemAlignOptimizer  # type: ignore[attr-defined]
    judges_mod = ModuleType("mlflow.genai.judges")
    judges_mod.optimizers = optimizers_mod  # type: ignore[attr-defined]

    genai_mod = ModuleType("mlflow.genai")
    genai_mod.scorers = scorers_mod  # type: ignore[attr-defined]
    genai_mod.judges = judges_mod  # type: ignore[attr-defined]
    mlflow.genai = genai_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_mod)
    monkeypatch.setitem(sys.modules, "mlflow.genai.judges", judges_mod)
    monkeypatch.setitem(sys.modules, "mlflow.genai.judges.optimizers", optimizers_mod)
    return calls


def _args(argv: list[str], tmp_path) -> object:
    return build_parser().parse_args([*argv, "--output", str(tmp_path / "receipt.json")])


def test_label_schema_definitions_pair_boolean_judges_with_pass_fail_inputs() -> None:
    for name in ("correctness", "evidence_coverage"):
        import mlflow.genai.label_schemas as label_schemas

        _ = label_schemas
        definition = _label_schema_definition(name)
        assert definition["name"] == name
        assert definition["type"] == "feedback"
        assert definition["enable_comment"] is True
        assert definition["overwrite"] is True


def test_align_requires_tagged_traces(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch, tagged_traces=[])
    with pytest.raises(AlignmentError, match="prepare-labeling"):
        align(_args(["align"], tmp_path))


def test_align_preserves_active_and_paused_sampling_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch, tagged_traces=[{"trace_id": "t1"}], semantic_memory=[1, 2, 3])

    receipt = align(_args(["align"], tmp_path))

    assert receipt["experiment_id"] == "exp-1"
    assert [row["name"] for row in receipt["judges"]] == ["correctness", "evidence_coverage"]
    assert receipt["judges"][0]["guideline_count"] == 3
    assert receipt["judges"][0]["traces"] == 1
    assert calls.updated == [
        {"experiment_id": "exp-1", "sample_rate": 0.1, "filter_string": "tag.correctness = 'true'"},
        {"experiment_id": "exp-1", "sample_rate": 0.0, "filter_string": None},
    ]
    assert receipt["judges"][0]["prior_sample_rate"] == 0.1
    assert receipt["judges"][0]["resulting_sample_rate"] == 0.1
    assert receipt["judges"][0]["monitoring_state"] == "active"
    assert receipt["judges"][0]["aligned_version"] == 7
    assert receipt["judges"][1]["monitoring_state"] == "paused"


def test_prepare_labeling_requires_assigned_users(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch)
    from scripts.benchmarks.align_judges import prepare_labeling

    with pytest.raises(AlignmentError, match="assigned-users"):
        prepare_labeling(_args(["prepare-labeling"], tmp_path))


def test_main_writes_bounded_failure_receipt_without_live_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FLEET_LIVE", "0")
    output = tmp_path / "failed.json"
    assert main(["align", "--output", str(output)]) == 1
    payload = json.loads(output.read_text())
    generated_at = payload.pop("generated_at")
    assert generated_at
    assert payload == {
        "schema": "fleet.judge-alignment/v1",
        "command": "align",
        "status": "failed",
        "error_category": "AlignmentError",
    }
