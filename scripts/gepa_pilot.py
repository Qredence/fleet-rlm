"""Run the bounded RLM correctness experiment after reviewing the baseline."""

import argparse
import json
import os
from pathlib import Path

import dspy
import mlflow
import mlflow.dspy
from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback
from dspy_baseline import CASES, INPUTS, answer_passes, example_from_trace
from fleet_rlm import agent
from fleet_rlm.config import Settings
from fleet_rlm.optimize import (
    _ephemeral_daytona_interpreter,
    _save_program,
    guard_database_url,
)
from gepa.utils.stop_condition import MaxMetricCallsStopper
from mlflow import MlflowClient


def case_example(case_id, lineage, task, expected, **context):
    return dspy.Example(
        task=task,
        files_manifest=[],
        memories=context.get("memories", []),
        active_skills=context.get("active_skills", ""),
        prior_turns=context.get("prior_turns", []),
        case_id=case_id,
        lineage=lineage,
        expected=expected,
    ).with_inputs(*INPUTS)


def fresh_cases(group: str) -> list[dspy.Example]:
    """Two independently authored synthetic fixture families, kept outside training."""
    if group == "validation":
        handoff, marker, owner, status = "Tern departure", "TERN-4281", "Arden", "CLEAR"
        audit, coefficient, operands, valid = "Flint tally", 4, (9, -2), 34
        chat, previous, next_value = "Calico transfer", "DELTA-7N", "DELTA-5F"
    else:
        handoff, marker, owner, status = (
            "Oriole transfer",
            "ORIOLE-58021",
            "Lena",
            "OPEN",
        )
        audit, coefficient, operands, valid = "Mica audit", 7, (-3, 52), 31
        chat, previous, next_value = "Saffron dispatch", "IVORY-8K", "IVORY-1D"

    prompt = (
        f"Prepare the saved {handoff} handoff for owner {owner} and state {status}. "
        "Follow the approved memory exactly when available. If no saved template is present, "
        "reply exactly NO SAVED HANDOFF FORMAT. Do not infer a template."
    )
    rule = (
        f"For {handoff} handoffs only, output the code {marker}, the state, and the owner in "
        "that order separated by a slash with a space on each side. Output only that line."
    )
    distractor = "For temporary dock notices only, lead with NOT-A-HANDOFF."
    skill = (
        f"## {audit}\nReject any noninteger argument by replying exactly '{audit.upper()} INVALID'. "
        f"Otherwise multiply the first argument by {coefficient} and add the second. "
        "Output the integer alone."
    )
    a, b = operands
    return [
        case_example(
            "memory-pending",
            handoff,
            prompt,
            "NO SAVED HANDOFF FORMAT",
            memories=[distractor],
        ),
        case_example(
            "memory-approved",
            handoff,
            prompt,
            f"{marker} / {status} / {owner}",
            memories=[distractor, rule],
        ),
        case_example(
            "skill-valid",
            audit,
            f"Audit {a} and {b} under the supplied skill. Give its exact output.",
            str(valid),
            active_skills=skill,
        ),
        case_example(
            "skill-invalid",
            audit,
            f"Audit {a}.5 and {b} under the supplied skill. Give its exact output.",
            f"{audit.upper()} INVALID",
            active_skills=skill,
        ),
        case_example(
            "history",
            chat,
            f"Read the earlier messages in this session. Give only the current {chat} marker.",
            next_value,
            prior_turns=[
                {
                    "user": f"The {chat} marker was {previous}.",
                    "assistant": "Acknowledged.",
                },
                {
                    "user": f"The {chat} marker has changed to {next_value}.",
                    "assistant": "Acknowledged.",
                },
            ],
        ),
        case_example(
            "isolation",
            chat,
            f"Give the earlier {chat} marker only if it already appears in this session. Otherwise answer exactly NO SESSION MARKER.",
            "NO SESSION MARKER",
        ),
    ]


def feedback(
    example, prediction, trace=None, pred_name=None, pred_trace=None, program_trace=None
):
    answer = getattr(prediction, "answer", "")
    passed = isinstance(answer, str) and answer_passes(answer, example.expected)
    guidance = {
        "memory-approved": "Use the approved template from memories, independently of workspace files.",
        "memory-pending": "Return the fallback exactly if the requested template is absent.",
        "skill-invalid": "Apply the skill's validity check before computing.",
        "birch-invalid": "Apply the skill's validity check before computing.",
        "quartz-invalid": "Apply the skill's validity check before computing.",
        "history": "Read the latest correction in the supplied prior turns.",
        "isolation": "Use only this session's prior turns.",
        "writing": "Honor the two-sentence limit and include only the three supplied facts.",
    }.get(example.case_id, "Apply the supplied skill and use its exact output format.")
    return ScoreWithFeedback(
        score=float(passed), feedback="Correct." if passed else guidance
    )


def repeated_examples(examples):
    return [
        dspy.Example(base=example, repeat=repeat).with_inputs(
            *INPUTS, "case_id", "repeat"
        )
        for example in examples
        for repeat in range(1, 6)
    ]


