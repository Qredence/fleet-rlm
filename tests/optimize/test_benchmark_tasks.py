from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, nullcontext
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


def freeze(tmp_path, count=2, task=None):
    cases = [
        {
            "case_id": f"task-{index}",
            "task": task if task is not None else f"Compute {index}",
            "expected_answer": "secret-gold",
            "question": {"answer": "secret-payload"},
            "files": {"context.txt": "unlabelled input"},
        }
        for index in range(count)
    ]
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, cases, {"dataset": "external/tasks", "dataset_revision": "a" * 40})
    return bundle, tmp_path / "run"


def api(
    monkeypatch,
    *,
    failure=None,
    drift=False,
    unhealthy_after=False,
    tolerated_error=None,
    comparison=None,
    cleanup_status=200,
):
    requests = []
    turns = []
    configs = []
    modes = {}

    def handle(request):
        requests.append(request)
        path = request.url.path
        session = path.split("/")[2] if path.startswith("/sessions/") else None
        route = "rlm" if modes.get(session) == "rlm" else "flex"
        if path.endswith("/mode") and request.method == "PUT":
            modes[session] = json.loads(request.content)["mode"]
            return httpx.Response(200, json={"mode": modes[session]})
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
                    "mode": modes.get(session, "hybrid"),
                    "models": {"root": "configured-model", "sub": "configured-model"},
                    "configuration": {
                        "digest": "changed" if drift and len(configs) > 1 else modes.get(session, "d"),
                        **(
                            {"values": {**comparison, "execution_mode": modes.get(session, "hybrid")}}
                            if comparison
                            else {}
                        ),
                    },
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
                    "payload": {"answer": "42", "route": route, "trace_id": f"trace-{len(turns)}"},
                }
            )
            return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
        if "/turns/" in path:
            return httpx.Response(
                200,
                json={
                    "status": "failed" if failure else "completed",
                    "result": {"answer": "42", "route": route, "trace_id": f"trace-{len(turns)}"},
                },
            )
        if request.method == "DELETE":
            return httpx.Response(cleanup_status, json={})
        if path.endswith("/attach"):
            return httpx.Response(200, json={})
        return pytest.fail(f"Unexpected request: {request.method} {path}")

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


