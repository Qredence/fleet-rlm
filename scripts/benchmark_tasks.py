"""Notebook helpers for external task benchmarks against an existing Fleet API.

Dataset loading, selection and official verifiers belong to the notebooks. This
module freezes tasks, collects Fleet/native-direct receipts and evaluates saved traces.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anyio
import httpx
from anyio.from_thread import start_blocking_portal

from fleet_rlm.contracts import parse_route, parse_sse
from fleet_rlm.optimize.artifacts import atomic_write
from fleet_rlm.workspace import validate_session_id


def _digest(value: Any) -> str:
    """Hash sorted-key JSON, rejecting nonfinite numbers and propagating serialization errors."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _same_destination(recorded: dict, expected: dict) -> bool:
    """Compare only the destination keys the caller expects, ignoring recorded extras."""
    return expected == {key: (recorded or {}).get(key) for key in expected}


def save_json(path: Path, value: Any) -> None:
    """Atomically replace a JSON file, creating parent directories as needed.

    Serialization errors, including nonfinite numbers, and filesystem errors propagate.
    """
    atomic_write(path, json.dumps(value, indent=2, allow_nan=False).encode() + b"\n")


def freeze_tasks(directory: Path, cases: list[dict], metadata: dict) -> dict:
    """Freeze the exact inputs and host-only expectations once, before execution.

    Write ``bundle.json`` in a new directory and return its cases, metadata, and digest.
    Case IDs must be valid session keys; attachments use basenames and text content.
    Metadata must name the dataset and pin its revision to a lowercase 40-digit hex SHA.

    Raises:
        ValueError: Cases are empty, IDs repeat, or case contents or metadata are invalid.
        FileExistsError: The bundle directory already exists.

    Missing required keys, JSON serialization errors, and filesystem errors propagate.
    """
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("A benchmark requires nonempty, unique case IDs")
    for case in cases:
        validate_session_id(case["case_id"])
        if not isinstance(case["task"], str) or not case["task"].strip():
            raise ValueError("Each task must be nonempty text")
        for name, text in case.get("files", {}).items():
            if Path(name).name != name or not isinstance(text, str):
                raise ValueError("Attachment names must be basenames with text content")
    revision = metadata.get("dataset_revision", "")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError("Resolve the HF dataset to a full commit SHA before freezing")
    if not isinstance(metadata.get("dataset"), str) or not metadata["dataset"].strip():
        raise ValueError("Metadata must name the dataset")
    payload = {"metadata": metadata, "cases": cases}
    bundle = {**payload, "digest": _digest(payload)}
    directory.mkdir(parents=True, exist_ok=False)
    save_json(directory / "bundle.json", bundle)
    return bundle


def load_tasks(directory: Path) -> dict:
    """Read ``bundle.json`` and return it after verifying its cases and metadata digest.

    Raises ValueError on a digest mismatch; file, JSON, and missing-key errors propagate.
    """
    bundle = json.loads((directory / "bundle.json").read_text())
    if bundle["digest"] != _digest({key: bundle[key] for key in ("metadata", "cases")}):
        raise ValueError("Frozen tasks changed")
    return bundle


def _health(client: httpx.Client) -> dict:
    """Return Fleet's health response, requiring an OK service and ready MLflow destination.

    Raise RuntimeError for failed readiness; HTTP and JSON decoding errors propagate.
    """
    response = client.get("/health", timeout=15)
    response.raise_for_status()
    health = response.json()
    if health.get("status") != "ok" or not health.get("mlflow", {}).get("ready"):
        raise RuntimeError("Fleet and its configured MLflow destination must be ready")
    return health


