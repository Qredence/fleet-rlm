"""Explicit native GEPA/Flex canary: synthetic data, live Gateway models, disposable Daytona runtimes.

Usage: uv run python scripts/verify_gepa_live.py --live
This is a small integration check with an intentionally wrong seed, not a production quality benchmark.
Search is capped at six metric calls, one worker, and small generation budgets. No database writes,
MLflow export, or candidate promotion occur. The report contains usage and cleanup evidence.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

import dspy
from daytona import ListSandboxesQuery

from fleet_rlm.config import FleetSettings
from fleet_rlm.dspy_support import FleetRLMChatAdapter, MeteredLM
from fleet_rlm.optimize.artifacts import IsolatedFleetProgram, digest_bytes
from fleet_rlm.optimize.scorers import fleet_metric
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

_SEED = """class Addition(dspy.Module):
    def forward(self, **inputs):
        return dspy.Prediction(answer="unoptimized")
"""


class AdditionProgram(dspy.Module):
    def __init__(self, manager: DaytonaSandboxManager, lm: dspy.LM):
        super().__init__()
        self.sandbox_mgr = manager
        self.lm = lm
        self.flex_agent = dspy.Flex(
            "task: str -> answer: str", interpreter_factory=manager.interpreter_factory(), max_predictor_calls=1
        )

    def forward(self, task: str) -> dspy.Prediction:
        self.sandbox_mgr.reset_turn_budget()
        try:
            with dspy.context(lm=MeteredLM(self.lm, self.sandbox_mgr.consume_budget), adapter=FleetRLMChatAdapter()):
                result = self.flex_agent(task=task)
            return dspy.Prediction(answer=result.answer, **self.sandbox_mgr.turn_usage())
        finally:
            self.sandbox_mgr.end_turn()


def main() -> None:
    """Run the `--live` GEPA canary, save its report and candidate, and attempt sandbox cleanup.

    Exit with status 1 when validation fails or provider cleanup is incomplete.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize the bounded live canary")
    parser.add_argument("--output", type=Path, default=Path(".scratch/gepa-validation/report.json"))
    args = parser.parse_args()
    if not args.live:
        parser.error("--live authorization is required")
    settings = FleetSettings(
        fleet_database_url="postgresql://unused.invalid/gepa-canary",
        fleet_database_migration_url="",
        mlflow_mode="local",
    ).with_explicit_overrides(
        fleet_root_max_tokens=512,
        fleet_sub_max_tokens=512,
        fleet_reflection_max_tokens=4096,
        fleet_root_num_retries=0,
        fleet_sub_num_retries=0,
        fleet_reflection_num_retries=0,
        fleet_root_cache=False,
        fleet_sub_cache=False,
        fleet_reflection_cache=False,
    )
    root, _, reflection, _ = settings.build_lms()
    judge = settings.build_judge_lm().copy(max_tokens=512, num_retries=0)
    managers: list[DaytonaSandboxManager] = []
    run_id = "gepa-canary-" + uuid.uuid4().hex[:12]
    report: dict[str, Any] = {
        "run_id": run_id,
        "root_model": root.model,
        "reflection_model": reflection.model,
        "search_budget": 6,
        "production_candidate": False,
    }

    def manager() -> DaytonaSandboxManager:
        """Create and track a disposable sandbox manager for one canary rollout."""
        runtime = DaytonaSandboxManager(
            settings.fleet_daytona_api_key,
            settings.fleet_daytona_org_id,
            settings.fleet_daytona_volume_id,
            f"{run_id}-{len(managers)}",
            max_llm_calls=1,
            max_depth=0,
            max_children=1,
            max_active_children=1,
            turn_timeout_seconds=60,
            auto_stop_minutes=1,
            disposable=True,
        )
        managers.append(runtime)
        return runtime

    def factory(_example: dspy.Example, candidate: dspy.Module) -> AdditionProgram:
        runtime = AdditionProgram(manager(), root)
        runtime.load_state(candidate.dump_state())
        return runtime

    seed = AdditionProgram(manager(), root)
    seed.flex_agent.load_state({"module_src": _SEED})
    program = IsolatedFleetProgram(seed, factory)
    train = [
        dspy.Example(task=f"Add {a} and {b}; return the decimal integer only.", answer=str(a + b)).with_inputs("task")
        for a, b in ((13, 17), (29, 41))
    ]
    dev = [dspy.Example(task="Add 37 and 19; return the decimal integer only.", answer="56").with_inputs("task")]

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
        result = fleet_metric(gold, pred, trace, pred_name, pred_trace, judge_lm=judge)
        if result.objective_scores["quality"] != 1.0:
            result.feedback += (
                " For addition, parse the two integers and sum them in Python. Generalize to other pairs."
            )
        return result

    started = time.monotonic()
    try:
        # The serving probe verifies the configured model/adapter independently of deterministic candidates.
        with dspy.context(lm=root, adapter=FleetRLMChatAdapter()):
            probe = dspy.Predict("task -> answer")(task="What is 2+2? Return only the integer.")
        report["serving_probe"] = str(probe.answer).strip() == "4"
        with dspy.context(adapter=FleetRLMChatAdapter()):
            candidate = dspy.GEPA(
                metric=metric,
                reflection_lm=reflection,
                max_metric_calls=6,
                reflection_minibatch_size=1,
                num_threads=1,
                use_merge=False,
                track_stats=True,
                seed=0,
            ).compile(program, trainset=train, valset=dev)
        report["source_changed"] = candidate.program.flex_agent.module_src != _SEED
        report["search"] = candidate.detailed_results.to_dict()
        artifact = args.output.parent / f"{run_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        candidate.program.save(str(artifact))
        restored = AdditionProgram(manager(), root)
        restored.load(str(artifact))
        isolated = IsolatedFleetProgram(restored, factory)
        # This held-out pair is never given to the optimizer or the reflection model.
        result = isolated(task="Add 101 and 307; return the decimal integer only.")
        report["heldout_correct"] = result.answer.strip() == "408"
        report["heldout_usage"] = {k: result[k] for k in ("lm_calls", "sandbox_errors")}
        report["heldout_metric"] = float(metric(dspy.Example(task="Add 101 and 307", answer="408"), result))
        report["artifact_digest"] = digest_bytes(artifact.read_bytes())
        report["status"] = (
            "passed" if all(report[key] for key in ("serving_probe", "source_changed", "heldout_correct")) else "failed"
        )
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        cleanup_errors = []
        for runtime in managers:
            try:
                runtime.shutdown()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        remaining = []
        for runtime in managers:
            if runtime._client is not None:
                try:
                    remaining.extend(
                        box.id
                        for box in runtime._client.list(
                            ListSandboxesQuery(labels={"fleet_session": runtime.session_id})
                        )
                    )
                except Exception as exc:
                    cleanup_errors.append(type(exc).__name__)
        report["provider_remaining_sandboxes"] = remaining
        report["cleanup_errors"] = cleanup_errors
        if cleanup_errors or remaining:
            report["status"] = "cleanup_incomplete"
        report["usage"] = {
            role: [item.get("usage", {}) for item in lm.history]
            for role, lm in (("serving", root), ("reflection", reflection), ("judge", judge))
        }
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str))
        print(
            json.dumps(
                {
                    key: report.get(key)
                    for key in ("status", "source_changed", "heldout_correct", "cleanup_errors", "usage")
                }
            )
        )
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