@pytest.mark.parametrize("name", ["longcot_benchmark", "oolong_benchmark"])
@pytest.mark.parametrize("from_notebooks", [False, True])
def test_notebook_bootstrap_resolves_scripts_before_configuration(monkeypatch, name, from_notebooks):
    document = json.loads((ROOT / "notebooks" / f"{name}.ipynb").read_text())
    cells = {cell["id"]: cell for cell in document["cells"]}
    assert list(cells).index("setup") < list(cells).index("configuration")
    monkeypatch.chdir(ROOT / "notebooks" if from_notebooks else ROOT)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    namespace = {}
    exec(compile("".join(cells["setup"]["source"]), name, "exec"), namespace)
    assert namespace["ROOT"] == ROOT.resolve()
    assert sys.path[0] == str(ROOT.resolve() / "scripts")
    configuration = ast.parse("".join(cells["configuration"]["source"]))
    flags = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in configuration.body
        if isinstance(node, ast.Assign) and node.targets[0].id in {"ALLOW_LIVE", "ALLOW_JUDGES"}
    }
    assert flags == {"ALLOW_LIVE": False, "ALLOW_JUDGES": False}


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
        return "native-dspy-judge"

    settings = SimpleNamespace(
        mlflow_target=lambda: SimpleNamespace(
            eval_experiment_name="configured-eval", tracking_uri=descriptor["tracking_uri"]
        ),
        mlflow_descriptor=lambda: descriptor,
        prepare_mlflow_auth=lambda: None,
        judge_environment=lambda: {},
        build_judge_lm=build_judge_lm,
        resolved_model=lambda role: SimpleNamespace(build_adapter=lambda: "judge-adapter") if role == "judge" else None,
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
        scorers,
        "build_mlflow_scorer_suite",
        lambda model, lm, jev=None, judge_adapter=None: [f"native-suite:{model}:{lm}:{jev}:{judge_adapter}"],
    )
    monkeypatch.setattr(mlflow, "start_run", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(mlflow, "set_tags", lambda tags: logged.append(tags))
    monkeypatch.setattr(mlflow, "log_dict", lambda value, path: logged.append((path, value)))
    monkeypatch.setattr(mlflow, "log_input", lambda dataset, **kwargs: inputs.append((dataset, kwargs)))
    monkeypatch.setattr(mlflow.data, "from_pandas", lambda frame, **kwargs: (frame.to_dict("records"), kwargs))

    def evaluate(*, data, scorers):
        assert data == [{"trace": trace, "expectations": {"benchmark_official_score": 1}}]
        expected_lm = "native-dspy-judge"
        assert scorers[0] == f"native-suite:{judge_model}:{expected_lm}:native-jev-lm:judge-adapter"
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


@pytest.mark.parametrize("official_only", [False, True])
def test_official_scores_survive_real_mlflow_cross_experiment_cloning(
    tmp_path, monkeypatch, mlflow_test_uri, official_only
):
    import mlflow

    from fleet_rlm.optimize import scorers

    bundle, run_dir = freeze(tmp_path, task="identical prompt")
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
        judge_environment=lambda: (
            pytest.fail("Official-only scoring must not prepare judge credentials") if official_only else {}
        ),
        build_judge_lm=lambda: None,
        resolved_model=lambda _role: SimpleNamespace(build_adapter=lambda: None),
        build_jev_lm=lambda: None,
        fleet_judge_model="unused",
        mlflow_trace_catalog="",
        mlflow_trace_schema="",
        mlflow_eval_trace_table_prefix="",
        mlflow_tracing_sql_warehouse_id="",
    )

    # Exercise native trace readback, labeling, cloning and scoring without model judges.
    def suite(*_args, **_kwargs):
        if official_only:
            pytest.fail("Official-only scoring must not build model judges")
        return []

    monkeypatch.setattr(scorers, "build_mlflow_scorer_suite", suite)
    result = bench.evaluate_saved_tasks(
        bundle, run_dir, settings, allow_judges=not official_only, official_only=official_only
    )
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
    if official_only:
        assert saved["dataset_id"] and saved["official_only"]
        from mlflow.genai.datasets import search_datasets

        assert len(search_datasets()) == 1
        bench.evaluate_saved_tasks(bundle, run_dir, settings, official_only=True)
        assert len(search_datasets()) == 1  # Re-score receipts, reusing the native frozen dataset.


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


def comparison_settings():
    values = {
        "execution_mode": "hybrid",
        "fleet_root_model": "openai/configured-model",
        "fleet_sub_model": "openai/configured-model",
        "fleet_shared_notes_subpath": "",
        "fleet_root_cache": False,
        "fleet_sub_cache": False,
        "fleet_root_max_tokens": 16384,
        "fleet_sub_max_tokens": None,
    }
    lm = SimpleNamespace(model="configured-model", cache=False, num_retries=3, kwargs={"max_tokens": 16384})
    return values, lm


@pytest.mark.parametrize("arm", ["hybrid", "rlm"])
@pytest.mark.parametrize("deadline", [0, 600])
def test_comparison_mode_deadline_prompt_and_evidence(tmp_path, monkeypatch, arm, deadline):
    values, lm = comparison_settings()
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(
        bundle,
        [{"case_id": "task", "task": "published prompt\nsolution = ...", "question": {"answer": "secret-gold"}}],
        {"dataset": "external/tasks", "dataset_revision": "a" * 40},
    )
    requests, turns = api(monkeypatch, comparison=values)
    run = tmp_path / arm
    bench.run_tasks(
        bundle,
        run,
        base_url="http://fleet",
        expected_destination=DESTINATION,
        allow_live=True,
        arm=arm,
        direct_lm=lm,
        expected_configuration=values,
        timeout_seconds=deadline,
    )
    assert turns == [
        {"task": "published prompt\nsolution = ...", "idempotency_key": "task", "timeout_seconds": deadline}
    ]
    assert "secret-gold" not in b"".join(request.content for request in requests).decode()
    assert json.loads((run / "evidence/task/request.json").read_text()) == turns[0]
    assert "turn_complete" in (run / "evidence/task/response.sse").read_text()
    receipt = json.loads((run / "receipts/task.json").read_text())
    assert receipt["cleanup"] == "released"
    assert receipt["output"]["route"] == ("rlm" if arm == "rlm" else "flex")
    request_count = len(requests)
    with pytest.raises(ValueError, match="different frozen tasks or API"):
        bench.run_tasks(
            bundle,
            run,
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm=arm,
            direct_lm=lm,
            expected_configuration=values,
            timeout_seconds=600 if deadline == 0 else 0,
        )
    assert len(requests) == request_count  # A changed deadline cannot rewrite or replay prior evidence.


