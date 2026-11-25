"""Replay reviewed Fleet QA traces with dspy.Evaluate and log per-case evidence to MLflow."""

import argparse
import json
import re
import subprocess
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import dspy
import mlflow
import mlflow.dspy
from fleet_rlm import agent
from fleet_rlm.config import Settings
from fleet_rlm.optimize import _ephemeral_daytona_interpreter
from mlflow import MlflowClient
from mlflow.entities import Span, Trace


@dataclass(frozen=True)
class Case:
    id: str
    lineage: str
    source_trace: str
    answer: str | None
    needs_workspace: bool = False


# Ground truth comes from the test instructions and fixtures. Historical answers
# are deliberately never read by the dataset loader.
CASES = (
    Case(
        "memory-pending",
        "tamarack",
        "tr-b26420d7d06880f5377646d1e4e67bcc",
        "NO SAVED HANDOFF FORMAT",
    ),
    Case(
        "memory-approved",
        "tamarack",
        "tr-8c181126bc3f93609543d2e6aa1d9c6d",
        "TAMARACK-62415 | Avery | READY",
    ),
    Case(
        "birch-valid", "birch", "tr-76ad2a407524d34dbc89fa97198145af", "BIRCH-8062:73"
    ),
    Case(
        "birch-invalid", "birch", "tr-76ad2a407524d34dbc89fa97198145af", "BIRCH INVALID"
    ),
    Case(
        "quartz-valid",
        "quartz",
        "tr-84e4585d9c854df41cc9200c013a8279",
        "QUARTZ-4071\nOWNER=Rowan\nSTATE=BLUE\nSUM=151",
    ),
    Case(
        "quartz-invalid",
        "quartz",
        "tr-3f5e07323c36fe66accb9ab41f1c7ef6",
        "INVALID QUARTZ INPUT",
    ),
    Case("writing", "status-update", "tr-27327f2cc9c6f434eae3a03068b7272a", None),
    Case(
        "missing",
        "missing-ledger",
        "tr-c2113a95c70e36d501d568d26be4f56d",
        "MISSING: no-such-ledger-qa-80417.csv",
        True,
    ),
    Case(
        "history",
        "fennel-witness",
        "tr-683891fde76f75107cf732b245564277",
        "FENNEL-9721-GRAY | WREN-1674-JADE",
        True,
    ),
    Case(
        "isolation",
        "fennel-witness",
        "tr-ba63f9247ea93e4c623cd1d8065773ad",
        "NO SESSION MARKER | NO SESSION FILE",
        True,
    ),
)
INPUTS = ("task", "files_manifest", "memories", "active_skills", "prior_turns")
WRITING_FACTS = (
    r"search (?:is|remains) (?:currently )?(?:running )?slow",
    r"writes? (?:are|remain) unaffected",
    r"(?:the |our )?next update (?:is|will be|will follow) at 14:00 UTC",
    r"we (?:will|’ll|'ll) (?:provide|share|post|give) (?:the |our )?next update at 14:00 UTC",
)


def answer_passes(answer: str, expected: str | None) -> bool:
    answer = answer.strip()
    if expected is not None:
        return answer == expected
    sentences = re.findall(r"[^.!?]+[.!?]", answer)
    if (
        len(sentences) != 2
        or "".join(sentences) != answer
        or "\n" in answer
        or len(answer.split()) > 28
    ):
        return False
    remaining = answer
    for pattern in WRITING_FACTS:
        remaining = re.sub(pattern, "", remaining, flags=re.IGNORECASE)
    return (
        bool(re.search(WRITING_FACTS[0], answer, re.IGNORECASE))
        and bool(re.search(WRITING_FACTS[1], answer, re.IGNORECASE))
        and any(
            re.search(pattern, answer, re.IGNORECASE) for pattern in WRITING_FACTS[2:]
        )
        and not re.sub(
            r"[\s,.!?;]+|\b(?:but|and|while)\b", "", remaining, flags=re.IGNORECASE
        )
    )


