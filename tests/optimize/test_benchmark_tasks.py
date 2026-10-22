from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

DESTINATION = {"mode": "local", "tracking_uri": "http://tracking", "experiment_name": "runtime"}
ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("benchmark_tasks", ROOT / "scripts/benchmark_tasks.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def freeze(tmp_path, count=2):
    cases = [
        {
            "case_id": f"task-{index}",
            "task": f"Compute {index}",
            "expected_answer": "secret-gold",
            "question": {"answer": "secret-payload"},
            "files": {"context.txt": "unlabelled input"},
        }
        for index in range(count)
    ]
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, cases, {"dataset": "external/tasks", "dataset_revision": "a" * 40})
    return bundle, tmp_path / "run"


def api(monkeypatch, *, failure=None, drift=False, unhealthy_after=False, tolerated_error=None):
    requests = []
    turns = []
    configs = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "failed" if unhealthy_after and turns else "ok",
                    "mlflow": {
                        "ready": True,
                        "mode": "local",
                        "experiment_name": "runtime",
                        "experiment_id": "runtime",
                        "tracking_uri": "http://tracking",
                    },
                },
            )
        if path.endswith("/config"):
            configs.append(path)
            return httpx.Response(
                200,
                json={
                    "mode": "hybrid",
                    "models": {"root": "configured-model"},
                    "configuration": {"digest": "changed" if drift and len(configs) > 1 else "d"},
                },
            )
        if path.endswith("/turn"):
            turns.append(json.loads(request.content))
            events = []
            if tolerated_error:
                events.append(
                    {
                        "event": "lm_end",
                        "model": "provider-model",
                        "ok": False,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "error_type": tolerated_error,
                    }
                )
            events.append(
                {
                    "event": "lm_end",
                    "model": "provider-model",
                    "ok": not failure,
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                    "error_type": failure,
                }
            )
            events.append(
                {
                    "event": "error" if failure else "turn_complete",
                    "turn_id": "turn-1",
                    "payload": {"answer": "42", "route": "flex", "trace_id": f"trace-{len(turns)}"},
                }
            )
            return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
        if "/turns/" in path:
            return httpx.Response(
                200,
                json={
                    "status": "failed" if failure else "completed",
                    "result": {"answer": "42", "route": "flex", "trace_id": f"trace-{len(turns)}"},
                },
            )
        if path.endswith("/attach") or request.method == "DELETE":
            return httpx.Response(200, json={})
        pytest.fail(f"Unexpected request: {request.method} {path}")

    original = httpx.Client
    monkeypatch.setattr(
        bench.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    return requests, turns


def test_bundle_is_immutable_and_requires_pinned_source(tmp_path):
    bundle, _ = freeze(tmp_path)
    loaded = bench.load_tasks(bundle)
    with pytest.raises(FileExistsError):
        bench.freeze_tasks(bundle, loaded["cases"], loaded["metadata"])
    loaded["cases"][0]["task"] = "modified"
    bench.save_json(bundle / "bundle.json", loaded)
    with pytest.raises(ValueError, match="changed"):
        bench.load_tasks(bundle)
    with pytest.raises(ValueError, match="commit SHA"):
        bench.freeze_tasks(tmp_path / "other", [{"case_id": "x", "task": "x"}], {"dataset_revision": "main"})
    with pytest.raises(ValueError, match="name the dataset"):
        bench.freeze_tasks(tmp_path / "other2", [{"case_id": "x", "task": "x"}], {"dataset_revision": "a" * 40})


def test_runs_normal_api_without_gold_or_overrides_and_reuses_completed_receipts(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    requests, turns = api(monkeypatch)
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert turns == [
        {"task": "Compute 0", "idempotency_key": "task-0"},
        {"task": "Compute 1", "idempotency_key": "task-1"},
    ]
    assert all("secret" not in request.content.decode() for request in requests)
    assert all(request.method not in {"PATCH", "PUT"} for request in requests)
    receipt = json.loads((run / "receipts/task-0.json").read_text())
    assert receipt["configuration"]["mode"] == "hybrid"
    assert receipt["configuration_digest"] == "d"
    assert receipt["status"] == "completed" and receipt["durable_outcome"]["status"] == "completed"
    assert receipt["output"]["trace_id"] == "trace-1" and receipt["cleanup"] == "released"
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 2
    scores = bench.score_tasks(bundle, run, lambda _case, answer: float(answer == "42"))
    assert scores["complete"] and scores["completed_case_accuracy"] == 1
    assert scores["configuration_digest"] == "d"
    assert scores["reported_provider_usage"]["provider-model"]["input_tokens"] == 20
    assert scores["cases"][0]["trace_url"].startswith("http://tracking/#/experiments/runtime/traces")


@pytest.mark.parametrize("failure", ["LMRateLimitError", "ServiceUnavailableError"])
def test_provider_stop_prevents_subsequent_tasks_and_replay(tmp_path, monkeypatch, failure):
    bundle, run = freeze(tmp_path)
    _, turns = api(monkeypatch, failure=failure)
    with pytest.raises(RuntimeError, match="stopped"):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    with pytest.raises(RuntimeError, match="never replay"):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 1
    scores = bench.score_tasks(bundle, run, lambda *_args: pytest.fail("failed turns must not be scored"))
    assert scores["status_counts"] == {"failed": 1, "unattempted": 1}
    assert not scores["complete"] and scores["all_selected_case_accuracy"] == 0


def test_tolerated_lm_failure_in_completed_turn_does_not_stop_the_run(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    _, turns = api(monkeypatch, tolerated_error="ServiceUnavailableError")
    result = bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert result["status"] == "finished" and len(turns) == 2
    for case_id in ("task-0", "task-1"):
        receipt = json.loads((run / "receipts" / f"{case_id}.json").read_text())
        assert receipt["status"] == "completed" and "stop_reason" not in receipt
        assert receipt["cleanup"] == "released"
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 2


def test_unknown_outcome_receipt_is_written_before_post_and_never_replayed(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    original = bench._turn
    calls = []
    api(monkeypatch)

    def timeout(_client, _session, case):
        calls.append(case["case_id"])
        receipt = json.loads((run / "receipts/task-0.json").read_text())
        assert receipt["status"] == "uncertain"
        raise httpx.ReadTimeout("outcome unknown")

    monkeypatch.setattr(bench, "_turn", timeout)
    with pytest.raises(RuntimeError):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    monkeypatch.setattr(bench, "_turn", original)
    with pytest.raises(RuntimeError, match="never replay"):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert calls == ["task-0"]
    assert json.loads((run / "receipts/task-0.json").read_text())["status"] == "uncertain"


@pytest.mark.parametrize("option", ["drift", "unhealthy_after"])
def test_config_drift_or_post_turn_service_failure_stops_measurement(tmp_path, monkeypatch, option):
    bundle, run = freeze(tmp_path)
    _, turns = api(monkeypatch, **{option: True})
    with pytest.raises(RuntimeError):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 1
    assert not bench.score_tasks(bundle, run, lambda *_args: 1)["complete"]
    with pytest.raises(RuntimeError):
        bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    assert len(turns) == 1


def test_offline_scoring_keeps_wrong_answers_verifier_errors_and_missing_usage(tmp_path, monkeypatch):
    bundle, run = freeze(tmp_path)
    api(monkeypatch)
    bench.run_tasks(bundle, run, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)

    def verifier(case, _answer):
        if case["case_id"] == "task-1":
            raise ValueError("invalid gold")
        return 0

    score = bench.score_tasks(bundle, run, verifier)
    assert score["completed"] == 2 and score["scored"] == 1 and score["score_errors"] == 1
    assert score["completed_case_accuracy"] is None and score["mean_official_score_scored"] == 0
    assert not score["complete"]


def notebook_functions(name, namespace):
    document = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text())
    for cell in document["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None and not cell["outputs"]
            compile("".join(cell["source"]), name, "exec")
        if cell["id"] == "definitions":
            tree = ast.parse("".join(cell["source"]))
            tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
            exec(compile(tree, name, "exec"), namespace)
    return namespace


def test_longcot_hf_join_preserves_all_domains_and_requires_prompt_identity():
    @dataclass
    class Question:
        question_id: str
        domain: str
        difficulty: str
        prompt: str
        problem: dict
        answer: str | None = None

    domains = ("logic", "cs", "chemistry", "chess", "math")
    namespace = notebook_functions(
        "longcot_benchmark",
        {
            "hashlib": hashlib,
            "json": json,
            "asdict": asdict,
            "DOMAINS": domains,
            "VERIFIER_COMMIT": "fb9649423f15f5b0091f8e988b100596cac592ca",
        },
    )
    rows = [
        {
            "question_id": domain,
            "domain": domain,
            "difficulty": "easy",
            "prompt": "published prompt",
            "template": "template",
        }
        for domain in domains
    ]
    canonical = {
        row["question_id"]: Question(
            row["question_id"], row["domain"], "easy", row["prompt"], {"template": "template", "hidden_state": [1, 2]}
        )
        for row in rows
    }
    selected = namespace["select_longcot"](rows, per_domain=1)
    assert selected == namespace["select_longcot"](list(reversed(rows)), per_domain=1)
    for row in selected:
        case = namespace["longcot_task"](row, canonical)
        assert case["task"] == row["prompt"]
        assert case["question"]["problem"]["hidden_state"] == [1, 2]
    with pytest.raises(ValueError, match="differs"):
        namespace["longcot_task"]({**rows[0], "prompt": "altered"}, canonical)
    namespace["distribution"] = lambda _name: SimpleNamespace(
        read_text=lambda _path: json.dumps({"vcs_info": {"commit_id": "installed-commit"}})
    )
    namespace["require_verifier"]("installed-commit")
    with pytest.raises(ValueError, match="required commit"):
        namespace["require_verifier"]("frozen-commit")
    seen, checked = [], []
    namespace.update(
        bundle={"metadata": {"verifier_commit": "frozen-commit"}},
        require_verifier=lambda expected: checked.append(expected),
        longcot=SimpleNamespace(
            Question=lambda **kwargs: kwargs,
            VerifyOptions=lambda **kwargs: kwargs,
            MathVerifyOptions=lambda **kwargs: kwargs,
            ChemistryVerifyOptions=lambda **kwargs: kwargs,
            verify=lambda _question, answer, options: seen.append(options) or answer == "yes",
        ),
    )
    assert namespace["official_score"]({"question": {}}, "yes") == 1
    assert checked == ["frozen-commit"]
    assert seen == [{"math": {"enable_fallback": False}, "chemistry": {"enable_fallback": False}}]


def test_oolong_selection_covers_groups_without_gold_or_labelled_context():
    namespace = notebook_functions(
        "oolong_benchmark", {"hashlib": hashlib, "Counter": Counter, "defaultdict": defaultdict}
    )
    rows = [
        {"id": index, "task_group": str(index % 3), "context_len": 1024, "context_window_id": index // 3}
        for index in range(30)
    ]
    chosen = namespace["select_oolong"](rows)
    assert chosen == namespace["select_oolong"](reversed(rows))
    assert len(chosen) == 12 and len({row["task_group"] for row in chosen}) == 3
    assert max(Counter(row["context_window_id"] for row in chosen).values()) <= 2
    case = namespace["oolong_task"](
        {
            **rows[0],
            "question": "count?",
            "context_window_text": "safe context",
            "context_window_text_with_labels": "leaked gold",
            "answer": "['gold']",
            "dataset": "test",
            "answer_type": "ANSWER_TYPE.LABEL",
        }
    )
    assert "gold" not in case["task"] and case["files"] == {"context.txt": "safe context"}


def test_live_and_judge_gates_precede_all_io(tmp_path):
    with pytest.raises(ValueError, match="allow_live"):
        bench.run_tasks(tmp_path, tmp_path, base_url="http://invalid", expected_destination=DESTINATION)
    with pytest.raises(ValueError, match="allow_judges"):
        bench.evaluate_saved_tasks(tmp_path, tmp_path, None)
    for descriptor in ({}, {"mode": "local", "tracking_uri": "http://tracking"}):
        with pytest.raises(ValueError, match="Expected destination"):
            bench.run_tasks(
                tmp_path, tmp_path, base_url="http://invalid", allow_live=True, expected_destination=descriptor
            )


def test_tracking_profile_mismatch_prevents_runtime_creation_and_paid_calls(tmp_path, monkeypatch):
    bundle, run_dir = freeze(tmp_path)
    requests, turns = api(monkeypatch)
    with pytest.raises(RuntimeError, match="stopped"):
        bench.run_tasks(
            bundle,
            run_dir,
            base_url="http://fleet",
            allow_live=True,
            expected_destination={"mode": "databricks", "tracking_uri": "databricks", "experiment_name": "runtime"},
        )
    assert [request.url.path for request in requests] == ["/health"]
    assert not turns
    receipt = json.loads((run_dir / "receipts/task-0.json").read_text())
    assert receipt["status"] == "preflight_failed" and "destination differs" in receipt["error"]


@pytest.mark.parametrize("backend", ["local", "databricks"])
@pytest.mark.parametrize(
    "judge_model", ["openai:/configured-judge", "databricks:/configured-judge", "anthropic:/judge"]
)
@pytest.mark.parametrize("scoring_error", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_evaluates_saved_traces_in_configured_experiment_without_turns(
    tmp_path, monkeypatch, backend, judge_model, scoring_error, partial
):
    import mlflow
    import pandas as pd

    from fleet_rlm import observability
    from fleet_rlm.optimize import evaluation, scorers

    bundle, run_dir = freeze(tmp_path, count=2 if partial else 1)
    _, turns = api(monkeypatch)
    bench.run_tasks(bundle, run_dir, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)

    def verifier(case, _answer):
        if partial and case["case_id"] == "task-1":
            raise ValueError("verifier unavailable")
        return 1

    bench.score_tasks(bundle, run_dir, verifier)
    descriptor = {
        "mode": backend,
        "tracking_uri": "databricks" if backend == "databricks" else "http://tracking",
        "trace_location": {"catalog": "catalog", "schema": "runtime", "table_prefix": "traces"}
        if backend == "databricks"
        else None,
    }
    run = json.loads((run_dir / "run.json").read_text())
    run["service"]["mlflow"].update(descriptor)
    bench.save_json(run_dir / "run.json", run)
    configured, reads, labels, inputs, logged = [], [], [], [], []

    def build_judge_lm():
        assert judge_model.startswith("openai:/")
        return "native-dspy-judge"

    settings = SimpleNamespace(
        mlflow_target=lambda: SimpleNamespace(
            eval_experiment_name="configured-eval", tracking_uri=descriptor["tracking_uri"]
        ),
        mlflow_descriptor=lambda: descriptor,
        prepare_mlflow_auth=lambda: None,
        judge_environment=lambda: {},
        build_judge_lm=build_judge_lm,
        build_jev_lm=lambda: "native-jev-lm",
        fleet_judge_model=judge_model,
        mlflow_trace_catalog="catalog",
        mlflow_trace_schema="runtime",
        mlflow_eval_trace_table_prefix="evaluations",
        mlflow_tracing_sql_warehouse_id="warehouse",
    )
    trace = SimpleNamespace(info=SimpleNamespace(trace_id="trace-1"))
    monkeypatch.setattr(
        observability, "configure_mlflow_observability", lambda *args, **kwargs: configured.append((args, kwargs))
    )
    monkeypatch.setattr(
        evaluation, "read_evaluation_trace", lambda identity, **kwargs: reads.append((identity, kwargs)) or trace
    )
    monkeypatch.setattr(evaluation, "label_trace", lambda identity, **kwargs: labels.append((identity, kwargs)))
    monkeypatch.setattr(
        scorers, "build_mlflow_scorer_suite", lambda model, lm, jev=None: [f"native-suite:{model}:{lm}:{jev}"]
    )
    monkeypatch.setattr(mlflow, "start_run", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(mlflow, "set_tags", lambda tags: logged.append(tags))
    monkeypatch.setattr(mlflow, "log_dict", lambda value, path: logged.append((path, value)))
    monkeypatch.setattr(mlflow, "log_input", lambda dataset, **kwargs: inputs.append((dataset, kwargs)))
    monkeypatch.setattr(mlflow.data, "from_pandas", lambda frame, **kwargs: (frame.to_dict("records"), kwargs))

    def evaluate(*, data, scorers):
        assert data == [{"trace": trace, "expectations": {"benchmark_official_score": 1}}]
        expected_lm = "native-dspy-judge" if judge_model.startswith("openai:/") else None
        assert scorers[0] == f"native-suite:{judge_model}:{expected_lm}:native-jev-lm"
        assert scorers[-1](expectations=data[0]["expectations"]).value == 1
        frame = pd.DataFrame({"benchmark_correctness/value": [1]})
        if scoring_error:
            frame["fleet_metric/error_message"] = ["judge failure"]
        return SimpleNamespace(run_id="saved-eval", metrics={"benchmark_correctness/mean": 1}, result_df=frame)

    monkeypatch.setattr(mlflow.genai, "evaluate", evaluate)
    if scoring_error:
        with pytest.raises(RuntimeError, match="Evaluation incomplete"):
            bench.evaluate_saved_tasks(bundle, run_dir, settings, allow_judges=True)
    else:
        assert bench.evaluate_saved_tasks(bundle, run_dir, settings, allow_judges=True).run_id == "saved-eval"
    assert len(turns) == (2 if partial else 1)  # Evaluation never sends another Turn.
    assert configured[0][0] == ("configured-eval", descriptor["tracking_uri"])
    assert configured[0][1]["trace_table_prefix"] == "evaluations"
    assert reads[-1][1]["expected_labels"]["review_status"] == "benchmark"
    assert reads[-1][1]["expected_labels"]["expected_answer"] == "secret-gold"
    assert reads[-1][1]["uc_location"] == (("catalog", "runtime", "traces") if backend == "databricks" else None)
    assert labels[0][1]["review_status"] == "benchmark"
    assert inputs[0][0][0] == [
        {"case_id": case["case_id"], "task": case["task"]} for case in bench.load_tasks(bundle)["cases"]
    ]
    assert inputs[0][1] == {"context": "benchmark"}
    saved = json.loads((run_dir / "evaluation.json").read_text())
    assert saved["run_id"] == "saved-eval"
    assert saved["complete"] is (not scoring_error and not partial)
    assert saved["scoring_complete"] is not scoring_error
    assert (saved["expected"], saved["completed"], saved["scored"]) == ((2, 2, 1) if partial else (1, 1, 1))
    assert logged[0]["benchmark.expected"] == str(saved["expected"])


def test_official_scores_survive_real_mlflow_cross_experiment_cloning(tmp_path, monkeypatch, mlflow_test_uri):
    import mlflow

    from fleet_rlm.optimize import scorers

    bundle, run_dir = freeze(tmp_path)
    with monkeypatch.context() as api_patch:
        api(api_patch)
        bench.run_tasks(bundle, run_dir, base_url="http://fleet", expected_destination=DESTINATION, allow_live=True)
    source = mlflow.set_experiment("benchmark-serving")
    source_trace_ids = set()
    expected_scores = {"task-0": 0.75, "task-1": 0.0}
    for case_id in expected_scores:
        with mlflow.start_span(name=case_id) as span:
            span.set_inputs({"task": case_id})
            span.set_outputs({"answer": "42"})
        trace = mlflow.get_trace(span.trace_id, flush=True)
        source_trace_ids.add(trace.info.trace_id)
        receipt_path = run_dir / "receipts" / f"{case_id}.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["output"]["trace_id"] = trace.info.trace_id
        bench.save_json(receipt_path, receipt)
    descriptor = {"mode": "local", "tracking_uri": mlflow_test_uri, "experiment_name": source.name}
    run = json.loads((run_dir / "run.json").read_text())
    run["service"]["mlflow"].update(descriptor, experiment_id=source.experiment_id)
    bench.save_json(run_dir / "run.json", run)
    bench.score_tasks(bundle, run_dir, lambda case, _answer: expected_scores[case["case_id"]])
    settings = SimpleNamespace(
        mlflow_target=lambda: SimpleNamespace(
            eval_experiment_name="benchmark-evaluation", tracking_uri=mlflow_test_uri
        ),
        mlflow_descriptor=lambda: descriptor,
        prepare_mlflow_auth=lambda: None,
        judge_environment=lambda: {},
        build_judge_lm=lambda: None,
        build_jev_lm=lambda: None,
        fleet_judge_model="unused",
        mlflow_trace_catalog="",
        mlflow_trace_schema="",
        mlflow_eval_trace_table_prefix="",
        mlflow_tracing_sql_warehouse_id="",
    )
    # Exercise native trace readback, labeling, cloning and scoring without model judges.
    monkeypatch.setattr(scorers, "build_mlflow_scorer_suite", lambda *_args: [])
    result = bench.evaluate_saved_tasks(bundle, run_dir, settings, allow_judges=True)
    frame = result.result_df
    cloned_traces = [mlflow.entities.Trace.from_json(value) for value in frame["trace"]]
    assert {trace.info.trace_id for trace in cloned_traces}.isdisjoint(source_trace_ids)
    assert {
        trace.data.spans[0].name: value
        for trace, value in zip(cloned_traces, frame["benchmark_correctness/value"], strict=True)
    } == expected_scores
    assert not frame.filter(regex="error_message$").notna().any().any()
    saved = json.loads((run_dir / "evaluation.json").read_text())
    assert saved["complete"] and saved["scoring_complete"]


def test_evaluation_refuses_a_run_without_recorded_destination(tmp_path):
    bundle, run_dir = freeze(tmp_path, count=1)
    digest = bench.load_tasks(bundle)["digest"]
    bench.save_json(run_dir / "run.json", {"bundle_digest": digest, "run_id": "r", "status": "incomplete"})
    bench.save_json(
        run_dir / "scores.json",
        {"bundle_digest": digest, "complete": False, "expected": 1, "completed": 0, "scored": 0, "cases": []},
    )
    with pytest.raises(ValueError, match="recorded Fleet destination"):
        bench.evaluate_saved_tasks(bundle, run_dir, SimpleNamespace(), allow_judges=True)


def test_oolong_official_parser_retains_numeric_partial_credit():
    spec = importlib.util.spec_from_file_location("oolong_scoring", ROOT / "scripts/oolong_scoring.py")
    official = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(official)
    case = {
        "id": 1,
        "context_window_id": 1,
        "dataset": "test",
        "answer": "['12']",
        "answer_type": "ANSWER_TYPE.NUMERIC",
    }
    assert official.synth_process_response(case, "Answer: 12", "fleet")["score"] == 1
    assert official.synth_process_response(case, "Answer: 13", "fleet")["score"] == 0.75