@pytest.mark.parametrize("change", ["cache", "notes", "model", "budget"])
def test_comparison_configuration_mismatch_stops_before_paid_call(tmp_path, monkeypatch, change):
    expected, lm = comparison_settings()
    values = dict(expected)
    if change == "cache":
        values["fleet_root_cache"] = True
    elif change == "notes":
        values["fleet_shared_notes_subpath"] = "shared/learned-notes"
    elif change == "model":
        lm.model = "another-model"
    else:
        values["fleet_sub_max_tokens"] = 999
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, [{"case_id": "task", "task": "prompt"}], {"dataset": "x", "dataset_revision": "a" * 40})
    _, turns = api(monkeypatch, comparison=values)
    with pytest.raises(RuntimeError, match="Benchmark stopped"):
        bench.run_tasks(
            bundle,
            tmp_path / "run",
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm="rlm",
            direct_lm=lm,
            expected_configuration=expected,
        )
    assert turns == []
    assert json.loads((tmp_path / "run/receipts/task.json").read_text())["status"] == "preflight_failed"


@pytest.mark.parametrize("missing_usage", [False, True])
@pytest.mark.parametrize("deadline", [0, 600])
def test_direct_uses_native_request_and_response_without_gold(tmp_path, monkeypatch, missing_usage, deadline):
    import anyio
    import mlflow
    from dspy._vendor.lm15.transports import AsyncTransportResponse, StdlibAsyncTransport, Timeouts

    from fleet_rlm.config import FleetSettings

    monkeypatch.setenv("DATABRICKS_TOKEN", "test-only")
    requests = []

    @asynccontextmanager
    async def transport(_self, request):
        assert (anyio.current_effective_deadline() == float("inf")) is (deadline == 0)
        requests.append(request)

        async def chunks():
            yield json.dumps(
                {
                    "id": "provider-response",
                    "model": "reported-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "solution = 42"},
                            "finish_reason": "length",
                        }
                    ],
                    **(
                        {}
                        if missing_usage
                        else {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
                    ),
                }
            ).encode()

        async def release(_consumed):
            pass

        yield AsyncTransportResponse(
            status=200, reason="OK", headers=[], http_version="HTTP/1.1", chunks=chunks(), release=release
        )

    mlflow.set_experiment("direct-benchmark")
    mlflow.dspy.autolog(log_traces=True)
    monkeypatch.setattr(StdlibAsyncTransport, "stream", transport)
    settings = FleetSettings(
        fleet_root_api_base="https://gateway.invalid/ai-gateway/mlflow/v1",
        fleet_root_api_key_env="DATABRICKS_TOKEN",
        databricks_token="test-only",
        fleet_root_model="openai/system.ai.deepseek-v4-1-flash-1017",
        fleet_root_max_tokens=16384,
        fleet_root_temperature=0.2,
        fleet_root_cache=False,
        fleet_root_adapter="json",
        fleet_root_response_format="json_object",
    )
    from fleet_rlm.models import build_lm

    lm = build_lm(settings, settings.fleet_root_model, "root")
    case = {"task": "published prompt", "expected_answer": "secret-gold", "question": {"answer": "secret-payload"}}
    receipt = bench._direct_turn(lm, case, timeout_seconds=deadline, evidence_dir=tmp_path / "evidence")
    assert len(requests) == 1
    wire = json.loads(requests[0].body)
    assert wire["messages"] == [{"role": "user", "content": "published prompt"}]
    assert wire["response_format"] == {"type": "json_object"}
    assert not wire.get("tools")
    assert wire["model"] == "system.ai.deepseek-v4-1-flash-1017"
    assert wire["max_completion_tokens"] == 16384 and wire["temperature"] == 0.2
    assert (
        lm.kwargs.get("timeout") is None and Timeouts().read == 600
    )  # Native SDK idle limit, not a benchmark deadline.
    assert receipt["output"]["answer"] == "solution = 42"
    assert receipt["output"]["finish_reason"] == "length"
    assert receipt["usage"][0]["usage"] == (
        {} if missing_usage else {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    )
    trace = mlflow.get_trace(receipt["output"]["trace_id"])
    assert trace.data.spans[0].inputs == {"task": "published prompt"}
    assert trace.data.spans[0].span_type == "CHAIN"
    assert len([span for span in trace.data.spans if span.span_type in {"LLM", "CHAT_MODEL"}]) == 1
    assert "mlflow.chat.tokenUsage" not in trace.data.spans[0].attributes
    assert "secret-gold" not in trace.to_json() and "secret-payload" not in trace.to_json()
    assert json.loads((tmp_path / "evidence/response.json").read_text())["id"] == "provider-response"
    delivered = bench._benchmark_trace(
        receipt,
        {"source_group": "cs:template"},
        experiment_id=mlflow.get_experiment_by_name("direct-benchmark").experiment_id,
    )
    assert delivered.info.trace_id == trace.info.trace_id


def test_direct_deadline_cancels_native_async_call_and_saves_failure(tmp_path):
    import anyio
    import dspy
    import mlflow

    cancelled = []

    class Engine:
        async def complete(self, _request):
            try:
                await anyio.sleep_forever()
            finally:
                cancelled.append(True)

    mlflow.set_experiment("direct-deadline")
    lm = dspy.LM(
        "test/model", engine=SimpleNamespace(complete=lambda _request: None), async_engine=Engine(), cache=False
    )
    with pytest.raises(TimeoutError):
        bench._direct_turn(lm, {"task": "prompt"}, timeout_seconds=0.01, evidence_dir=tmp_path / "evidence")
    assert cancelled == [True]
    assert json.loads((tmp_path / "evidence/response.json").read_text())["error_type"] == "TimeoutError"


@pytest.mark.parametrize(
    "outcome", ["complete", "rate_limit", "cleanup", "verifier_error", "trace_error", "duplicate_model", "wrong_route"]
)
def test_pilot_is_gated_rotates_arms_and_never_exceeds_ten_slots(tmp_path, monkeypatch, outcome):
    from fleet_rlm import observability
    from fleet_rlm.optimize import artifacts

    bundle = tmp_path / "bundle"
    cases = [
        {"case_id": domain, "task": f"{domain} prompt", "source_group": f"{domain}:template"}
        for domain in ("logic", "cs", "chemistry", "chess", "math")
    ]
    bench.freeze_tasks(bundle, cases, {"dataset": "x", "dataset_revision": "a" * 40, "hf_pin_verified": True})
    values, lm = comparison_settings()
    settings = SimpleNamespace(
        fleet_root_model="root-model",
        fleet_sub_model="sub-model",
        mlflow_target=lambda: SimpleNamespace(mode="local", experiment_name="runtime", tracking_uri="http://tracking"),
        mlflow_descriptor=lambda: DESTINATION,
        configuration=lambda: {"digest": "settings", "values": values},
        mlflow_trace_catalog="",
        mlflow_trace_schema="",
        mlflow_trace_table_prefix="",
        mlflow_tracing_sql_warehouse_id="",
    )
    monkeypatch.setattr(bench, "build_lm", lambda _settings, _model_id, _role: lm, raising=False)
    slots = []
    direct_calls = []
    requests, turns = api(
        monkeypatch,
        comparison=values,
        failure="LMRateLimitError" if outcome == "rate_limit" else None,
        cleanup_status=409 if outcome == "cleanup" else 200,
    )
    original_run = bench.run_tasks

    def record_slot(*args, **kwargs):
        assert kwargs["timeout"] is None and kwargs["timeout_seconds"] == 0
        slots.append((kwargs["case_ids"], kwargs["arm"]))
        return original_run(*args, **kwargs)

    def direct(_lm, case, **_kwargs):
        assert _kwargs["timeout_seconds"] == 0
        direct_calls.append(case["task"])
        return {
            "status": "completed",
            "output": {"answer": "42", "route": "direct", "trace_id": "direct-trace"},
            "usage": [{"model": "configured-model", "usage": {"input_tokens": 3}, "finish_reason": "length"}],
        }

    def verify(_case, _answer):
        if outcome == "verifier_error":
            raise ValueError("offline verifier failed")
        return 0  # A wrong answer is still a valid completed observation.

    def trace(_receipt, _case, **_kwargs):
        if outcome == "trace_error":
            raise RuntimeError("required trace unavailable")
        if outcome == "wrong_route":
            _receipt["output"]["route"] = "flex"
        return SimpleNamespace(
            data=SimpleNamespace(
                spans=[SimpleNamespace(span_type="CHAT_MODEL") for _ in range(2 if outcome == "duplicate_model" else 1)]
            )
        )

    monkeypatch.setattr(bench, "run_tasks", record_slot)
    monkeypatch.setattr(bench, "_direct_turn", direct)
    monkeypatch.setattr(bench, "version", lambda _name: "test")
    monkeypatch.setattr(observability, "configure_mlflow_observability", lambda *_args, **_kwargs: "runtime")
    monkeypatch.setattr(artifacts, "optimization_code_digest", lambda: "code")
    monkeypatch.setattr(bench, "_benchmark_trace", trace)
    with pytest.raises(ValueError, match="allow_live"):
        bench.run_comparison_pilot(bundle, tmp_path / "run", settings, base_url="http://fleet")
    if outcome != "complete":
        for _ in range(2):
            with pytest.raises(RuntimeError):
                bench.run_comparison_pilot(
                    bundle, tmp_path / "run", settings, base_url="http://fleet", allow_live=True, official_score=verify
                )
        assert len(direct_calls) == (1 if outcome in {"verifier_error", "duplicate_model"} else 0)
        assert len(turns) == 1
        assert not (tmp_path / "run/hybrid").exists()
        scores = bench.score_comparison_pilot(bundle, tmp_path / "run", lambda *_args: 1)
        assert not scores["arms"]["rlm"]["complete"]
        return
    run = tmp_path / "run"
    report = bench.run_comparison_pilot(
        bundle, run, settings, base_url="http://fleet", allow_live=True, official_score=verify
    )
    assert report["status"] == "finished" and len(slots) == 10
    assert [arm for _, arm in slots[:6]] == ["rlm", "direct", "direct", "rlm", "rlm", "direct"]
    assert [case_ids for case_ids, _arm in slots[::2]] == [["cs"], ["chemistry"], ["chess"], ["math"], ["logic"]]
    assert Counter(arm for _, arm in slots) == {"direct": 5, "rlm": 5}
    assert report["max_task_attempts"] == 10 and report["timeout_seconds"] == 0
    assert report["native_transport_timeouts"]["read"] == 600
    assert len(turns) == 5 and len(direct_calls) == 5
    assert set(json.loads((run / "first-pair.json").read_text())) == {"direct", "rlm"}
    assert all(turn["timeout_seconds"] == 0 for turn in turns)
    assert all(
        request.extensions["timeout"]["read"] is None for request in requests if request.url.path.endswith("/turn")
    )
    assert len({request.url.path for request in requests if request.method == "DELETE"}) == 10
    request_count = len(requests)
    bench.run_comparison_pilot(bundle, run, settings, base_url="http://fleet", allow_live=True, official_score=verify)
    assert len(requests) == request_count and len(direct_calls) == 5  # No paid replay.
    scores = bench.score_comparison_pilot(bundle, run, lambda *_args: 1)
    assert all(report["correct"] == 5 and report["complete"] for report in scores["arms"].values())
    assert scores["paired_vs_direct"] == {"rlm": {"ties": 5}}
    assert scores["arms"]["direct"]["truncated_cases"] == 5
    assert scores["arms"]["rlm"]["cases_with_unknown_truncation"] == 5


def test_pilot_requires_hf_verification_before_service_access(tmp_path):
    bundle, run = freeze(tmp_path)
    with pytest.raises(ValueError, match="Hugging Face MCP"):
        bench.run_comparison_pilot(bundle, run, SimpleNamespace(), base_url="http://fleet", allow_live=True)
    assert not run.exists()


def test_direct_postflight_failure_preserves_the_completed_answer_and_trace(tmp_path, monkeypatch):
    values, lm = comparison_settings()
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, [{"case_id": "task", "task": "prompt"}], {"dataset": "x", "dataset_revision": "a" * 40})
    api(monkeypatch, comparison=values)
    original_health = bench._health
    health_calls = []

    def health(client):
        health_calls.append(True)
        if len(health_calls) == 2:
            raise RuntimeError("postflight unavailable")
        return original_health(client)

    output = {"answer": "saved answer", "trace_id": "saved-trace", "route": "direct"}

    def direct(_lm, _case, *, evidence_dir, **_kwargs):
        bench.save_json(evidence_dir / "response.json", {"id": "provider-response"})
        return {"status": "completed", "output": output, "usage": []}

    monkeypatch.setattr(bench, "_health", health)
    monkeypatch.setattr(bench, "_direct_turn", direct)
    run = tmp_path / "run"
    with pytest.raises(RuntimeError, match="Benchmark stopped"):
        bench.run_tasks(
            bundle,
            run,
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm="direct",
            direct_lm=lm,
            expected_configuration=values,
        )
    receipt = json.loads((run / "receipts/task.json").read_text())
    assert receipt["status"] == "completed" and receipt["output"] == output
    assert receipt["stop_reason"] == "transport_or_service_failure" and receipt["cleanup"] == "released"


