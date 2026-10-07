"""Monitoring eligibility and activation guards, without provider requests."""

from types import SimpleNamespace as Obj

import pytest
from mlflow.genai.scorers import ScorerSamplingConfig

from scripts import mlflow_monitoring as monitoring


def fleet_trace():
    root = Obj(name="fleet_turn", parent_id=None, inputs={"request": "Compute 2 + 2"}, outputs={"answer": "4"})
    settlement = Obj(
        name="Turn.settlement",
        parent_id="root",
        attributes={"settlement_status": "completed", "settlement_durable": True},
    )
    commit = Obj(
        name="database.commit",
        parent_id="root",
        attributes={"mlflow.spanOutputs": {"outcome": "completed"}},
        status=Obj(status_code=Obj(value="OK")),
    )
    return Obj(
        info=Obj(
            timestamp_ms=100,
            state="OK",
            tags={"fleet.trace_phase": "execution", "mlflow.traceName": "fleet_turn"},
            trace_metadata={},
        ),
        data=Obj(spans=[root, settlement, commit]),
    )


def test_committed_root_with_public_history_is_eligible():
    assert monitoring.eligibility_reason(fleet_trace(), 100, "completed") is None


@pytest.mark.parametrize("state", ["unknown", "cancelled", "failed"])
def test_missing_or_failed_durable_history_is_ineligible(state):
    assert monitoring.eligibility_reason(fleet_trace(), 100, state) == "unreconciled durable completion"


def test_historical_preparation_and_evaluation_traces_are_excluded():
    trace = fleet_trace()
    assert monitoring.eligibility_reason(trace, 101, "completed") == "historical"
    trace.info.tags["fleet.trace_phase"] = "preparation"
    assert monitoring.eligibility_reason(trace, 100, "completed") == "not completed execution"
    trace.info.tags["fleet.trace_phase"] = "execution"
    trace.info.trace_metadata["mlflow.sourceRun"] = "evaluation-run"
    assert monitoring.eligibility_reason(trace, 100, "completed") == "not user root execution"


def test_successful_trace_without_answer_or_commit_is_excluded():
    trace = fleet_trace()
    trace.data.spans[0].outputs = {}
    assert monitoring.eligibility_reason(trace, 100, "completed") == "missing valid input or final answer"
    assert monitoring.eligibility_reason(trace, 100, "completed", committed_answer="4") == (
        "missing valid input or final answer"
    )
    trace = fleet_trace()
    trace.data.spans.pop()
    assert monitoring.eligibility_reason(trace, 100, "completed") == "unreconciled durable completion"


def test_truncated_benchmark_is_invalid_input():
    trace = fleet_trace()
    trace.data.spans[0].inputs = {
        "request": (
            "The following lines contain 10 text messages\nDate: one\nIn the above data, which label is most common?"
        )
    }
    assert monitoring.eligibility_reason(trace, 100, "completed") == "invalid benchmark input"


def test_trace_preview_truncation_is_not_invalid_submission():
    trace = fleet_trace()
    trace.data.spans[0].inputs = {"request": "The following lines contain 2 text messages\nDate: first..."}
    assert monitoring.eligibility_reason(trace, 100, "completed") == (
        "input preview incomplete; committed input unavailable"
    )
    full = "The following lines contain 2 text messages\nDate: first\nDate: second\nIn the above data, which label?"
    assert monitoring.eligibility_reason(trace, 100, "completed", committed_request=full) is None


def test_committed_input_is_bound_to_assistant_trace_and_run(monkeypatch):
    trace = fleet_trace()
    trace.info.trace_id = "trace"
    trace.info.tags.update({"fleet.session_id": "session", "fleet.run_id": "run"})
    user = {"role": "user", "metadata": {"sequence": 1}, "parts": [{"type": "text", "text": "full input"}]}
    assistant = {
        "role": "assistant",
        "metadata": {"sequence": 2, "traceId": "trace", "runId": "another-run"},
        "parts": [{"type": "text", "text": "answer"}],
    }
    monkeypatch.setattr(monitoring.op, "history", lambda _: [user, assistant])
    assert monitoring.committed_turn_evidence(trace) == ("identity_mismatch", None, None)
    assistant["metadata"]["runId"] = "run"
    assert monitoring.committed_turn_evidence(trace) == ("completed", "full input", "answer")


