"""Execute one direct DSPy Phase 4 ablation trial.

The campaign driver invokes this module in a short-lived process for arms A
and B only.  The app-backed arms C and D use the public FastAPI/SSE client in
``phase4_api_client.py``; keeping them out of this worker prevents an
in-process TestClient path from being mistaken for production transport.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

# The worker is intentionally runnable from a baseline checkout that predates
# the Phase 4 package.  Keep the candidate-side campaign helpers importable,
# while resolving ``fleet_rlm`` from the selected checkout's ``src`` tree.
_CANDIDATE_ROOT = Path(__file__).resolve().parents[2]
_CHECKOUT_ROOT = Path.cwd().resolve()
for _path in (str(_CANDIDATE_ROOT), str(_CHECKOUT_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import dspy

from scripts.benchmarks.phase4_campaign import ARMS, Phase4Case, Trial, TrialObservation

_SOURCE_ID = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z][A-Za-z0-9_-]{0,63})(?![A-Za-z0-9_-])")
_MAX_WORKER_OUTPUT = 50_000


class Phase4Answer(dspy.Signature):
    """Return a bounded answer and explicit source/uncertainty fields."""

    question: str = dspy.InputField(desc="The sealed task question")
    source_material: str = dspy.InputField(desc="The selected bounded source records, labeled by source ID")
    answer: str = dspy.OutputField(desc="A concise answer supported only by the selected source records")
    evidence: str = dspy.OutputField(desc="A JSON array or comma-separated list of source IDs used")
    uncertainty: str = dspy.OutputField(desc="Required uncertainty or an empty string when none is required")


class Phase4FleetAnswer(dspy.Signature):
    """Fleet Root result encoded as one JSON string for the public answer field."""

    request: str = dspy.InputField(desc="The sealed task and attachment instructions")
    history: dspy.History = dspy.InputField(desc="Empty committed history for this isolated trial")
    session_context: dict = dspy.InputField(desc="Bounded session metadata")
    skill_cards: list[dict] = dspy.InputField(desc="No selected skills")
    attachments: list[dict] = dspy.InputField(desc="One immutable text source attachment")
    answer: str = dspy.OutputField(desc="JSON object with answer, evidence, and uncertainty fields")


def _blank(*, category: str, cleanup: bool = False, authorization: bool = False) -> TrialObservation:
    return TrialObservation(
        answer="",
        cited_evidence=(),
        uncertainty="",
        completed=False,
        authorization_confirmed=authorization,
        cleanup_confirmed=cleanup,
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        sandbox_seconds=None,
        latency_ms=None,
        root_lm_calls=None,
        child_lm_calls=None,
        delegated_bytes=None,
        sandbox_count=None,
        resource_shape=None,
        error_category=category,
    )


def _case_sources(case: Phase4Case) -> str:
    return "\n\n".join(f"[{key}] {case.sources[key]}" for key in sorted(case.sources))


def _parse_result(value: object, source_ids: set[str]) -> tuple[str, tuple[str, ...], str]:
    answer = ""
    evidence_value: object = ()
    uncertainty = ""
    if isinstance(value, Mapping):
        answer = value.get("answer", "") if isinstance(value.get("answer"), str) else ""
        evidence_value = value.get("evidence", ())
        uncertainty = value.get("uncertainty", "") if isinstance(value.get("uncertainty"), str) else ""
    elif isinstance(value, str):
        text = value.strip()
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            answer = text
        else:
            if isinstance(decoded, Mapping):
                return _parse_result(decoded, source_ids)
            answer = text
    else:
        raise ValueError("worker structured result is malformed")
    if len(answer.encode("utf-8")) > _MAX_WORKER_OUTPUT or len(uncertainty.encode("utf-8")) > 2_000:
        raise ValueError("result exceeds the worker bound")
    if isinstance(evidence_value, str):
        evidence_items = [item.strip() for item in evidence_value.split(",") if item.strip()]
    elif isinstance(evidence_value, Sequence) and not isinstance(evidence_value, (bytes, bytearray, str)):
        if any(not isinstance(item, str) for item in evidence_value):
            raise ValueError("worker evidence is malformed")
        evidence_items = [item for item in evidence_value if item.strip()]
    else:
        raise ValueError("worker evidence is malformed")
    normalized: list[str] = []
    for item in evidence_items:
        candidate = item.strip().strip("[]")
        if candidate not in source_ids:
            raise ValueError("citation is not available in the sealed source material")
        if candidate not in normalized:
            normalized.append(candidate)
    if not normalized:
        for candidate in _SOURCE_ID.findall(answer):
            if candidate in source_ids and candidate not in normalized:
                normalized.append(candidate)
    return answer, tuple(normalized), uncertainty


def _usage_entry(value: object) -> tuple[int | None, int | None]:
    if not isinstance(value, Mapping):
        return None, None
    aliases = {
        "input": ("input_tokens", "prompt_tokens"),
        "output": ("output_tokens", "completion_tokens"),
    }
    result: dict[str, int | None] = {}
    for name, keys in aliases.items():
        raw = next((value.get(key) for key in keys if value.get(key) is not None), None)
        result[name] = raw if type(raw) is int and raw >= 0 else None
    return result["input"], result["output"]


def _lm_observation(
    *,
    answer: object,
    evidence: object,
    uncertainty: object,
    root_lm: Any,
    sub_lm: Any | None,
    source_ids: set[str],
    started: float,
) -> TrialObservation:
    parsed_answer, cited, parsed_uncertainty = _parse_result(
        {"answer": answer, "evidence": evidence, "uncertainty": uncertainty}, source_ids
    )
    models = (root_lm,) if sub_lm is None else (root_lm, sub_lm)
    histories = [getattr(model, "history", ()) for model in models]
    if any(not isinstance(history, list) for history in histories):
        raise ValueError("LM history is unavailable")
    calls = sum(len(history) for history in histories)
    if calls < 1:
        raise ValueError("LM call count is unavailable")
    inputs: list[int] = []
    outputs: list[int] = []
    for history in histories:
        for entry in history:
            usage = entry.get("usage") if isinstance(entry, Mapping) else None
            input_tokens, output_tokens = _usage_entry(usage)
            if input_tokens is None or output_tokens is None:
                raise ValueError("LM token usage is unavailable")
            inputs.append(input_tokens)
            outputs.append(output_tokens)
    return TrialObservation(
        answer=parsed_answer,
        cited_evidence=cited,
        uncertainty=parsed_uncertainty,
        completed=bool(parsed_answer.strip()),
        authorization_confirmed=True,
        cleanup_confirmed=True,
        input_tokens=sum(inputs),
        output_tokens=sum(outputs),
        cache_read_tokens=0,
        sandbox_seconds=0,
        latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
        root_lm_calls=len(histories[0]),
        child_lm_calls=sum(len(history) for history in histories[1:]),
        delegated_bytes=0,
        sandbox_count=0,
        resource_shape=None,
    )


def _campaign_settings(*, recursive: bool, trial: Trial, root: Path) -> Any:
    """Load the selected checkout policy and apply the sealed campaign overlay."""
    import fleet_rlm.config.loader as loader

    settings = loader.load_runtime_settings()
    settings = settings.model_copy(
        update={
            # The worker may be imported from the frozen C checkout, whose
            # default profile predates the sealed campaign.  Apply every
            # cost-relevant LLM setting explicitly so all four arms share the
            # same model, deterministic decoding, retry allowance, and token
            # ceilings independent of the checkout's default profile.
            "root_llm_max_tokens": 1_024,
            "sub_llm_max_tokens": 512,
            "root_llm_timeout_seconds": 90,
            "sub_llm_timeout_seconds": 90,
            "root_llm_temperature": 0.0,
            "sub_llm_temperature": 0.0,
            "root_llm_num_retries": 0,
            "sub_llm_num_retries": 0,
            "root_llm_cache": False,
            "sub_llm_cache": False,
            "rlm_max_iters": 6,
            "rlm_max_llm_calls": 8,
            "rlm_max_provider_attempts": 8,
            "rlm_execution_timeout_s": 90,
            "rlm_wrap_up_seconds": 30,
            "rlm_recursion_enabled": recursive,
            "rlm_recursion_max_calls": 4,
            "rlm_recursion_child_max_iters": 4,
            "rlm_recursion_child_max_llm_calls": 4,
            "rlm_recursion_child_max_output_chars": 2_000,
            "rlm_recursion_max_parallel_children": 4,
            "max_active_daytona_leases": 1,
            "turn_timeout_seconds": 90,
            "mlflow_tracing_enabled": False,
            "data_root": str(root / "fleet-data"),
            "database_url": f"sqlite+aiosqlite:///{(root / 'trial.db').resolve()}",
            "volume_name": f"fleet-p4-{trial.arm.lower()}-{trial.case_id}-{trial.repeat}-{uuid4().hex[:12]}",
        }
    )
    return settings


def _model_observation(case: Phase4Case, trial: Trial) -> TrialObservation:
    from fleet_rlm.rlm.program import build_model_bundle

    with tempfile.TemporaryDirectory(prefix="fleet-p4-lm-") as temp:
        settings = _campaign_settings(recursive=False, trial=trial, root=Path(temp))
        source = _case_sources(case)
        started = time.perf_counter()
        models = build_model_bundle(settings)
        predictor = dspy.Predict(Phase4Answer)
        with dspy.context(lm=models.root_lm):
            prediction = predictor(question=case.question, source_material=source)
        return _lm_observation(
            answer=getattr(prediction, "answer", ""),
            evidence=getattr(prediction, "evidence", ""),
            uncertainty=getattr(prediction, "uncertainty", ""),
            root_lm=models.root_lm,
            sub_lm=None,
            source_ids=set(case.sources),
            started=started,
        )


def _native_observation(case: Phase4Case, trial: Trial) -> TrialObservation:
    from fleet_rlm.rlm.program import build_model_bundle

    with tempfile.TemporaryDirectory(prefix="fleet-p4-rlm-") as temp:
        settings = _campaign_settings(recursive=False, trial=trial, root=Path(temp))
        models = build_model_bundle(settings)
        source = _case_sources(case)
        started = time.perf_counter()
        rlm = dspy.RLM(
            Phase4Answer,
            max_iters=6,
            max_llm_calls=8,
            max_output_chars=settings.rlm_max_output_chars,
            verbose=False,
            tools=[],
            sub_lm=models.sub_lm,
        )
        with dspy.context(lm=models.root_lm):
            prediction = rlm(question=case.question, source_material=source)
        return _lm_observation(
            answer=getattr(prediction, "answer", ""),
            evidence=getattr(prediction, "evidence", ""),
            uncertainty=getattr(prediction, "uncertainty", ""),
            root_lm=models.root_lm,
            sub_lm=models.sub_lm,
            source_ids=set(case.sources),
            started=started,
        )


def _run(payload: Mapping[str, Any]) -> TrialObservation:
    case = Phase4Case.from_mapping(payload.get("case", {}))
    raw_trial = payload.get("trial")
    if not isinstance(raw_trial, Mapping):
        raise ValueError("trial descriptor is invalid")
    order = raw_trial.get("arm_order")
    if not isinstance(order, list) or tuple(order) not in {
        (ARMS[0], ARMS[1], ARMS[2], ARMS[3]),
        (ARMS[1], ARMS[2], ARMS[3], ARMS[0]),
        (ARMS[2], ARMS[3], ARMS[0], ARMS[1]),
        (ARMS[3], ARMS[0], ARMS[1], ARMS[2]),
    }:
        raise ValueError("trial order is invalid")
    trial = Trial(
        case_id=raw_trial.get("case_id", ""),
        classification=raw_trial.get("classification", "control"),
        repeat=raw_trial.get("repeat", 0),
        arm=raw_trial.get("arm", "A"),
        arm_order=tuple(order),
    )
    if trial.case_id != case.identifier or trial.arm not in {"A", "B", "C", "D"} or trial.repeat not in {1, 2, 3}:
        raise ValueError("trial descriptor does not match corpus")
    if trial.arm == "A":
        return _model_observation(case, trial)
    if trial.arm == "B":
        return _native_observation(case, trial)
    return _blank(category="api_adapter_required")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help="read one trial descriptor from stdin")
    args = parser.parse_args(argv)
    if not args.worker:
        return 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, Mapping):
            raise ValueError("worker payload is invalid")
        # Keep the stdout channel a single sanitised JSON protocol record.
        # DSPy, FastAPI startup hooks, or a provider SDK may write diagnostics
        # to stdout; redirect those bytes to the discarded stderr channel so
        # they cannot corrupt the receipt parser or leak into campaign data.
        with contextlib.redirect_stdout(sys.stderr):
            observation = _run(payload)
    except BaseException as exc:
        # Only the exception class is a bounded diagnostic; provider messages
        # can contain prompts, endpoints, or credentials.
        observation = _blank(category=type(exc).__name__[:64] or "worker_failed")
    print(
        json.dumps(
            observation.__dict__
            if hasattr(observation, "__dict__")
            else {
                "answer": observation.answer,
                "cited_evidence": list(observation.cited_evidence),
                "uncertainty": observation.uncertainty,
                "completed": observation.completed,
                "authorization_confirmed": observation.authorization_confirmed,
                "cleanup_confirmed": observation.cleanup_confirmed,
                "input_tokens": observation.input_tokens,
                "output_tokens": observation.output_tokens,
                "cache_read_tokens": observation.cache_read_tokens,
                "sandbox_seconds": observation.sandbox_seconds,
                "latency_ms": observation.latency_ms,
                "root_lm_calls": observation.root_lm_calls,
                "child_lm_calls": observation.child_lm_calls,
                "delegated_bytes": observation.delegated_bytes,
                "sandbox_count": observation.sandbox_count,
                "resource_shape": list(observation.resource_shape) if observation.resource_shape else None,
                "error_category": observation.error_category,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