def compare(student, examples, tag):
    def predict(case_id, repeat, **inputs):
        with mlflow.start_span(
            name="fleet.gepa.holdout",
            attributes={"case_id": case_id, "repeat": repeat, "variant": tag},
        ) as span:
            result = student(**inputs)
            return dspy.Prediction(answer=result.answer, trace_id=span.trace_id)

    result = dspy.Evaluate(
        devset=repeated_examples(examples),
        metric=lambda x, y: feedback(x, y).score,
        num_threads=4,
    )(predict)
    mlflow.flush_trace_async_logging()
    return [
        {
            "case": example.case_id,
            "lineage": example.lineage,
            "repeat": example.repeat,
            "answer": getattr(prediction, "answer", None),
            "trace_id": getattr(prediction, "trace_id", None),
            "passed": bool(score),
        }
        for example, prediction, score in result.results
    ]


def run(output: Path, database_url: str) -> None:
    settings = Settings()
    guard_database_url(database_url, settings)
    if not settings.mlflow_tracking_uri:
        raise ValueError("Configure FLEET_MLFLOW_TRACKING_URI")
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    mlflow.dspy.autolog(log_compiles=True, log_traces_from_compile=True)
    tracker = MlflowClient()
    train = [
        example_from_trace(tracker, case, 1).with_inputs(*INPUTS)
        for case in CASES
        if not case.needs_workspace
    ]
    validation = fresh_cases("validation")
    holdout = fresh_cases("holdout")
    groups = [
        {example.lineage for example in split} for split in (train, validation, holdout)
    ]
    if any(groups[i] & groups[j] for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Evaluation lineage overlaps between splits")
    root = agent._model(settings, settings.llm_model)
    sub = agent._model(settings, settings.sub_llm_model)
    reflection = agent._model(settings, settings.llm_model)
    root.cache = sub.cache = reflection.cache = False
    output.mkdir(parents=True, exist_ok=True)

    with (
        _ephemeral_daytona_interpreter(settings) as factory,
        dspy.context(lm=root, interpreter_factory=factory),
        mlflow.start_run(run_name="fleet-rlm-gepa-50") as tracking,
    ):
        student = agent.build_program(
            settings.model_copy(update={"optimized_program_path": None}), [], sub_lm=sub
        )
        mlflow.log_params(
            {
                "max_metric_calls": 50,
                "objective": "exact",
                "revision": "75120dc1d",
                "training_lineages": len(groups[0]),
                "validation_lineages": len(groups[1]),
                "holdout_lineages": len(groups[2]),
            }
        )
        metric_budget = 50
        minibatch = 2
        # GEPA tests its stopper between iterations. Reserve room for both
        # minibatches and a full validation batch.
        last_start = metric_budget - (2 * minibatch + len(validation))
        candidate = dspy.GEPA(
            metric=feedback,
            max_metric_calls=metric_budget,
            reflection_lm=reflection,
            reflection_minibatch_size=minibatch,
            use_merge=False,
            track_stats=True,
            gepa_kwargs={"stop_callbacks": MaxMetricCallsStopper(last_start + 1)},
            num_threads=4,
            seed=0,
            log_dir=str(output / "search"),
        ).compile(student, trainset=train, valset=validation)
        baseline = compare(student, holdout, "incumbent")
        proposed = compare(candidate, holdout, "candidate")
        pairs = zip(baseline, proposed, strict=True)
        regressions = [
            after for before, after in pairs if before["passed"] and not after["passed"]
        ]
        blockers = [
            row
            for row in proposed
            if row["case"] in {"memory-approved", "memory-pending", "isolation"}
            and not row["passed"]
        ]
        before_total = sum(row["passed"] for row in baseline)
        after_total = sum(row["passed"] for row in proposed)
        search_calls = candidate.detailed_results.total_metric_calls
        changed = any(
            base.signature.instructions != proposed.signature.instructions
            for base, proposed in zip(
                student.predictors(), candidate.predictors(), strict=True
            )
        )
        eligible = (
            changed
            and search_calls is not None
            and search_calls <= metric_budget
            and not regressions
            and not blockers
            and after_total > before_total
        )
        if eligible:
            _save_program(candidate, output / "candidate.json")
        report = {
            "run_id": tracking.info.run_id,
            "experiment": tracking.info.experiment_id,
            "baseline": baseline,
            "candidate": proposed,
            "regressions": regressions,
            "blocking_failures": blockers,
            "instructions_changed": changed,
            "search_metric_calls": search_calls,
            "eligible_for_live_canary": eligible,
            "lineages": [sorted(group) for group in groups],
        }
        path = output / "gepa.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        mlflow.log_artifact(str(path))
        mlflow.log_metrics(
            {
                "baseline_correct": before_total,
                "candidate_correct": after_total,
                "new_failures": len(regressions),
                "blocking_failures": len(blockers),
            }
        )
        print(
            f"{tracking.info.run_id} holdout {before_total}/{len(baseline)} -> {after_total}/{len(proposed)} eligible={eligible}",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get("FLEET_EVAL_DATABASE_URL")
    if not url:
        parser.error("FLEET_EVAL_DATABASE_URL must identify the isolated Neon branch")
    run(args.output_dir, url)


if __name__ == "__main__":
    main()
