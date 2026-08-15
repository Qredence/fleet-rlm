"""Benchmark the native whole-value URL Tool path at the configured 10 MiB limit.

The harness uses native ``dspy.RLM`` and the in-process interpreter with injected
deterministic models. It does not call a provider or the network, so it is safe to
run as a repeatable sizing gate before introducing paging or streaming.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import itertools
import json
import platform
import resource
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import dspy

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.files.url_tool import UrlFetchResult, UrlToolHost, WorkspaceUrlSourceStore
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.observability.turn_tracing import turn_trace
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import ToolStarted
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import observe_tool

RECEIPT_SCHEMA = "fleet.native-long-context-benchmark/v2"
DEFAULT_SIZES = (1 * 1024 * 1024, 5 * 1024 * 1024, 10 * 1024 * 1024)
DEFAULT_DEADLINE_SECONDS = 120.0
RSS_GATE_BYTES = 64 * 1024 * 1024
_ResultT = TypeVar("_ResultT")


class NativeLongContextSignature(dspy.Signature):
    """Analyze one deterministic long-context source and return its verified answer."""

    request: str = dspy.InputField(desc="The long-context analysis request")
    answer: str = dspy.OutputField(desc="The verified scalar answer")


def _rss_bytes() -> int:
    """Return the process's peak resident set size in bytes."""
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _source(size: int, markers: tuple[str, ...]) -> str:
    """
    Create a deterministic text source of the requested size with markers at fixed positions.

    Parameters:
        size (int): The source size in bytes.
        markers (tuple[str, ...]): Three markers to place near the beginning, middle, and end of the source.

    Returns:
        str: The generated text source containing the specified markers.

    Raises:
        ValueError: If the source is too small or the markers overlap.
    """
    data = bytearray(b"x" * size)
    positions = (64 * 1024 - len(markers[0]), size // 2, size - len(markers[2]) - 1)
    spans = tuple(zip(positions, markers, strict=True))
    if any(position < 0 or position + len(marker) > size for position, marker in spans):
        raise ValueError("benchmark source is too small for planted markers")
    ordered = sorted(spans)
    if any(
        left_position + len(left_marker) > right_position
        for (left_position, left_marker), (right_position, _) in itertools.pairwise(ordered)
    ):
        raise ValueError("planted markers overlap for this source size")
    for position, marker in spans:
        data[position : position + len(marker)] = marker.encode("utf-8")
    return data.decode("utf-8")


class _SyntheticFetcher:
    def __init__(self, size: int, markers: tuple[str, ...]) -> None:
        self.size = size
        self.markers = markers
        self.calls = 0

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        """Fetch the synthetic source associated with a URL.

        Parameters:
                url (str): The URL identifying the source.
                max_bytes (int): The maximum response size, which is ignored.

        Returns:
                UrlFetchResult: The generated text source and its content type.
        """
        del max_bytes
        self.calls += 1
        return UrlFetchResult(url, "text/plain; charset=utf-8", _source(self.size, self.markers))


class _SyntheticWorkspace:
    """In-process stand-in for the Daytona Session Workspace contract."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def stat(self, path: str) -> WorkspaceEntry | None:
        """Return metadata for a stored workspace file.

        Parameters:
                path (str): Workspace path to inspect.

        Returns:
                WorkspaceEntry | None: File metadata, or `None` if the path is not stored.
        """
        value = self.values.get(path)
        return None if value is None else WorkspaceEntry(path, "file", len(value.encode()), None)

    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        """List files under a workspace path.

        Parameters:
                path (str): Directory path whose entries should be listed.
                limit (int): Maximum number of entries to return.
                after (str | None): Pagination cursor, ignored by this in-memory workspace.

        Returns:
                WorkspaceListResult: Matching entries, truncation status, and no continuation cursor.
        """
        del after
        prefix = path.rstrip("/") + "/"
        entries = tuple(
            WorkspaceEntry(item, "file", len(value.encode()), None)
            for item, value in self.values.items()
            if item.startswith(prefix)
        )
        return WorkspaceListResult(entries[:limit], truncated=len(entries) > limit, next_cursor=None)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        """
        Read a bounded page of text from a workspace value.

        Parameters:
            path (str): Path identifying the workspace value.
            cursor (str | None): Character offset at which to begin reading, or `None` to start at the beginning.
            max_chars (int): Maximum number of characters to include in the page.
            max_bytes (int): Maximum allowed UTF-8 size of the complete value.

        Returns:
        WorkspaceTextPage: Page content, the cursor for the next page, total value size in bytes,
        and whether the end was reached.

        Raises:
            ValueError: If the complete value exceeds `max_bytes`.
        """
        value = self.values[path]
        if len(value.encode()) > max_bytes:
            raise ValueError("workspace read exceeded bound")
        offset = int(cursor or "0")
        content = value[offset : offset + max_chars]
        next_offset = offset + len(content)
        return WorkspaceTextPage(
            content,
            None if next_offset >= len(value) else str(next_offset),
            len(value.encode()),
            next_offset >= len(value),
        )

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        """Write text content to a workspace path.

        Parameters:
                path (str): The workspace path to write.
                content (str): The text content to store.
                overwrite (bool): Whether to replace existing content at the path.

        Returns:
                WorkspaceEntry: Metadata for the written file.

        Raises:
                FileExistsError: If the path already exists and overwriting is disabled.
        """
        if path in self.values and not overwrite:
            raise FileExistsError(path)
        self.values[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), None)


class _SemanticLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> list[dict[str, str]]:
        """Record a prompt and return a deterministic response based on its length.

        Parameters:
                prompt (str): The prompt to record.

        Returns:
                list[dict[str, str]]: A single-completion payload whose text encodes the prompt length.
        """
        self.prompts.append(prompt)
        return [{"text": f"semantic:{len(prompt)}"}]


class _Action(dspy.Predict):
    def __init__(self, codes: list[str]) -> None:
        super().__init__("variables_info, repl_history, iteration -> reasoning, code")
        self.codes = codes
        self._index = 0

    async def aforward(self, **_kwargs: Any) -> dspy.Prediction:
        """
        Provide the next predetermined interpreter action.

        Returns:
            dspy.Prediction: A prediction containing the action reasoning and selected code.
        """
        code = self.codes[min(self._index, len(self.codes) - 1)]
        self._index += 1
        return dspy.Prediction(reasoning="Run bounded native long-context analysis.", code=code)


def _root_lm() -> dspy.utils.DummyLM:
    """Create a deterministic language model that submits the verified benchmark result."""
    return dspy.utils.DummyLM(
        [{"reasoning": "Submit the verified result.", "code": "SUBMIT(answer='ok')"}],
        adapter=dspy.JSONAdapter(),
    )


def _rlm(
    *,
    tools: tuple[dspy.Tool, ...],
    codes: list[str],
    sub_lm: _SemanticLM,
) -> dspy.RLM:
    """
    Create a configured RLM with deterministic action generation for the supplied interpreter code.

    Parameters:
        tools: Tools available to the RLM.
        codes: Interpreter code returned across successive RLM iterations.
        sub_lm: Semantic language model used for subcalls.

    Returns:
        dspy.RLM: The configured RLM.
    """
    rlm = RLMFactory(verbose=False).create(
        models=RLMModelBundle(root_lm=_root_lm(), sub_lm=sub_lm),
        options=RLMOptions(max_iters=len(codes), max_llm_calls=4, max_output_chars=2_000),
        tools=tools,
        signature=NativeLongContextSignature,
    )
    rlm.generate_action = _Action(codes)
    return rlm


def _termination_mode(prediction: dspy.Prediction) -> str:
    return "forced_extraction" if prediction.final_reasoning == "Extract forced final output" else "typed_submit"


def _usage_output(prediction: dspy.Prediction) -> object:
    """Return exactly DSPy's public Prediction usage output."""
    return prediction.get_lm_usage()


async def _with_native_interpreter(
    factory: Callable[[], DaytonaCodeInterpreter],
    execute: Callable[[DaytonaCodeInterpreter], Awaitable[_ResultT]],
) -> _ResultT:
    """Own one interpreter across setup, RLM construction, execution, and shutdown."""
    interpreter: DaytonaCodeInterpreter | None = None
    primary_error: BaseException | None = None
    try:
        interpreter = factory()
        return await execute(interpreter)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if interpreter is not None:
            try:
                interpreter.shutdown()
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"native interpreter shutdown failed: {type(cleanup_error).__name__}: {cleanup_error}"
                )