def _turn(
    client: httpx.Client,
    session: str,
    case: dict,
    *,
    timeout_seconds: int | None = None,
    evidence_dir: Path | None = None,
) -> dict:
    """Submit a task and return its outcome, model usage, and any durable Turn receipt.

    Completion requires matching streamed and durable answer, route, and trace ID,
    a nonempty trace ID, and a recognized route. Other outcomes remain uncertain
    unless the durable Turn failed or was cancelled. HTTP and JSON readback errors propagate.
    """
    request = {"task": case["task"], "idempotency_key": case["case_id"]}
    if timeout_seconds is not None:
        request["timeout_seconds"] = timeout_seconds
    if evidence_dir is not None:
        save_json(evidence_dir / "request.json", request)
    if evidence_dir is None:
        response = client.post(f"/sessions/{session}/turn", json=request)
        response.raise_for_status()
        raw = response.text
    else:
        # Keep partial evidence too. Closing a broken stream requests Fleet's native disconnect cancellation.
        with (
            (evidence_dir / "response.sse").open("xb") as saved,
            client.stream("POST", f"/sessions/{session}/turn", json=request) as response,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes():
                saved.write(chunk)
                saved.flush()
        raw = (evidence_dir / "response.sse").read_text()
    events = parse_sse(raw)
    usage = [
        {
            key: call[key]
            for key in ("model", "usage", "duration_ms", "ok", "error_type", "finish_reason")
            if key in call
        }
        for event in events
        if event.get("event") == "lm_end"
        for call in [{**event, **event.get("payload", {})}]
    ]
    outcome = next(
        (event for event in reversed(events) if event.get("event") in {"turn_complete", "error", "turn_cancelled"}),
        {},
    )
    payload = outcome.get("payload", {})
    turn_id = outcome.get("turn_id") or payload.get("turn_id") or payload.get("request_id")
    result = {"status": "uncertain", "usage": usage, "turn_id": turn_id, "output": payload}
    if turn_id:
        response = client.get(f"/sessions/{session}/turns/{turn_id}", timeout=15)
        response.raise_for_status()
        durable = response.json()
        result["durable_outcome"] = durable
        if durable.get("status") == "completed" and outcome.get("event") == "turn_complete":
            matches = all(
                durable.get("result", {}).get(key) == payload.get(key) for key in ("answer", "route", "trace_id")
            )
            if matches and payload.get("trace_id") and parse_route(payload.get("route")) is not None:
                result["status"] = "completed"
        elif durable.get("status") in {"failed", "cancelled"}:
            result["status"] = "failed"
    errors = [str(call.get("error_type", "")) for call in usage if call.get("ok") is False]
    # A completed Turn may tolerate an optional LM failure; only an incomplete outcome stops the run.
    if result["status"] != "completed":
        if any("RateLimit" in error for error in errors):
            result["stop_reason"] = "provider_rate_limit"
        if any(word in error for error in errors for word in ("Connection", "Service", "Unavailable")):
            result["stop_reason"] = "provider_service_failure"
    return result


def _direct_turn(lm: Any, case: dict, *, timeout_seconds: int, evidence_dir: Path) -> dict:
    """Make one native DSPy request, preserving its response, usage, and finish reason."""
    import mlflow
    from dspy.lm15 import Config, Message, Request

    request = Request(
        model=lm.model,
        messages=(Message.user(case["task"]),),
        config=Config(max_tokens=lm.kwargs.get("max_tokens"), temperature=lm.kwargs.get("temperature")),
    )
    save_json(
        evidence_dir / "request.json",
        {
            "task": case["task"],
            "model": request.model,
            "config": asdict(request.config),
            "tools": [],
            "request": asdict(request),
        },
    )

    async def complete():
        # Covers native retries as well as generation; cancellation reaches the async provider client.
        # Match Fleet's native convention: zero disables the generation deadline.
        with anyio.fail_after(timeout_seconds or None):
            return await lm.acall(request)

    # This is an application boundary. Native DSPy autolog owns the model span and its usage.
    with mlflow.start_span(name="benchmark_direct", span_type="CHAIN") as span:
        span.set_inputs({"task": case["task"]})
        span.set_attribute("fleet.workload", "benchmark")
        try:
            # A blocking portal also works from a notebook's already-running event loop.
            with start_blocking_portal() as portal:
                response = portal.call(complete)
        except BaseException as exc:
            save_json(evidence_dir / "response.json", {"error_type": type(exc).__name__, "trace_id": span.trace_id})
            raise
        usage = {key: value for key, value in asdict(response.usage).items() if value is not None}
        payload = {
            "answer": response.text or "",
            "route": "direct",
            "trace_id": span.trace_id,
            "finish_reason": response.finish_reason,
            "reported_model": response.model,
        }
        span.set_outputs(payload)
        save_json(evidence_dir / "response.json", asdict(response))
    mlflow.flush_trace_async_logging()
    return {
        "status": "completed",
        "output": payload,
        "usage": [{"model": response.model, "usage": usage, "ok": True, "finish_reason": response.finish_reason}],
    }


def _comparison_config(config: dict, lm: Any, expected: dict) -> None:
    """Reject model/profile differences or cross-session learning before a comparison call."""
    values = config.get("configuration", {}).get("values", {})
    if values.get("fleet_shared_notes_subpath") != "":
        raise ValueError("Disable shared learned notes for the comparison service")
    if any(values.get(f"fleet_{role}_cache") is not False for role in ("root", "sub")) or lm.cache:
        raise ValueError("Disable response caching for every comparison arm")
    if any(config.get("models", {}).get(role) != lm.model for role in ("root", "sub")):
        raise ValueError("Comparison arms must use the same root/sub generation model")
    normalized = {**values, "execution_mode": expected["execution_mode"]}
    for role in ("root", "sub"):
        normalized[f"fleet_{role}_model"] = expected[f"fleet_{role}_model"]
    if normalized != expected:
        raise ValueError("Comparison service configuration differs from the frozen settings")


def run_tasks(
    bundle_dir: Path,
    run_dir: Path,
    *,
    base_url: str,
    expected_destination: dict,
    allow_live: bool = False,
    timeout: float | None = 1200,
    arm: str | None = None,
    case_ids: list[str] | None = None,
    direct_lm: Any = None,
    expected_configuration: dict | None = None,
    timeout_seconds: int = 0,
) -> dict:
    """Run each case once; incomplete receipts block continuation without replay.

    Create sessions, attach only input files, save receipts, and attempt session cleanup.
    Reuse completed receipts only when cleanup was released and no stop reason was saved.
    Return the run metadata after all cases finish. ``timeout`` is the HTTP timeout in seconds
    (``None`` disables it). A comparison ``timeout_seconds`` of zero disables the generation deadline;
    ``expected_destination`` must identify the serving MLflow mode, tracking URI, and experiment name.

    Raises:
        ValueError: Live execution is disabled, the destination is incomplete, or saved run identity differs.
        FileExistsError: A ``.running`` marker already exists, including one left by a crash.
        RuntimeError: A receipt blocks continuation, a service check or Turn is uncertain,
            a provider stop reason is recorded, or session cleanup is unresolved.

    Per-case exceptions are saved in receipts before stopping with RuntimeError.
    Bundle loading and filesystem errors can propagate directly.
    """
    if not allow_live:
        raise ValueError("Set allow_live=True deliberately to spend model/Daytona resources")
    if any(not expected_destination.get(key) for key in ("mode", "tracking_uri", "experiment_name")):
        raise ValueError("Expected destination must identify the mode, tracking URI and experiment")
    if arm not in {None, "direct", "hybrid", "rlm"} or (
        arm is not None and (direct_lm is None or expected_configuration is None)
    ):
        raise ValueError("A comparison arm requires a matched direct_lm")
    if timeout_seconds != 0 and not 10 <= timeout_seconds <= 3600:
        raise ValueError("Comparison timeout must be 0 (disabled) or 10..3600 seconds")
    bundle = load_tasks(bundle_dir)
    cases = bundle["cases"]
    if case_ids is not None:
        by_id = {case["case_id"]: case for case in cases}
        if not case_ids or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= by_id.keys():
            raise ValueError("Case selection must name unique frozen cases")
        cases = [by_id[case_id] for case_id in case_ids]
    if arm is not None and any(case.get("files") for case in cases):
        raise ValueError("The three-arm pilot accepts self-contained published prompts, not attachments")
    run_dir.mkdir(parents=True, exist_ok=True)
    identity = {
        "bundle_digest": bundle["digest"],
        "base_url": base_url.rstrip("/"),
        "expected_destination": expected_destination,
    }
    if arm is not None:
        identity.update(
            arm=arm,
            timeout_seconds=timeout_seconds,
            expected_configuration_digest=_digest(expected_configuration),
            generation={
                "model": direct_lm.model,
                "max_tokens": direct_lm.kwargs.get("max_tokens"),
                "temperature": direct_lm.kwargs.get("temperature"),
                "num_retries": direct_lm.num_retries,
                "cache": direct_lm.cache,
            },
        )
    run_path = run_dir / "run.json"
    run: dict[str, Any] = (
        json.loads(run_path.read_text()) if run_path.exists() else {**identity, "run_id": uuid.uuid4().hex}
    )
    if any(run[key] != value for key, value in identity.items()):
        raise ValueError("Run directory belongs to different frozen tasks or API")
    # A crash leaves this marker and an uncertain receipt. Never steal a live run's lock.
    lock = run_dir / ".running"
    with lock.open("x"):
        pass
    try:
        run["status"] = "incomplete"
        save_json(run_path, run)
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout, connect=10)) as client:
            for case in cases:
                receipt_path = run_dir / "receipts" / f"{case['case_id']}.json"
                if receipt_path.exists():
                    old = json.loads(receipt_path.read_text())
                    if old["status"] == "completed" and old.get("cleanup") == "released" and not old.get("stop_reason"):
                        continue
                    raise RuntimeError(f"Inspect existing receipt before continuing; never replay: {receipt_path}")
                session = f"bench-{uuid.uuid4().hex}"
                receipt = {"case_id": case["case_id"], "session_id": session, "status": "preflight_failed"}
                started = time.monotonic()
                created_runtime = False
                try:
                    health = _health(client)
                    run.setdefault("service", health)
                    if not _same_destination(health["mlflow"], expected_destination):
                        raise RuntimeError("Fleet tracking destination differs from the notebook settings")
                    if run["service"]["mlflow"] != health["mlflow"]:
                        raise RuntimeError("Fleet tracking destination changed during the run")
                    created_runtime = True  # Even a failed config read can have allocated a runtime.
                    if arm in {"hybrid", "rlm"}:
                        response = client.put(f"/sessions/{session}/mode", json={"mode": arm}, timeout=30)
                        response.raise_for_status()
                    response = client.get(f"/sessions/{session}/config", timeout=30)
                    response.raise_for_status()
                    config = response.json()
                    if arm is not None:
                        assert expected_configuration is not None
                        _comparison_config(config, direct_lm, expected_configuration)
                        if arm in {"hybrid", "rlm"} and config.get("mode") != arm:
                            raise ValueError("Fleet did not apply the selected comparison mode")
                    digest = config.get("configuration", {}).get("digest")
                    if not digest:
                        raise RuntimeError("Fleet did not report an effective configuration digest")
                    run.setdefault("configuration_digest", digest)
                    if run["configuration_digest"] != digest:
                        raise RuntimeError("Effective Fleet configuration changed during the run")
                    receipt.update(configuration=config, configuration_digest=digest)
                    save_json(run_path, run)
                    save_json(receipt_path, receipt)
                    for name, text in case.get("files", {}).items():
                        response = client.post(
                            f"/sessions/{session}/attach", files={"file": (name, text.encode(), "text/plain")}
                        )
                        response.raise_for_status()
                    receipt["status"] = "uncertain"
                    save_json(receipt_path, receipt)  # Persist before the first possible model call.
                    if arm is None:
                        receipt.update(_turn(client, session, case))
                    else:
                        evidence_dir = run_dir / "evidence" / case["case_id"]
                        if arm == "direct":
                            receipt.update(
                                _direct_turn(
                                    direct_lm, case, timeout_seconds=timeout_seconds, evidence_dir=evidence_dir
                                )
                            )
                        else:
                            receipt.update(
                                _turn(
                                    client,
                                    session,
                                    case,
                                    timeout_seconds=timeout_seconds,
                                    evidence_dir=evidence_dir,
                                )
                            )
                    _health(client)
                except Exception as exc:
                    receipt["error"] = (
                        type(exc).__name__ if arm is not None else f"{type(exc).__name__}: {str(exc)[:500]}"
                    )
                    receipt["stop_reason"] = "transport_or_service_failure"
                    if arm == "direct" and receipt["status"] == "uncertain":
                        response_path = run_dir / "evidence" / case["case_id"] / "response.json"
                        if response_path.exists():
                            receipt["output"] = {"trace_id": json.loads(response_path.read_text()).get("trace_id")}
                    if arm in {"hybrid", "rlm"} and receipt["status"] == "uncertain":
                        receipt["cancellation"] = "stream_closed"
                        try:
                            partial = parse_sse((run_dir / "evidence" / case["case_id"] / "response.sse").read_text())
                            turn_id = next(event["turn_id"] for event in partial if event.get("turn_id"))
                            response = client.get(f"/sessions/{session}/turns/{turn_id}", timeout=15)
                            response.raise_for_status()
                            receipt.update(turn_id=turn_id, disconnect_readback=response.json())
                        except (OSError, ValueError, StopIteration, httpx.HTTPError):
                            receipt["disconnect_readback"] = "unresolved"
                finally:
                    receipt["latency_seconds"] = time.monotonic() - started
                    if created_runtime:
                        try:
                            response = client.delete(f"/sessions/{session}", timeout=120)
                            receipt["cleanup"] = "released" if response.status_code in {200, 404} else "unresolved"
                        except httpx.HTTPError:
                            receipt["cleanup"] = "unresolved"
                    save_json(receipt_path, receipt)
                if receipt["status"] in {"uncertain", "preflight_failed"} or receipt.get("stop_reason"):
                    raise RuntimeError(f"Benchmark stopped; inspect {receipt_path}")
                if receipt.get("cleanup") != "released":
                    raise RuntimeError(f"Session cleanup unresolved; inspect {receipt_path}")
        run["status"] = (
            "finished"
            if all((run_dir / "receipts" / f"{case['case_id']}.json").exists() for case in bundle["cases"])
            else "incomplete"
        )
        return run
    finally:
        save_json(run_path, run)
        lock.unlink()