@pytest.mark.parametrize(
    "rationale,value",
    [
        ("Placeholder", True),
        ("Placeholder — evaluation not yet performed", None),
        ("...", None),
        ("Evidence absent", True),
    ],
)
def test_false_passes_and_placeholder_rationales_block_calibration(monkeypatch, rationale, value):
    receipt = {"jobs": {"evidence_support": [{"job_id": "job"}]}, "expected": {"trace": {"evidence_support": None}}}
    monkeypatch.setattr(monitoring.op, "read_json", lambda _: receipt)
    monkeypatch.setattr(monitoring.op, "write_json", lambda *_: None)
    monkeypatch.setattr(
        monitoring.op,
        "monitoring_request",
        lambda _: {
            "status": "SUCCEEDED",
            "result": {"trace": {"assessments": [{"feedback": {"value": value}, "rationale": rationale}]}},
        },
    )
    assert monitoring.calibration_status()["passed"]["evidence_support"] is False


def test_failed_calibration_never_calls_registration_or_activation(monkeypatch):
    monkeypatch.setattr(
        monitoring,
        "calibration_status",
        lambda: {"definitions_hash": monitoring.definitions_hash(), "passed": {"answer_correctness": False}},
    )
    monkeypatch.setattr(monitoring, "get_scorer", lambda **_: pytest.fail("Must not touch registered judges"))
    monitoring.activate(apply=True)


def test_changed_inference_settings_cannot_reuse_previous_calibration(monkeypatch):
    judge_candidates = monitoring.candidates()
    monkeypatch.setattr(
        monitoring,
        "calibration_status",
        lambda: {
            "definitions_hash": monitoring.definitions_hash(),
            "serialized_definitions_hash": "another-model-or-inference-mode",
            "passed": {"answer_correctness": True},
        },
    )
    monkeypatch.setattr(monitoring, "candidates", lambda: judge_candidates)
    monkeypatch.setattr(monitoring, "get_scorer", lambda **_: pytest.fail("Must not touch registered judges"))
    with pytest.raises(ValueError, match="inference settings"):
        monitoring.activate(apply=True)


def _registered_judge(judge, *, version=1, sample_rate=0, filter_string=None):
    return judge._set_registration_metadata(
        backend="test",
        experiment_id=monitoring.op.EXPERIMENT,
        sampling_config=ScorerSamplingConfig(sample_rate=sample_rate, filter_string=filter_string)
        if sample_rate
        else None,
        scorer_version=version,
    )


def _activation_harness(monkeypatch, initial, *, readback=None):
    judge_candidates = monitoring.candidates()
    receipt = {
        "definitions_hash": monitoring.definitions_hash(),
        "serialized_definitions_hash": monitoring.serialized_definitions_hash(judge_candidates),
        "passed": {"answer_correctness": True},
    }
    registry = {"answer_correctness": initial}
    registrations = []
    starts = []
    writes = []
    get_calls = 0

    def get_scorer(*, name, experiment_id):
        nonlocal get_calls
        assert name == "answer_correctness"
        assert experiment_id == monitoring.op.EXPERIMENT
        get_calls += 1
        if get_calls > 1 and readback is not None:
            return readback(registry[name])
        return registry[name]

    def register(judge, **_kwargs):
        registrations.append(judge)
        version = (registry.get(judge.name).scorer_version or 0) + 1
        _registered_judge(judge, version=version)
        registry[judge.name] = judge
        return judge

    def start(judge, *, sampling_config, **_kwargs):
        starts.append(judge)
        _registered_judge(
            judge,
            version=judge.scorer_version,
            sample_rate=sampling_config.sample_rate,
            filter_string=sampling_config.filter_string,
        )
        registry[judge.name] = judge
        return judge

    monkeypatch.setattr(monitoring, "candidates", lambda: judge_candidates)
    monkeypatch.setattr(monitoring, "calibration_status", lambda: receipt)
    monkeypatch.setattr(monitoring, "get_scorer", get_scorer)
    judge_type = type(judge_candidates["answer_correctness"])
    monkeypatch.setattr(judge_type, "register", register, raising=False)
    monkeypatch.setattr(judge_type, "start", start, raising=False)
    monkeypatch.setattr(monitoring, "MlflowClient", lambda: Obj(search_traces=lambda **_: None))
    monkeypatch.setattr(monitoring.op, "write_json", lambda *args: writes.append(args))
    return judge_candidates, registrations, starts, writes