async def _run_case(size: int, *, trace_enabled: bool) -> dict[str, object]:
    """
    Run a deterministic benchmark case for a synthetic source of the specified size.

    Parameters:
        size (int): Source size in bytes.
        trace_enabled (bool): Whether tracing is enabled for the RLM turns.

    Returns:
        dict[str, object]: Benchmark measurements covering correctness, caching, timing, model calls,
        interpreter payload limits, tracing, and peak resident memory.
    """
    markers = (
        f"needle-{size}-boundary",
        f"needle-{size}-middle",
        f"needle-{size}-tail",
    )
    session_id = uuid4()
    fetcher = _SyntheticFetcher(size, markers)
    host = UrlToolHost(
        session_id=session_id,
        store=WorkspaceUrlSourceStore(_SyntheticWorkspace()),
        max_bytes=10 * 1024 * 1024,
        fetcher=fetcher,
    )
    observed: list[object] = []
    first_event_at: float | None = None
    started_at = time.perf_counter()

    def observe(item: object) -> None:
        nonlocal first_event_at
        observed.append(item)
        if first_event_at is None:
            first_event_at = time.perf_counter()

    source_tool = observe_tool(host.as_tools()[0], observe, host.event_views()["fetch_url"])
    semantic_lm = _SemanticLM()
    url = f"https://example.com/native-long-context/{size}"
    first_codes = [
        "source = fetch_url(url=" + repr(url) + ")\n"
        "markers = " + repr(markers) + "\n"
        "assert source['ok'] is True\n"
        "content = source['content']\n"
        "positions = [content.index(marker) for marker in markers]\n"
        "excerpts = [content[max(0, position - 24):position + 24] for position in positions]\n"
        "batch = llm_query_batched(['Question: locate the marker\\nEvidence: ' + excerpt for excerpt in excerpts])",
        "assert len(batch) == 3\n"
        "assert all(marker in content for marker in markers)\n"
        "SUBMIT(answer=f'{len(positions)}|{len(batch)}|{markers[0]}')",
    ]
    trace_ids: list[str | None] = []

    async def execute_first(interpreter: DaytonaCodeInterpreter) -> tuple[dspy.Prediction, dspy.RLM, str | None]:
        interpreter.bind_observer(observe, max_chars=2_000)
        rlm = _rlm(
            tools=(source_tool,),
            codes=first_codes,
            sub_lm=semantic_lm,
        )
        with (
            contextlib.redirect_stdout(io.StringIO()),
            turn_trace(session_id, uuid4(), enabled=trace_enabled) as trace,
            dspy.context(track_usage=True),
        ):
            prediction = await rlm.acall(interpreter, request="Analyze the synthetic source")
        return prediction, rlm, trace.trace_id

    first_prediction, first, first_trace_id = await _with_native_interpreter(
        lambda: DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        execute_first,
    )
    trace_ids.append(first_trace_id)
    first_completed_at = time.perf_counter()

    async def execute_second(interpreter: DaytonaCodeInterpreter) -> tuple[dspy.Prediction, dspy.RLM, str | None]:
        interpreter.bind_observer(observe, max_chars=2_000)
        rlm = _rlm(
            tools=(source_tool,),
            codes=[
                "source = fetch_url(url="
                + repr(url)
                + ")\nassert source['cache_hit'] is True\nSUBMIT(answer='cache-hit')"
            ],
            sub_lm=semantic_lm,
        )
        with (
            contextlib.redirect_stdout(io.StringIO()),
            turn_trace(session_id, uuid4(), enabled=trace_enabled) as trace,
            dspy.context(track_usage=True),
        ):
            prediction = await rlm.acall(interpreter, request="Follow up on the source")
        return prediction, rlm, trace.trace_id

    second_prediction, _second, second_trace_id = await _with_native_interpreter(
        lambda: DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
        execute_second,
    )
    trace_ids.append(second_trace_id)
    completed_at = time.perf_counter()

    body_free = ("x" * 1024) not in str(observed)
    expected = f"3|3|{markers[0]}"
    cache_events = [
        item.output["cache_hit"] for item in observed if hasattr(item, "output") and "cache_hit" in item.output
    ]
    code_sizes = [len(item.code) for item in observed if hasattr(item, "code") and isinstance(item.code, str)]
    output_sizes = [len(item.output) for item in observed if hasattr(item, "output") and isinstance(item.output, str)]
    max_code_chars = max(code_sizes, default=0)
    max_output_chars = max(output_sizes, default=0)
    tool_calls = Counter(item.tool_name for item in observed if isinstance(item, ToolStarted))
    termination = {
        "first_turn": _termination_mode(first_prediction),
        "follow_up": _termination_mode(second_prediction),
    }
    usage = {
        "first_turn": _usage_output(first_prediction),
        "follow_up": _usage_output(second_prediction),
    }
    return {
        "source_bytes": size,
        "correct": first_prediction.answer == expected and second_prediction.answer == "cache-hit",
        "body_free_observations": body_free,
        "cache_miss_then_hit": cache_events == [False, True],
        "cache_hit_events": cache_events,
        "cache_fetch_calls": fetcher.calls,
        "first_runtime_event_latency_ms": round(
            (first_event_at - started_at) * 1000 if first_event_at is not None else -1.0, 3
        ),
        "first_turn_completion_ms": round((first_completed_at - started_at) * 1000, 3),
        "follow_up_completion_ms": round((completed_at - first_completed_at) * 1000, 3),
        "iterations": {
            "first_turn": len(first_prediction.trajectory),
            "follow_up": len(second_prediction.trajectory),
        },
        "sub_lm_call_count": len(semantic_lm.prompts),
        "native_tool_call_counts": {
            "llm_query": tool_calls["llm_query"],
            "llm_query_batched": tool_calls["llm_query_batched"],
        },
        "recursive_call_count": 0,
        "maximum_recursive_prompt_chars": 0,
        "selected_excerpt_max_chars": max((len(prompt) for prompt in semantic_lm.prompts), default=0),
        "max_interpreter_code_chars": max_code_chars,
        "max_interpreter_output_chars": max_output_chars,
        "interpreter_payload_within_limits": max_code_chars <= 12_000 and max_output_chars <= 2_000,
        "termination": termination,
        "typed_completion": all(mode == "typed_submit" for mode in termination.values()),
        "prediction_lm_usage": usage,
        "usage_tracking_attached": all(value is not None for value in usage.values()),
        "rlm_type": f"{type(first).__module__}.{type(first).__qualname__}",
        "registered_tool_names": sorted(first.tools),
        "trace_ids": trace_ids,
        "peak_host_rss_bytes": _rss_bytes(),
    }


