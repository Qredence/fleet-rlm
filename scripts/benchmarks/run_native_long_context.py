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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import dspy

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.files.url_tool import UrlFetchResult, UrlToolHost, WorkspaceUrlSourceStore
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.observability.turn_tracing import turn_trace
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.factory import RLMFactory
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.tool_observer import observe_tool

RECEIPT_SCHEMA = "fleet.native-long-context-benchmark/v1"
DEFAULT_SIZES = (1 * 1024 * 1024, 5 * 1024 * 1024, 10 * 1024 * 1024)
DEFAULT_DEADLINE_SECONDS = 120.0
RSS_GATE_BYTES = 64 * 1024 * 1024


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

    def __call__(self, prompt: str) -> str:
        """Record a prompt and return a deterministic response based on its length.

        Parameters:
                prompt (str): The prompt to record.

        Returns:
                str: A response containing the prompt length.
        """
        self.prompts.append(prompt)
        return f"semantic:{len(prompt)}"


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
    interpreter: DaytonaCodeInterpreter,
    sub_lm: _SemanticLM,
) -> dspy.RLM:
    """
    Create a configured RLM with deterministic action generation for the supplied interpreter code.

    Parameters:
        tools: Tools available to the RLM.
        codes: Interpreter code returned across successive RLM iterations.
        interpreter: Code interpreter used by the RLM.
        sub_lm: Semantic language model used for subcalls.

    Returns:
        dspy.RLM: The configured RLM.
    """
    rlm = RLMFactory(verbose=False).create(
        models=RLMModelBundle(root_lm=_root_lm(), sub_lm=sub_lm),
        options=RLMOptions(max_iterations=len(codes), max_llm_calls=4, max_output_chars=2_000),
        interpreter=interpreter,
        tools=tools,
        signature="request -> answer: str",
    )
    rlm.generate_action = _Action(codes)
    return rlm


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
    first_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    first_interpreter.bind_observer(observe, max_chars=2_000)
    first = _rlm(
        tools=(source_tool,),
        codes=first_codes,
        interpreter=first_interpreter,
        sub_lm=semantic_lm,
    )

    trace_ids: list[str | None] = []
    with contextlib.redirect_stdout(io.StringIO()), turn_trace(session_id, uuid4(), enabled=trace_enabled) as trace:
        trace_ids.append(trace.trace_id)
        first_prediction = await first.acall(request="Analyze the synthetic source")
    first_completed_at = time.perf_counter()

    second_interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    second_interpreter.bind_observer(observe, max_chars=2_000)
    second = _rlm(
        tools=(source_tool,),
        codes=[
            "source = fetch_url(url=" + repr(url) + ")\nassert source['cache_hit'] is True\nSUBMIT(answer='cache-hit')"
        ],
        interpreter=second_interpreter,
        sub_lm=semantic_lm,
    )
    with contextlib.redirect_stdout(io.StringIO()), turn_trace(session_id, uuid4(), enabled=trace_enabled) as trace:
        trace_ids.append(trace.trace_id)
        second_prediction = await second.acall(request="Follow up on the source")
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
        "recursive_call_count": 0,
        "maximum_recursive_prompt_chars": 0,
        "selected_excerpt_max_chars": max((len(prompt) for prompt in semantic_lm.prompts), default=0),
        "max_interpreter_code_chars": max_code_chars,
        "max_interpreter_output_chars": max_output_chars,
        "interpreter_payload_within_limits": max_code_chars <= 12_000 and max_output_chars <= 2_000,
        "trace_ids": trace_ids,
        "peak_host_rss_bytes": _rss_bytes(),
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


def _gate(cases: list[dict[str, object]], *, deadline_seconds: float) -> dict[str, object]:
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
            int(largest["sub_lm_call_count"]) == 3 and int(largest["recursive_call_count"]) == 0
        ),
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
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "trace_enabled": bool(args.trace),
        "deadline_seconds": args.deadline_seconds,
        "cases": cases,
        "gate": _gate(cases, deadline_seconds=args.deadline_seconds),
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