def _benchmark_trace(receipt: dict, case: dict, *, experiment_id: str) -> Any:
    """Read and exclude a delivered benchmark trace from curation, including failed attempts."""
    from fleet_rlm.optimize.evaluation import label_trace, read_evaluation_trace

    trace_id = receipt.get("output", {}).get("trace_id")
    if not trace_id:
        raise RuntimeError("Benchmark receipt has no delivered trace identity")
    read_evaluation_trace(trace_id, experiment_id=experiment_id, allow_error=True)
    labels = {"review_status": "benchmark", "source_group": case["source_group"]}
    label_trace(
        trace_id, review_status="benchmark", source_group=case["source_group"], reviewer="LongHorizonReasoning/longcot"
    )
    return read_evaluation_trace(trace_id, experiment_id=experiment_id, allow_error=True, expected_labels=labels)


def run_comparison_pilot(
    bundle_dir: Path,
    run_dir: Path,
    settings: Any,
    *,
    base_url: str,
    allow_live: bool = False,
    official_score: Callable[[dict, str], float] | None = None,
) -> dict:
    """Run five frozen tasks across direct/strict RLM with a first-pair evidence gate."""
    if not allow_live:
        raise ValueError("Set allow_live=True deliberately for the paired pilot")
    bundle = load_tasks(bundle_dir)
    if bundle["metadata"].get("hf_pin_verified") is not True:
        raise ValueError("Verify the pinned dataset through Hugging Face MCP before running the pilot")
    if len(bundle["cases"]) != 5 or {case["source_group"].split(":")[0] for case in bundle["cases"]} != {
        "logic",
        "cs",
        "chemistry",
        "chess",
        "math",
    }:
        raise ValueError("The pilot requires exactly five tasks, one per LongCoT domain")
    if official_score is None:
        raise ValueError("Provide official_score for first-pair verification before paid calls")
    from fleet_rlm.observability import configure_mlflow_observability
    from fleet_rlm.optimize.artifacts import optimization_code_digest

    target = settings.mlflow_target()
    if target.mode != "local":
        raise ValueError("The authorized pilot uses the local MLflow destination only")
    if os.environ.get("MLFLOW_TRACKING_URI") and os.environ.get("MLFLOW_EXPERIMENT_ID"):
        import mlflow

        if os.environ["MLFLOW_TRACKING_URI"] != target.tracking_uri or mlflow.get_tracking_uri() != target.tracking_uri:
            raise ValueError("Preconfigured MLflow tracking differs; refusing to override it")
        experiment = mlflow.get_experiment(os.environ["MLFLOW_EXPERIMENT_ID"])
        if experiment is None or experiment.name != target.experiment_name:
            raise ValueError("Preconfigured MLflow experiment differs; refusing to override it")
    destination = settings.mlflow_descriptor()
    lm, sub_lm, _, _ = settings.build_lms()
    if lm.model != sub_lm.model:
        raise ValueError("Resolve root/sub generation model mismatch before the pilot")
    domains = ("cs", "chemistry", "chess", "math", "logic")
    cases = sorted(bundle["cases"], key=lambda case: domains.index(case["source_group"].split(":")[0]))
    order = [
        {"case_id": case["case_id"], "arm": arm}
        for index, case in enumerate(cases)
        for arm in (("rlm", "direct") if index % 2 == 0 else ("direct", "rlm"))
    ]
    # Audit the native SDK limit rather than confusing it with the disabled benchmark deadline.
    from dspy.lm15 import Timeouts

    identity = {
        "bundle_digest": bundle["digest"],
        "base_url": base_url.rstrip("/"),
        "settings_digest": settings.configuration()["digest"],
        "code_digest": optimization_code_digest(),
        "helper_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "order": order,
        "arms": ["direct", "rlm"],
        "max_task_attempts": 10,
        "timeout_seconds": 0,
        "native_transport_timeouts": asdict(Timeouts()),
        "dependencies": {package: version(package) for package in ("dspy", "mlflow", "longcot")},
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "comparison.json"
    saved = json.loads(path.read_text()) if path.exists() else {**identity, "status": "incomplete"}
    if any(saved[key] != value for key, value in identity.items()):
        raise ValueError("Comparison directory belongs to another frozen protocol or configuration")
    lock = run_dir / ".running"
    with lock.open("x"):
        pass
    try:
        save_json(path, saved)
        experiment_id = configure_mlflow_observability(
            target.experiment_name,
            target.tracking_uri,
            trace_catalog=settings.mlflow_trace_catalog,
            trace_schema=settings.mlflow_trace_schema,
            trace_table_prefix=settings.mlflow_trace_table_prefix,
            tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
        )
        if experiment_id is None:
            raise RuntimeError("Benchmark tracing experiment is unavailable")
        for index, slot in enumerate(order):
            if (
                optimization_code_digest() != identity["code_digest"]
                or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != identity["helper_digest"]
            ):
                raise RuntimeError("Comparison code changed; stop without another attempt")
            arm_dir = run_dir / slot["arm"]
            case = next(case for case in cases if case["case_id"] == slot["case_id"])
            receipt = {}
            print(f"Benchmark slot {index + 1}/10: {slot['arm']} {slot['case_id']}", flush=True)
            try:
                run_tasks(
                    bundle_dir,
                    arm_dir,
                    base_url=base_url,
                    expected_destination=destination,
                    allow_live=True,
                    timeout=None,
                    arm=slot["arm"],
                    case_ids=[slot["case_id"]],
                    direct_lm=lm,
                    expected_configuration=settings.configuration()["values"],
                    timeout_seconds=0,
                )
            finally:
                receipt_path = arm_dir / "receipts" / f"{slot['case_id']}.json"
                if receipt_path.exists():
                    receipt = json.loads(receipt_path.read_text())
                    if receipt.get("output", {}).get("trace_id"):
                        trace = _benchmark_trace(receipt, case, experiment_id=experiment_id)
                        if slot["arm"] == "direct":
                            model_spans = [span for span in trace.data.spans if span.span_type in {"LLM", "CHAT_MODEL"}]
                            if len(model_spans) != 1:
                                raise RuntimeError("Direct invocation must have exactly one native model span")
            if receipt["status"] != "completed" or receipt.get("cleanup") != "released":
                raise RuntimeError("Benchmark slot did not complete with released resources")
            if slot["arm"] == "rlm" and receipt["output"].get("route") != "rlm":
                raise RuntimeError("Strict RLM returned a different route")
            if not receipt.get("output", {}).get("trace_id"):
                raise RuntimeError("Completed benchmark slot has no trace")
            print(f"Completed {slot['arm']} {slot['case_id']} in {receipt['latency_seconds']:.1f}s", flush=True)
            if index == 1:
                scores = score_comparison_pilot(bundle_dir, run_dir, official_score)
                first_pair = {}
                for arm in identity["arms"]:
                    row = next(row for row in scores["arms"][arm]["cases"] if row["case_id"] == case["case_id"])
                    if "official_score" not in row:
                        raise RuntimeError("First-pair verifier failed; stop without another task attempt")
                    first_pair[arm] = {"trace_id": row["trace_id"], "official_score": row["official_score"]}
                save_json(run_dir / "first-pair.json", first_pair)
                print("First-pair evidence and verifier gate passed; continuing without extra probes.", flush=True)
        saved["status"] = "finished"
        return saved
    finally:
        save_json(path, saved)
        lock.unlink()


def score_tasks(bundle_dir: Path, run_dir: Path, official_score: Callable[[dict, str], float]) -> dict:
    """Score saved answers offline, including explicit failures and unattempted cases.

    Call ``official_score(case, answer)`` only for completed receipts, accepting finite scores
    from zero through one. Verifier exceptions and invalid scores become per-case ``score_error``
    entries. Write and return ``scores.json`` with coverage, accuracy, latency in seconds, and usage.
    All-selected accuracy counts only scores of one as correct, using every frozen case as the denominator.

    Raises ValueError if the run's bundle digest differs; bundle and receipt read errors propagate.
    """
    rows: list[dict[str, Any]] = []
    usage: dict[str, Counter] = defaultdict(Counter)
    bundle = load_tasks(bundle_dir)
    run = json.loads((run_dir / "run.json").read_text())
    if run["bundle_digest"] != bundle["digest"]:
        raise ValueError("Run directory belongs to different frozen tasks")
    for case in bundle["cases"]:
        path = run_dir / "receipts" / f"{case['case_id']}.json"
        receipt: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {"status": "unattempted"}
        row: dict[str, Any] = {"case_id": case["case_id"], **receipt}
        reasons = [call.get("finish_reason") for call in receipt.get("usage", [])]
        if any(reason in {"length", "max_tokens"} for reason in reasons):
            row["truncated"] = True
        else:
            row["truncated"] = False if reasons and all(reasons) else None
        row["trace_id"] = receipt.get("output", {}).get("trace_id")
        destination = run.get("service", {}).get("mlflow", {})
        host = destination.get("workspace_host") or destination.get("tracking_uri", "")
        if row["trace_id"] and host.startswith(("http://", "https://")):
            page = "/ml/experiments/" if destination.get("mode") == "databricks" else "/#/experiments/"
            row["trace_url"] = (
                f"{host.rstrip('/')}{page}{destination['experiment_id']}/traces"
                f"?selectedTraceId={quote(row['trace_id'], safe='')}"
            )
        if receipt["status"] == "completed":
            try:
                score = float(official_score(case, receipt["output"].get("answer", "")))
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("Verifier score must be between zero and one")
                row["official_score"] = score
            except Exception as exc:
                row["score_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        for call in receipt.get("usage", []):
            for key, value in (call.get("usage") or {}).items():
                if type(value) in (int, float):
                    usage[call.get("model", "unknown")][key] += value
        rows.append(row)
    completed = [row for row in rows if row["status"] == "completed"]
    scored = [row for row in completed if "official_score" in row]
    attempted = [row for row in rows if "latency_seconds" in row]
    correct = sum(row["official_score"] == 1 for row in scored)
    summary = {
        "bundle_digest": bundle["digest"],
        "configuration_digest": run.get("configuration_digest"),
        "expected": len(rows),
        "completed": len(completed),
        "scored": len(scored),
        "correct": correct,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "completed_case_accuracy": correct / len(completed) if completed and len(scored) == len(completed) else None,
        "all_selected_case_accuracy": correct / len(rows),
        "mean_official_score_scored": (sum(row["official_score"] for row in scored) / len(scored) if scored else None),
        "total_latency_seconds": sum(row["latency_seconds"] for row in attempted),
        "mean_latency_seconds": (
            sum(row["latency_seconds"] for row in attempted) / len(attempted) if attempted else None
        ),
        "reported_provider_usage": dict(usage),
        "usage_calls_without_tokens": sum(not call.get("usage") for row in rows for call in row.get("usage", [])),
        "cases_without_usage": sum(not row.get("usage") for row in rows if row["status"] != "unattempted"),
        "score_errors": sum("score_error" in row for row in rows),
        "truncated_cases": sum(row["truncated"] is True for row in rows),
        "cases_with_unknown_truncation": sum(row["truncated"] is None for row in rows),
        "complete": run["status"] == "finished" and len(scored) == len(rows),
        "cases": rows,
    }
    save_json(run_dir / "scores.json", summary)
    return summary


def score_comparison_pilot(bundle_dir: Path, run_dir: Path, official_score: Callable[[dict, str], float]) -> dict:
    """Summarize saved arms and paired strict correctness without replaying any calls."""
    bundle = load_tasks(bundle_dir)
    manifest = json.loads((run_dir / "comparison.json").read_text())
    selected_arms = manifest.get("arms", ["direct", "hybrid", "rlm"])
    arms = {
        arm: score_tasks(bundle_dir, run_dir / arm, official_score) if (run_dir / arm / "run.json").exists() else None
        for arm in selected_arms
    }
    scores = {
        arm: {row["case_id"]: row.get("official_score") for row in (report or {}).get("cases", [])}
        for arm, report in arms.items()
    }
    paired = {}
    for arm in selected_arms:
        if arm == "direct":
            continue
        counts = Counter()
        for case in bundle["cases"]:
            direct, fleet = (scores[name].get(case["case_id"]) for name in ("direct", arm))
            if direct is None or fleet is None:
                counts["unavailable"] += 1
            elif (fleet == 1) == (direct == 1):
                counts["ties"] += 1
            else:
                counts["wins" if fleet == 1 else "losses"] += 1
        paired[arm] = dict(counts)
    report = {"bundle_digest": bundle["digest"], "arms": arms, "paired_vs_direct": paired, "diagnostic_only": True}
    save_json(run_dir / "comparison-scores.json", report)
    return report


def evaluate_saved_tasks(
    bundle_dir: Path, run_dir: Path, settings: Any, *, allow_judges: bool = False, official_only: bool = False
) -> Any:
    """Evaluate saved scores/traces with Fleet's suite or the explicit official-only path.

    Read ``scores.json``, label scored traces as benchmarks, and return the MLflow evaluation result.
    Configure MLflow and judge credentials in this process, log source and score artifacts, and write
    ``evaluation.json``. Saved official scores accompany each evaluation row across trace cloning.
    ``official_only=True`` uses a native evaluation dataset and only the deterministic
    scorer, with no judge credentials/models and no custom-scorer security override.

    Raises:
        ValueError: Judge spending is disabled, bundles or tracking destinations differ,
            destination or evaluation configuration is missing, or no scored traces are available.
        RuntimeError: Evaluation returns no result rows or reports scorer errors; its summary is saved first.

    Trace readback, labeling, model-provider, MLflow, and filesystem errors propagate.
    """
    if not allow_judges and not official_only:
        raise ValueError("Set allow_judges=True deliberately to spend evaluation model resources")
    import mlflow
    import pandas as pd
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer

    from fleet_rlm.observability import configure_mlflow_observability
    from fleet_rlm.optimize.evaluation import label_trace, read_evaluation_trace
    from fleet_rlm.optimize.scorers import build_mlflow_scorer_suite

    bundle = load_tasks(bundle_dir)
    run = json.loads((run_dir / "run.json").read_text())
    scored = json.loads((run_dir / "scores.json").read_text())
    if run["bundle_digest"] != bundle["digest"] or scored["bundle_digest"] != bundle["digest"]:
        raise ValueError("Saved scores belong to different frozen tasks")
    recorded = (run.get("service") or {}).get("mlflow")
    if not recorded:
        raise ValueError("Run has no recorded Fleet destination; it stopped before the preflight check")
    target = settings.mlflow_target()
    descriptor = settings.mlflow_descriptor()
    if not _same_destination(recorded, descriptor):
        raise ValueError("Notebook tracking settings differ from the recorded Fleet destination")
    if not target.eval_experiment_name:
        raise ValueError("Configure Fleet's evaluation experiment first")
    settings.prepare_mlflow_auth()
    experiment_id = configure_mlflow_observability(
        target.eval_experiment_name + ("-official-benchmark" if official_only else ""),
        target.tracking_uri,
        trace_catalog=settings.mlflow_trace_catalog,
        trace_schema=settings.mlflow_trace_schema,
        trace_table_prefix=settings.mlflow_eval_trace_table_prefix,
        tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
    )
    if not official_only:
        for name, value in settings.judge_environment().items():
            os.environ[name] = value
    cases = {case["case_id"]: case for case in bundle["cases"]}
    trace_location = descriptor.get("trace_location")
    uc_location = (
        tuple(trace_location[key] for key in ("catalog", "schema", "table_prefix")) if trace_location else None
    )
    evaluation_rows = []
    for row in scored["cases"]:
        if "official_score" not in row:
            continue
        read_evaluation_trace(
            row["trace_id"], experiment_id=run["service"]["mlflow"]["experiment_id"], uc_location=uc_location
        )
        case = cases[row["case_id"]]
        label_trace(
            row["trace_id"],
            review_status="benchmark",
            source_group=case.get("source_group", case["case_id"]),
            expected_answer=case.get("expected_answer"),
            reviewer=bundle["metadata"]["dataset"],
        )
        # Read labels back through the existing delivery contract before Fleet judges use them.
        trace = read_evaluation_trace(
            row["trace_id"],
            experiment_id=run["service"]["mlflow"]["experiment_id"],
            uc_location=uc_location,
            expected_labels={
                key: value
                for key, value in {
                    "review_status": "benchmark",
                    "source_group": case.get("source_group", case["case_id"]),
                    "expected_answer": case.get("expected_answer"),
                }.items()
                if value is not None
            },
        )
        # MLflow may clone serving traces into the evaluation experiment. Row
        # expectations stay attached to the example even when its trace ID changes.
        evaluation_rows.append({"trace": trace, "expectations": {"benchmark_official_score": row["official_score"]}})

    @scorer(name="benchmark_correctness")
    def benchmark_correctness(expectations: dict[str, Any]) -> Feedback:
        """Return the saved official score from row expectations; a missing score raises KeyError."""
        return Feedback(value=expectations["benchmark_official_score"], rationale="Pinned official benchmark verifier")

    if not evaluation_rows:
        raise ValueError("No completed, officially scored traces to evaluate")
    evaluation_data: pd.DataFrame | list[dict] = evaluation_rows
    dataset = None
    if official_only:
        from mlflow.genai.datasets import create_dataset, search_datasets
        from mlflow.genai.scorers import list_scorers

        if experiment_id is None:
            raise RuntimeError("The official-only evaluation experiment is unavailable")
        # Keep the agreed deterministic suite separate from experiments with paid registered judges.
        registered = list_scorers(experiment_id=experiment_id)
        if any(item.name != "benchmark_correctness" for item in registered):
            raise ValueError("Official-only experiment contains another scorer; refusing model judges")
        # Keep this source-controlled @scorer local: default MLflow servers deliberately
        # prohibit registering executable custom scorers. Do not weaken that security setting.
        name = f"benchmark-{bundle['digest']}"
        found = search_datasets(experiment_ids=[experiment_id], filter_string=f"name = '{name}'", max_results=2)
        if len(found) > 1:
            raise ValueError("Ambiguous frozen evaluation dataset")
        records = [
            {"inputs": {"task": case["task"]}, "expectations": {"benchmark_case_id": case["case_id"]}}
            for case in bundle["cases"]
        ]
        expected_records = sorted(map(_digest, records))  # Native merge_records may annotate its input dictionaries.
        if found:
            dataset = found[0]
        else:
            dataset = create_dataset(
                name=name, experiment_id=[experiment_id], tags={"benchmark.bundle_digest": bundle["digest"]}
            )
            dataset.merge_records(records)
        frame = dataset.to_df()
        actual = frame[["inputs", "expectations"]].to_dict("records")
        if sorted(map(_digest, actual)) != expected_records:
            raise ValueError("Native evaluation dataset differs from the frozen tasks")
        by_task = {
            cases[row["case_id"]]["task"]: evaluation
            for row, evaluation in zip(
                [row for row in scored["cases"] if "official_score" in row], evaluation_rows, strict=True
            )
        }
        frame = frame[frame["inputs"].map(lambda inputs: inputs["task"] in by_task)].copy()
        frame["trace"] = [by_task[inputs["task"]]["trace"] for inputs in frame["inputs"]]
        frame["expectations"] = [by_task[inputs["task"]]["expectations"] for inputs in frame["inputs"]]
        evaluation_data = frame
    coverage = {key: scored[key] for key in ("expected", "completed", "scored")}
    with mlflow.start_run(run_name=f"benchmark-{run['run_id']}"):
        mlflow.set_tags(
            {
                "fleet.workload": "benchmark",
                "benchmark.dataset": bundle["metadata"]["dataset"],
                "benchmark.arm": run.get("arm", "fleet"),
                **{f"benchmark.{key}": str(value) for key, value in coverage.items()},
            }
        )
        mlflow.log_dict(bundle["metadata"], "benchmark/source.json")
        mlflow.log_dict(scored, "benchmark/scores.json")
        mlflow.log_input(
            mlflow.data.from_pandas(  # ty: ignore[unresolved-attribute]
                pd.DataFrame([{"case_id": case["case_id"], "task": case["task"]} for case in bundle["cases"]]),
                name=bundle["metadata"]["dataset"],
                source=bundle["metadata"].get("dataset_url"),
            ),
            context="benchmark",
        )
        result = mlflow.genai.evaluate(
            data=evaluation_data,
            scorers=[
                *(
                    []
                    if official_only
                    else build_mlflow_scorer_suite(
                        settings.fleet_judge_model,
                        settings.build_judge_lm() if settings.fleet_judge_model.startswith("openai:/") else None,
                        settings.build_jev_lm(),
                    )
                ),
                benchmark_correctness,
            ],
        )
        frame = result.result_df
        error_columns = (
            []
            if frame is None
            else [name for name in frame.columns if name == "error_message" or name.endswith("/error_message")]
        )
        scoring_complete = (
            frame is not None
            and len(frame) == len(evaluation_rows)
            and not any(frame[name].notna().any() for name in error_columns)
        )
        save_json(
            run_dir / "evaluation.json",
            {
                "run_id": result.run_id,
                "metrics": result.metrics,
                "complete": scoring_complete and bool(scored["complete"]),
                "scoring_complete": scoring_complete,
                **({"dataset_id": dataset.dataset_id, "official_only": True} if dataset is not None else {}),
                **coverage,
            },
        )
        if not scoring_complete:
            raise RuntimeError(
                f"Evaluation incomplete; inspect saved MLflow run {result.run_id} without rerunning tasks"
            )
        return result