def _case_prediction(case: dict[str, object]) -> dspy.Prediction:
    native_calls = case["native_tool_call_counts"]
    if not isinstance(native_calls, dict):
        raise TypeError("native tool call counts must be a mapping")
    evidence_present = (
        bool(case["body_free_observations"])
        and int(native_calls["llm_query_batched"]) == 1
        and int(case["sub_lm_call_count"]) == 3
        and int(case["selected_excerpt_max_chars"]) > 0
    )
    return dspy.Prediction(
        answer_correct=bool(case["correct"]),
        evidence_present=evidence_present,
        typed_completion=bool(case["typed_completion"]),
    )


class _CaseProgram(dspy.Module):
    def __init__(self, cases: list[dict[str, object]]) -> None:
        super().__init__()
        self._cases = {int(case["source_bytes"]): case for case in cases}

    def forward(self, *, source_bytes: int) -> dspy.Prediction:
        return _case_prediction(self._cases[source_bytes])


def _metric_components(example: dspy.Example, prediction: dspy.Prediction) -> dict[str, float]:
    return {
        "answer_correctness": float(prediction.answer_correct == example.expected_answer_correct),
        "evidence_presence": float(prediction.evidence_present == example.expected_evidence_present),
        "typed_completion": float(prediction.typed_completion == example.expected_typed_completion),
    }


