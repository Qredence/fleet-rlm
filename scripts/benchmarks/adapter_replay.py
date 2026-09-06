"""Credential-free protocol replay for runtime benchmark v2, not semantic evaluation."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import dspy
from dspy.utils.exceptions import AdapterParseError, LMServerError, LMTimeoutError

from fleet_rlm.rlm.budget import BudgetLimits, TurnBudget
from fleet_rlm.rlm.compat_3_3_1 import FleetJSONAdapter, assert_dspy_version
from fleet_rlm.rlm.program import DeadlineLMProxy

DATASET = Path(__file__).with_name("runtime_v2_adapter_cases.json")
SCORERS = ("adapter-outcome/v1", "provider-accounting/v1", "fleet-attempt-ceiling/v1")


class ActionSignature(dspy.Signature):
    iteration: str = dspy.InputField()
    reasoning: str = dspy.OutputField()
    code: str = dspy.OutputField()


class ReplayLM(dspy.BaseLM):
    """Scripted provider responses; never performs network I/O or caches calls."""

    forward_contract = "legacy"

    def __init__(self, case: dict[str, Any], clock: list[float]) -> None:
        """
        Initialize the scripted language model with a replay case and shared clock.

        Parameters:
            case (dict[str, Any]): Dataset case containing the scripted responses and behavior.
            clock (list[float]): Mutable clock value used to track simulated time.
        """
        super().__init__("test/adapter-replay", cache=False, num_retries=0)
        self.case = case
        self.clock = clock
        self.calls: list[dict[str, Any]] = []

    @property
    def supported_params(self) -> set[str]:
        """
        Return the provider parameters supported for the current replay case.
        """
        return {"response_format"} if self.case.get("structured") else set()

    @property
    def supports_response_schema(self) -> bool:
        """Indicate whether the scripted case supports structured responses.

        Returns:
                bool: `True` if the case is structured, `False` otherwise.
        """
        return bool(self.case.get("structured"))

    def forward(self, prompt=None, messages=None, **kwargs):
        """
        Replay the next scripted language-model response and record the request.

        Raises:
            LMTimeoutError: If the scripted response indicates a provider timeout.
            LMServerError: If the scripted response indicates a retryable server error.

        Returns:
            A response object containing the scripted content, usage data, and model name.
        """
        self.calls.append({"prompt": prompt, "messages": messages, "kwargs": kwargs})
        responses = self.case["responses"]
        text = responses[min(len(self.calls) - 1, len(responses) - 1)]
        if self.case.get("late"):
            self.clock[0] = 109.5
        if text == "provider-timeout":
            raise LMTimeoutError("scripted timeout")
        if text == "retryable-server-error":
            raise LMServerError("scripted retry")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            model=self.model,
        )

    async def aforward(self, **kwargs):
        """Produce the scripted response for a model call.

        Returns:
            The scripted response.
        """
        return self.forward(**kwargs)


def replay(case: dict[str, Any], *, variant: str, asynchronous: bool) -> dict[str, Any]:
    """
    Replay one benchmark case through the selected JSON adapter configuration.

    Parameters:
        case (dict[str, Any]): Dataset case containing scripted responses and expected outcomes.
        variant (str): Adapter variant to exercise.
        asynchronous (bool): Whether to invoke the adapter asynchronously.

    Returns:
        dict[str, Any]: Recorded outcome, timing, provider-attempt counts, and scorer results.
    """
    clock = [109.5 if case.get("reserve") else 100.0]
    turn = TurnBudget(deadline=110.0, limits=BudgetLimits(provider_attempts=10))
    source = ReplayLM(case, clock)
    lm = DeadlineLMProxy(
        source,
        deadline=110.0,
        reserve_seconds=0.0,
        retries=case.get("provider_retries", 0),
        error_message="replay deadline",
        budget=turn,
    )
    adapter: dspy.JSONAdapter
    if variant == "stock":
        adapter = dspy.JSONAdapter()
    else:
        retries = {"fleet": 2, "fleet-no-parse-repair": 0, "fleet-one-parse-repair": 1}[variant]
        adapter = FleetJSONAdapter(deadline=110.0, wrap_up_seconds=1.0, max_parse_retries=retries, budget=turn)
    started = time.perf_counter()
    with patch("fleet_rlm.rlm.budget.time.monotonic", lambda: clock[0]):
        try:
            if asynchronous:
                result = asyncio.run(adapter.acall(lm, {}, ActionSignature, [], {"iteration": "1/3"}))
            else:
                result = adapter(lm, {}, ActionSignature, [], {"iteration": "1/3"})
            outcome = "submit" if result[0]["code"] == "SUBMIT(answer=1)" else "other-action"
        except (AdapterParseError, LMTimeoutError, TimeoutError) as exc:
            outcome = type(exc).__name__
    return {
        "case": case["id"],
        "variant": variant,
        "mode": "async" if asynchronous else "sync",
        "seconds": time.perf_counter() - started,
        "outcome": outcome,
        "provider_attempts": len(source.calls),
        "budget_attempts": turn.snapshot()["provider_attempts"],
        "scores": {
            "adapter-outcome/v1": outcome == case["expected"],
            "provider-accounting/v1": turn.snapshot()["provider_attempts"] == len(source.calls),
            "fleet-attempt-ceiling/v1": variant != "fleet" or len(source.calls) == case["fleet_attempts"],
        },
    }


def run_adapter_comparison(*, repetitions: int = 5) -> dict[str, Any]:
    """
    Run the scripted adapter comparison across configured variants and execution modes.

    Parameters:
        repetitions (int): Number of times to execute each dataset case; must be at least 2.

    Returns:
        dict[str, Any]: Sealed benchmark results containing samples, per-variant summaries,
            validation gates, metadata, and the overall pass status.

    Raises:
        ValueError: If repetitions is less than 2.
    """
    from scripts.benchmarks.runtime_v2 import digest, percentile, seal

    assert_dspy_version()
    if repetitions < 2:
        raise ValueError("adapter comparison requires at least two repetitions")
    dataset = json.loads(DATASET.read_text())
    variants = ("stock", "fleet", "fleet-no-parse-repair", "fleet-one-parse-repair")
    samples = []
    for repetition in range(repetitions):
        for case in dataset["cases"]:
            for variant in variants:
                for asynchronous in (False, True):
                    samples.append(
                        {**replay(case, variant=variant, asynchronous=asynchronous), "repetition": repetition}
                    )
    summary = {}
    for variant in variants:
        selected = [sample for sample in samples if sample["variant"] == variant]
        durations = [sample["seconds"] for sample in selected]
        summary[variant] = {
            "samples": len(selected),
            "correct": sum(sample["scores"]["adapter-outcome/v1"] for sample in selected),
            "provider_attempts": sum(sample["provider_attempts"] for sample in selected),
            "latency_seconds": {"p50": percentile(durations, 50), "p95": percentile(durations, 95)},
        }
    paired = {}
    parity = True
    for sample in samples:
        key = (sample["case"], sample["variant"], sample["repetition"])
        result = (sample["outcome"], sample["provider_attempts"], sample["budget_attempts"])
        if key in paired and paired[key] != result:
            parity = False
        paired[key] = result
    gates = {
        "fleet_contract": all(all(sample["scores"].values()) for sample in samples if sample["variant"] == "fleet"),
        "all_attempts_accounted": all(sample["scores"]["provider-accounting/v1"] for sample in samples),
        "sync_async_parity": parity,
        "valid_output_no_extra_calls": all(
            sample["provider_attempts"] == 1 for sample in samples if sample["case"] == "valid/v1"
        ),
    }
    implementation_paths = (
        Path(__file__),
        Path("scripts/benchmarks/runtime_v2.py"),
        Path("src/fleet_rlm/rlm/submit_validation.py"),
        Path("src/fleet_rlm/rlm/compat_3_3_1.py"),
        Path("src/fleet_rlm/rlm/budget.py"),
        Path("src/fleet_rlm/rlm/program.py"),
    )
    return seal(
        {
            "schema": "fleet.runtime-adapter-comparison/v2",
            "scope": "scripted-adapter-protocol-only",
            "runtime_variant": "legacy",
            "dspy_version": dspy.__version__,
            "repetitions": repetitions,
            "dataset_digest": digest(dataset),
            "scorer_digest": digest({"ids": SCORERS, "source": Path(__file__).read_text()}),
            "implementation_digest": digest({str(path): path.read_text() for path in implementation_paths}),
            "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
            "semantic_gate": "not_exercised",
            "scorer_ids": list(SCORERS),
            "samples": samples,
            "summary": summary,
            "gates": gates,
            "passed": all(gates.values()),
        }
    )