def test_activation_reuses_identical_definition_and_is_idempotent(monkeypatch):
    candidate = monitoring.candidates()["answer_correctness"]
    existing = _registered_judge(candidate, version=7, sample_rate=0.5, filter_string="sentinel")
    candidates, registrations, starts, writes = _activation_harness(monkeypatch, existing)

    monitoring.activate(apply=False)
    monitoring.activate(apply=True)

    assert registrations == []
    assert starts == [existing]
    assert writes
    assert candidates["answer_correctness"].model_dump() == existing.model_dump()


@pytest.mark.parametrize(
    "variant",
    ["model", "inference_params", "output_type", "rationale_mode"],
)
def test_activation_registers_when_any_calibrated_setting_differs(monkeypatch, variant):
    candidate = monitoring.candidates()["answer_correctness"]
    config = {
        "name": candidate.name,
        "instructions": candidate.instructions,
        "model": candidate.model,
        "description": candidate.description,
        "feedback_value_type": candidate.feedback_value_type,
        "generate_rationale_first": candidate._generate_rationale_first,
        "inference_params": candidate.inference_params,
    }
    changes = {
        "model": {"model": "gateway:/different-model"},
        "inference_params": {"inference_params": {"temperature": 0.3}},
        "output_type": {"feedback_value_type": str},
        "rationale_mode": {"generate_rationale_first": not candidate._generate_rationale_first},
    }
    config.update(changes[variant])
    existing = monitoring.make_judge(**config)
    existing = _registered_judge(existing, version=4)
    candidates, registrations, starts, _ = _activation_harness(monkeypatch, existing)

    monitoring.activate(apply=True)

    assert len(registrations) == 1
    assert registrations[0].model_dump() == candidates["answer_correctness"].model_dump()
    assert starts == [candidates["answer_correctness"]]


@pytest.mark.parametrize("mismatch", ["definition", "version"])
def test_activation_readback_mismatch_does_not_write_success_receipt(monkeypatch, mismatch):
    candidate = monitoring.candidates()["answer_correctness"]
    existing = _registered_judge(candidate, version=7)

    def bad_readback(current):
        if mismatch == "definition":
            wrong = monitoring.make_judge(
                name=current.name,
                instructions=current.instructions,
                model="gateway:/different-model",
                feedback_value_type=current.feedback_value_type,
            )
            return _registered_judge(
                wrong,
                version=current.scorer_version,
                sample_rate=current.sample_rate,
                filter_string=current.filter_string,
            )
        return _registered_judge(
            monitoring.make_judge(
                name=current.name,
                instructions=current.instructions,
                model=current.model,
                feedback_value_type=current.feedback_value_type,
            ),
            version=current.scorer_version + 1,
            sample_rate=current.sample_rate,
            filter_string=current.filter_string,
        )

    _, _, _, writes = _activation_harness(monkeypatch, existing, readback=bad_readback)

    with pytest.raises(ValueError, match="Activated judge"):
        monitoring.activate(apply=True)

    assert writes == []


def test_activation_preview_does_not_register_start_or_write(monkeypatch):
    existing = _registered_judge(monitoring.candidates()["answer_correctness"], version=3)
    _, registrations, starts, writes = _activation_harness(monkeypatch, existing)

    result = monitoring.activate(apply=False)

    assert result is None
    assert registrations == []
    assert starts == []
    assert writes == []