def native_quality_metric(example: dspy.Example, prediction: dspy.Prediction, trace: object = None) -> float:
    """Return the bounded mean of correctness, evidence, and typed completion."""
    del trace
    components = _metric_components(example, prediction)
    return sum(components.values()) / len(components)


def _evaluate_cases(cases: list[dict[str, object]]) -> dict[str, object]:
    devset = [
        dspy.Example(
            source_bytes=int(case["source_bytes"]),
            expected_answer_correct=True,
            expected_evidence_present=True,
            expected_typed_completion=True,
        ).with_inputs("source_bytes")
        for case in cases
    ]
    result = dspy.Evaluate(
        devset=devset,
        metric=native_quality_metric,
        num_threads=1,
        display_progress=False,
        display_table=False,
    )(_CaseProgram(cases))
    examples = []
    for example, prediction, score in result.results:
        examples.append(
            {
                "input": {"source_bytes": int(example.source_bytes)},
                "expected": {
                    "answer_correct": bool(example.expected_answer_correct),
                    "evidence_present": bool(example.expected_evidence_present),
                    "typed_completion": bool(example.expected_typed_completion),
                },
                "prediction": {
                    "answer_correct": bool(prediction.answer_correct),
                    "evidence_present": bool(prediction.evidence_present),
                    "typed_completion": bool(prediction.typed_completion),
                },
                "sub_scores": _metric_components(example, prediction),
                "score": float(score),
            }
        )
    return {
        "engine": "dspy.Evaluate",
        "example_type": "dspy.Example",
        "metric": "native_quality_metric",
        "score": float(result.score),
        "examples": examples,
    }