def test_partial_stream_keeps_evidence_and_requests_disconnect_cleanup(tmp_path, monkeypatch):
    values, lm = comparison_settings()
    bundle = tmp_path / "bundle"
    bench.freeze_tasks(bundle, [{"case_id": "task", "task": "prompt"}], {"dataset": "x", "dataset_revision": "a" * 40})
    requests, turns = api(monkeypatch, comparison=values)
    make_client = bench.httpx.Client

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"event":"turn_status","turn_id":"turn-interrupted"}\n\n'
            raise httpx.ReadTimeout("stream interrupted")

    def client(**kwargs):
        instance = make_client(**kwargs)
        stream = instance.stream

        def interrupted(method, url, **options):
            if url.endswith("/turn"):
                return nullcontext(
                    httpx.Response(200, request=httpx.Request(method, "http://fleet" + url), stream=BrokenStream())
                )
            return stream(method, url, **options)

        instance.stream = interrupted
        return instance

    monkeypatch.setattr(bench.httpx, "Client", client)
    run = tmp_path / "run"
    for _ in range(2):
        with pytest.raises(RuntimeError):
            bench.run_tasks(
                bundle,
                run,
                base_url="http://fleet",
                expected_destination=DESTINATION,
                allow_live=True,
                arm="rlm",
                direct_lm=lm,
                expected_configuration=values,
            )
    receipt = json.loads((run / "receipts/task.json").read_text())
    assert receipt["status"] == "uncertain" and receipt["cancellation"] == "stream_closed"
    assert receipt["turn_id"] == "turn-interrupted" and receipt["cleanup"] == "released"
    assert "turn-interrupted" in (run / "evidence/task/response.sse").read_text()
    assert any(request.method == "DELETE" for request in requests)
    assert turns == []  # No replacement of the interrupted request.


def test_comparison_requires_expected_configuration_before_any_request(tmp_path, monkeypatch):
    _, lm = comparison_settings()
    monkeypatch.setattr(bench, "load_tasks", lambda *_args: pytest.fail("must reject before loading tasks"))
    with pytest.raises(ValueError, match="comparison arm requires"):
        bench.run_tasks(
            tmp_path / "bundle",
            tmp_path / "run",
            base_url="http://fleet",
            expected_destination=DESTINATION,
            allow_live=True,
            arm="hybrid",
            direct_lm=lm,
            expected_configuration=None,
        )