def workspace_trace_root(trace: Trace) -> Span | None:
    run_id = trace.info.tags.get("fleet.run_id")
    return next(
        (
            span
            for span in trace.data.spans
            if (
                span.name == "fleet.rlm.run"
                and span.attributes.get("fleet.depth") == 0
                and span.attributes.get("fleet.run_id") == run_id
            )
            or (
                span.parent_id is None and span.name in {"RLM.forward", "fleet.rlm.run"}
            )
        ),
        None,
    )


def _root_forward(trace: Trace, root: Span) -> Span | None:
    if root.name == "RLM.forward":
        return root
    return next(
        (
            s
            for s in trace.data.spans
            if s.parent_id == root.span_id and s.name == "RLM.forward"
        ),
        None,
    )


def workspace_trace_usage(trace: Trace, root: Span) -> tuple[dict | None, int]:
    if root.parent_id is None:
        return trace.info.token_usage, sum(
            s.name == "LM.__call__" for s in trace.data.spans
        )

    children = defaultdict(list)
    for span in trace.data.spans:
        children[span.parent_id].append(span)
    pending = list(children[root.span_id])
    model_calls = 0
    while pending:
        span = pending.pop()
        model_calls += span.name == "LM.__call__"
        pending.extend(children[span.span_id])

    forward = _root_forward(trace, root)
    usage = (
        forward.attributes.get("mlflow.chat.tokenUsage")
        if forward is not None
        else None
    )
    return usage, model_calls


def workspace_trace_task(client: MlflowClient, trace_id: str) -> str:
    trace = client.get_trace(trace_id)
    root = workspace_trace_root(trace) if trace is not None else None
    if (
        root is None
        or not isinstance(root.inputs, dict)
        or not isinstance(root.inputs.get("task"), str)
    ):
        raise ValueError(f"Missing RLM task in source trace {trace_id}")
    return root.inputs["task"]


def example_from_trace(client: MlflowClient, case: Case, repeat: int) -> dspy.Example:
    trace = client.get_trace(case.source_trace)
    root = workspace_trace_root(trace) if trace is not None else None
    forward = (
        _root_forward(trace, root) if trace is not None and root is not None else None
    )
    if forward is None or not isinstance(forward.inputs, dict):
        raise ValueError(f"Missing RLM source trace for {case.id}")
    captured = forward.inputs
    context = {key: captured.get(key, []) for key in INPUTS}
    context["memories"] = [
        item
        for item in context["memories"]
        if any(
            marker in item
            for marker in ("TAMARACK-62415", "JUNIPER-63947", "QUARTZ-4071")
        )
    ]
    if case.id == "birch-invalid":
        context["task"] = context["task"].replace("C = 13.", "C = 13.5.")
    if case.id == "memory-pending" and any(
        "TAMARACK-62415" in item for item in context["memories"]
    ):
        raise ValueError(
            "Pending workflow leaked its test fact into the replay context"
        )
    if case.id == "memory-approved" and not any(
        "TAMARACK-62415" in item and "pending approval" not in item.casefold()
        for item in context["memories"]
    ):
        raise ValueError("Approved workflow did not supply a clean memory")
    return dspy.Example(
        **context,
        case_id=case.id,
        lineage=case.lineage,
        source_trace=case.source_trace,
        repeat=repeat,
        expected=case.answer,
    ).with_inputs(*INPUTS, "case_id", "repeat")


def metric(example, prediction, trace=None) -> bool:
    answer = getattr(prediction, "answer", None)
    return isinstance(answer, str) and answer_passes(answer, example.expected)


def trace_usage(client: MlflowClient, trace_id: str | None) -> dict:
    trace = client.get_trace(trace_id) if trace_id else None
    if trace is None:
        return {"tokens": None, "model_calls": None, "seconds": None}
    return {
        "tokens": (trace.info.token_usage or {}).get("total_tokens"),
        "model_calls": sum(span.name == "LM.__call__" for span in trace.data.spans),
        "seconds": trace.info.execution_duration / 1000
        if trace.info.execution_duration is not None
        else None,
    }