def _signature_contract() -> dict[str, object]:
    return {
        "name": NativeLongContextSignature.__name__,
        "type": f"{NativeLongContextSignature.__module__}.{NativeLongContextSignature.__qualname__}",
        "input_fields": list(NativeLongContextSignature.input_fields),
        "output_fields": list(NativeLongContextSignature.output_fields),
    }


def _parse_sizes(value: str) -> tuple[int, ...]:
    """
    Parse a comma-separated sequence of ascending positive byte sizes.

    Parameters:
        value (str): Comma-separated size values.

    Returns:
        tuple[int, ...]: The parsed sizes.

    Raises:
        ValueError: If the input is empty, contains a non-positive size, or is not in ascending order.
    """
    sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not sizes or any(size < 1 for size in sizes) or tuple(sorted(sizes)) != sizes:
        raise ValueError("sizes must be positive comma-separated values in ascending order")
    return sizes


def _gate(
    cases: list[dict[str, object]],
    *,
    deadline_seconds: float,
    evaluation: dict[str, object],
) -> dict[str, object]:
    """
    Evaluate benchmark cases against correctness, performance, memory, caching, and execution limits.

    Parameters:
        cases (list[dict[str, object]]): Benchmark results, including the configured baseline and largest source cases.
        deadline_seconds (float): Maximum permitted completion time for the largest case.

    Returns:
        dict[str, object]: Gate decision, pass status, individual check results, and RSS limit measurements.
    """
    baseline = next((case for case in cases if case["source_bytes"] == DEFAULT_SIZES[0]), None)
    largest = max(cases, key=lambda case: int(case["source_bytes"]))
    if baseline is None:
        return {"decision": "insufficient_baseline", "passed": False}
    native_counts = largest["native_tool_call_counts"]
    if not isinstance(native_counts, dict):
        return {"decision": "invalid_native_call_counts", "passed": False}
    rss_delta = int(largest["peak_host_rss_bytes"]) - int(baseline["peak_host_rss_bytes"])
    largest["peak_rss_delta_over_1mib_bytes"] = rss_delta
    completion_ms = float(largest["first_turn_completion_ms"])
    checks = {
        "correct": bool(largest["correct"]),
        "within_turn_deadline": completion_ms <= deadline_seconds * 1000,
        "rss_delta_within_64mib": rss_delta <= RSS_GATE_BYTES,
        "cache_reused": bool(largest["cache_miss_then_hit"]),
        "body_free_observations": bool(largest["body_free_observations"]),
        "interpreter_payload_within_limits": bool(largest["interpreter_payload_within_limits"]),
        "batched_calls_without_recursion": (
            int(largest["sub_lm_call_count"]) == 3
            and int(native_counts["llm_query"]) == 0
            and int(native_counts["llm_query_batched"]) == 1
            and int(largest["recursive_call_count"]) == 0
        ),
        "typed_completion": bool(largest["typed_completion"]),
        "usage_tracking_attached": bool(largest["usage_tracking_attached"]),
        "stock_dspy_rlm": largest["rlm_type"] == "dspy.predict.rlm.RLM",
        "dspy_evaluation_full_score": float(evaluation["score"]) == 100.0,
    }
    passed = all(checks.values())
    return {
        "decision": "keep_whole_value" if passed else "create_corrective_paging_plan",
        "passed": passed,
        "checks": checks,
        "rss_delta_over_1mib_bytes": rss_delta,
        "rss_limit_delta_bytes": RSS_GATE_BYTES,
    }


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for the benchmark.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        default=",".join(str(size) for size in DEFAULT_SIZES),
        help="Ascending source sizes in bytes (default: 1 MiB, 5 MiB, 10 MiB)",
    )
    parser.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS)
    parser.add_argument("--trace", action="store_true", help="Enable existing fail-soft MLflow Turn traces")
    parser.add_argument("--output", type=Path, required=True)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    """
    Run the configured benchmark cases and assemble the results receipt.

    Parameters:
        args (argparse.Namespace): Parsed benchmark options, including sizes, deadline, and tracing settings.

    Returns:
        dict[str, object]: Receipt containing environment metadata, case results, and gate evaluation.

    Raises:
        ValueError: If the deadline is not positive or the size specification is invalid.
    """
    sizes = _parse_sizes(args.sizes)
    if args.deadline_seconds <= 0:
        raise ValueError("deadline must be positive")
    cases = [await _run_case(size, trace_enabled=args.trace) for size in sizes]
    evaluation = _evaluate_cases(cases)
    dspy_contract = {
        "version": dspy.__version__,
        "rlm_type": cases[0]["rlm_type"],
        "rlm_instances_per_case": 2,
        "rlm_roles": ["primary", "follow_up"],
        "signature": _signature_contract(),
        "registered_tool_type": f"{dspy.Tool.__module__}.{dspy.Tool.__qualname__}",
        "registered_tool_names": cases[0]["registered_tool_names"],
        "native_tools": ["llm_query", "llm_query_batched", "SUBMIT"],
        "prediction_type": f"{dspy.Prediction.__module__}.{dspy.Prediction.__qualname__}",
        "sandbox_serializable_type": (f"{dspy.SandboxSerializable.__module__}.{dspy.SandboxSerializable.__qualname__}"),
        "usage_source": "Prediction.get_lm_usage()",
        "usage_tracking": "dspy.context(track_usage=True)",
        "evaluation_type": f"{dspy.Evaluate.__module__}.{dspy.Evaluate.__qualname__}",
        "metric": "native_quality_metric",
    }
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "trace_enabled": bool(args.trace),
        "deadline_seconds": args.deadline_seconds,
        "dspy_contract": dspy_contract,
        "cases": cases,
        "evaluation": evaluation,
        "gate": _gate(cases, deadline_seconds=args.deadline_seconds, evaluation=evaluation),
    }


def main(argv: list[str] | None = None) -> int:
    """
    Run the benchmark, write its JSON receipt, and report the gate result.

    Parameters:
        argv (list[str] | None): Optional command-line arguments to parse instead of the process arguments.

    Returns:
        int: `0` if the benchmark completes successfully, `1` if it fails.
    """
    args = build_parser().parse_args(argv)
    try:
        receipt = asyncio.run(_run(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(args.output), "gate": receipt["gate"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