def replay(
    settings: Settings, output: Path, repeats: int, pilot: bool, program: str
) -> None:
    if not settings.mlflow_tracking_uri:
        raise ValueError(
            "Configure FLEET_MLFLOW_TRACKING_URI before running the baseline"
        )
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    mlflow.dspy.autolog(log_evals=False)
    client = MlflowClient()
    selected = [
        case
        for case in CASES
        if not case.needs_workspace
        and (not pilot or case.id in {"memory-approved", "birch-valid", "writing"})
    ]
    examples = [
        example_from_trace(client, case, repeat)
        for case in selected
        for repeat in range(1, repeats + 1)
    ]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    root = agent._model(settings, settings.llm_model)
    # The comparison measures real independent executions, not DSPy cache hits.
    root.cache = False
    sub = agent._model(settings, settings.sub_llm_model)
    sub.cache = False
    output.mkdir(parents=True, exist_ok=True)
    programs = ("rlm", "flex") if program == "both" else (program,)
    for variant in programs:
        interpreter_context = (
            _ephemeral_daytona_interpreter(settings)
            if variant == "rlm"
            else nullcontext(None)
        )
        with (
            interpreter_context as factory,
            dspy.context(
                lm=root, **({"interpreter_factory": factory} if factory else {})
            ),
            mlflow.start_run(
                run_name=f"fleet-baseline-{variant}{'-pilot' if pilot else ''}"
            ) as run,
        ):
            mlflow.log_params(
                {
                    "program": variant,
                    "revision": revision,
                    "interpreter": "daytona" if factory else "flex-default",
                    "model": settings.llm_model,
                    "repeats": repeats,
                    "pilot": pilot,
                }
            )
            student = (
                agent.build_program(
                    settings.model_copy(update={"optimized_program_path": None}),
                    [],
                    sub_lm=sub,
                )
                if variant == "rlm"
                else dspy.Flex(agent.WorkspaceTask)
            )

            def predict(case_id, repeat, _student=student, _variant=variant, **inputs):
                with mlflow.start_span(
                    name="fleet.baseline",
                    attributes={
                        "case_id": case_id,
                        "repeat": repeat,
                        "program": _variant,
                    },
                ) as span:
                    prediction = _student(**inputs)
                    span.set_outputs({"answer": prediction.answer})
                    return dspy.Prediction(
                        answer=prediction.answer,
                        python=len(prediction.trajectory)
                        if hasattr(prediction, "trajectory")
                        else 0,
                        trace_id=span.trace_id,
                    )

            result = dspy.Evaluate(
                devset=examples,
                metric=metric,
                num_threads=3,
                max_errors=6,
            )(predict)
            mlflow.flush_trace_async_logging()
            rows = []
            for example, prediction, passed in result.results:
                trace_id = getattr(prediction, "trace_id", None)
                row = {
                    "id": example.case_id,
                    "repeat": example.repeat,
                    "lineage": example.lineage,
                    "source_trace": example.source_trace,
                    "trace_id": trace_id,
                    "expected": example.expected,
                    "answer": getattr(prediction, "answer", None),
                    "passed": bool(passed),
                    "python": getattr(prediction, "python", None),
                    **trace_usage(client, trace_id),
                }
                rows.append(row)
            report = {
                "run_id": run.info.run_id,
                "experiment": run.info.experiment_id,
                "program": variant,
                "revision": revision,
                "rows": rows,
                "excluded_workspace_cases": [
                    case.id for case in CASES if case.needs_workspace
                ],
            }
            path = output / f"{variant}{'-pilot' if pilot else ''}.json"
            path.write_text(json.dumps(report, indent=2) + "\n")
            mlflow.log_artifact(str(path))
            mlflow.log_metric("exact_correct", sum(row["passed"] for row in rows))
            mlflow.log_metric("rows", len(rows))
            print(
                f"{variant} {run.info.run_id} {sum(row['passed'] for row in rows)}/{len(rows)} {path}",
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--program", choices=("both", "rlm", "flex"), default="both")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    replay(Settings(), args.output_dir, args.repeats, args.pilot, args.program)


if __name__ == "__main__":
    main()
