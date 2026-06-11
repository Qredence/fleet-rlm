#!/usr/bin/env python3
"""Run RLM browser validation scenarios via the same WebSocket path as the Workbench UI."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output" / "browser-rlm-test"
DEFAULT_SERVER = "http://127.0.0.1:8000"
PDF_PATH = str(ROOT / "output" / "the-enterprise-in-2030-report-copy.pdf")
LOG_PATH = str(ROOT / "output" / "browser-rlm-test" / "oolong-002-logs.txt")

SCENARIOS: dict[str, dict[str, Any]] = {
    "c": {
        "name": "OOLONG log count",
        "execution_mode": "rlm_only",
        "content": (
            f"Using the log file at {LOG_PATH}:\n\n"
            "Each line has format: timestamp [LEVEL] service: message.\n\n"
            "How many log lines have level 'CRITICAL' AND service 'cache-layer'? "
            "Answer with just the number. Use Python in the REPL to count — do not estimate."
        ),
        "context_paths": [LOG_PATH],
        "expected_routing": {"forced_rlm"},
        "gold_contains": ["2"],
        "max_iterations_budget": 8,
        "max_llm_query_budget": 0,
        "timeout_seconds": 720,
    },
    "d": {
        "name": "DSPy URL document RLM",
        "execution_mode": "auto",
        "content": (
            "Summarize the key design decisions in https://dspy.ai/diving-deeper/rlm/ — "
            "especially how variable space differs from token space, and when to use "
            "llm_query vs plain Python. Use the REPL to explore the document variable; "
            "cite specific section headings."
        ),
        "expected_routing": {"url_document_rlm"},
        "gold_contains": ["variable", "llm_query"],
        "max_iterations_budget": 4,
        "max_llm_query_budget": 8,
        "timeout_seconds": 360,
    },
    "a": {
        "name": "Enterprise 2030 PDF needle",
        "execution_mode": "auto",
        "content": (
            f"Using the document at {PDF_PATH}:\n\n"
            "What is the exact quote from Chad Gates, Managing Director, Pronto Software? "
            "Return the quote verbatim and attribute it to the speaker."
        ),
        "context_paths": [PDF_PATH],
        "expected_routing": {"large_context_rlm"},
        "gold_contains": [
            "By 2030, insight will be everywhere",
            "business intelligence system",
        ],
        "max_iterations_budget": 15,
        "max_llm_query_budget": 10,
        "timeout_seconds": 600,
    },
    "b": {
        "name": "Repo router discovery",
        "execution_mode": "rlm_only",
        "content": (
            "Analyze https://github.com/qredence/fleet-rlm on branch main.\n\n"
            "Write Python code in the sandbox to programmatically discover every FastAPI "
            "router module under src/fleet_rlm/api/routers/ and list each router's registered "
            "path prefix. Do not guess — inspect the filesystem and source code.\n\n"
            "Return a markdown table: router file | prefix | notable endpoints (max 3 per router)."
        ),
        "repo_url": "https://github.com/qredence/fleet-rlm.git",
        "repo_ref": "main",
        "expected_routing": {"forced_rlm"},
        "gold_contains": ["runtime", "ws"],
        "max_iterations_budget": 20,
        "max_llm_query_budget": 15,
        "timeout_seconds": 900,
    },
}


def _make_ws_url(server_url: str, path: str, query: str = "") -> str:
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme, path=path, query=query, params="", fragment=""))


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    data = _event_data(event)
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else data


def _is_terminal_chat_event(event: dict[str, Any]) -> bool:
    if event.get("type") == "execution_completed":
        return True
    if event.get("type") != "event":
        return False
    data = _event_data(event)
    frame_kind = str(data.get("kind") or "")
    if frame_kind == "execution_completed":
        return True
    legacy_kind = str(data.get("kind") or data.get("source_type") or "")
    return legacy_kind in {"done", "error", "cancelled", "final"}


def _step_payload(event: dict[str, Any]) -> dict[str, Any]:
    step = event.get("step")
    if not isinstance(step, dict):
        return {}
    output = step.get("output")
    if not isinstance(output, dict):
        return {}
    payload = output.get("payload")
    return payload if isinstance(payload, dict) else {}


def _extract_routing_decision(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        event_type = event.get("type")
        if event_type not in {"event", "execution_completed", "execution_step"}:
            continue
        data = _event_data(event)
        payload = _event_payload(event) or _step_payload(event)
        rd = payload.get("routing_decision")
        if rd:
            return str(rd)
        runtime = payload.get("runtime")
        if isinstance(runtime, dict) and runtime.get("routing_decision"):
            return str(runtime["routing_decision"])
        text = str(data.get("text") or payload.get("text") or (event.get("step") or {}).get("label") or "")
        match = re.search(r"route[d]?:?\s*(\w+)", text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _count_repl_iterations(events: list[dict[str, Any]], execution_events: list[dict[str, Any]]) -> int:
    for event in reversed(events + execution_events):
        payload = _event_payload(event) or _step_payload(event)
        trajectory = payload.get("trajectory")
        if isinstance(trajectory, dict):
            steps = trajectory.get("steps")
            if isinstance(steps, list) and steps:
                return len(steps)
        if event.get("type") == "execution_completed":
            summary = event.get("summary")
            if isinstance(summary, dict):
                artifact = summary.get("final_artifact")
                if isinstance(artifact, dict):
                    nested = artifact.get("value")
                    if isinstance(nested, dict):
                        nested_traj = nested.get("trajectory")
                        if isinstance(nested_traj, dict) and isinstance(nested_traj.get("steps"), list):
                            return len(nested_traj["steps"])

    count = 0
    for event in events + execution_events:
        if event.get("type") not in {"event", "execution_step"}:
            continue
        step = event.get("step") if event.get("type") == "execution_step" else None
        if isinstance(step, dict) and step.get("type") == "repl":
            count += 1
            continue
        payload = _event_payload(event)
        phase = str(payload.get("phase") or "")
        if phase in {"rlm_tool_call", "rlm_reasoning"}:
            count += 1
    return count


def _count_llm_queries(events: list[dict[str, Any]], execution_events: list[dict[str, Any]]) -> int:
    """Count llm_query/sub_rlm invocations in REPL code, not prose mentions."""
    total = 0
    for event in events + execution_events:
        payload = _event_payload(event) or _step_payload(event)
        trajectory = payload.get("trajectory")
        if isinstance(trajectory, dict):
            steps = trajectory.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    code = str(step.get("code") or "")
                    total += len(re.findall(r"\bllm_query(?:_batched)?\s*\(", code))
                    total += len(re.findall(r"\bsub_rlm(?:_batched)?\s*\(", code))
    return total


def _extract_final_answer(events: list[dict[str, Any]], terminal: dict[str, Any]) -> str:
    parts: list[str] = []
    for event in reversed(events):
        if event.get("type") not in {"event", "execution_completed", "execution_step"}:
            continue
        data = _event_data(event)
        frame_kind = str(data.get("kind") or event.get("type") or "")
        payload = _event_payload(event) or _step_payload(event)
        step = event.get("step") if isinstance(event.get("step"), dict) else {}
        step_output = step.get("output") if isinstance(step.get("output"), dict) else {}
        if frame_kind in {"execution_completed", "final", "done", "text", "execution_step"}:
            parts.append(str(step_output.get("text") or data.get("text") or payload.get("response_preview") or ""))
        artifact = payload.get("final_artifact")
        if isinstance(artifact, dict):
            value = artifact.get("value")
            if isinstance(value, dict):
                parts.append(str(value.get("text") or value.get("summary") or ""))
    terminal_data = _event_data(terminal) if terminal.get("type") == "event" else terminal
    summary = (
        terminal.get("summary")
        or terminal.get("run_summary")
        or terminal_data.get("summary")
        or _event_payload(terminal).get("summary")
        or {}
    )
    if isinstance(summary, dict):
        parts.append(str(summary.get("answer") or summary.get("response") or summary.get("response_preview") or ""))
        artifact = summary.get("final_artifact")
        if isinstance(artifact, dict):
            value = artifact.get("value")
            if isinstance(value, dict):
                parts.append(str(value.get("text") or value.get("summary") or ""))
    return "\n".join(p for p in parts if p).strip()


@dataclass
class ScenarioResult:
    scenario_id: str
    name: str
    passed: bool
    routing_decision: str | None
    routing_ok: bool
    answer_ok: bool
    iterations: int
    iterations_ok: bool
    llm_query_count: int
    llm_query_ok: bool
    latency_ms: int
    final_answer: str
    errors: list[str] = field(default_factory=list)
    output_dir: Path = field(default_factory=Path)


async def _collect_chat_until_terminal(
    chat_ws: Any, timeout_seconds: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        raw = await asyncio.wait_for(chat_ws.recv(), timeout=remaining)
        payload = json.loads(raw)
        events.append(payload)
        if payload.get("type") == "error":
            raise RuntimeError(f"Chat websocket error: {payload}")
        if _is_terminal_chat_event(payload):
            return events, payload
    raise TimeoutError("Timed out waiting for terminal chat event")


async def _collect_execution_until_completed(execution_ws: Any, timeout_seconds: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        raw = await asyncio.wait_for(execution_ws.recv(), timeout=remaining)
        payload = json.loads(raw)
        events.append(payload)
        if payload.get("type") == "execution_completed":
            return events
    raise TimeoutError("Timed out waiting for execution_completed event")


async def _run_scenario(
    *,
    server_url: str,
    scenario_id: str,
    spec: dict[str, Any],
    output_root: Path,
    headers: list[tuple[str, str]],
) -> ScenarioResult:
    session_id = f"rlm-val-{scenario_id}-{uuid.uuid4().hex[:8]}"
    scenario_dir = output_root / f"scenario-{scenario_id}-{session_id}"
    scenario_dir.mkdir(parents=True, exist_ok=True)

    message: dict[str, Any] = {
        "type": "message",
        "content": spec["content"],
        "trace": True,
        "trace_mode": "compact",
        "execution_mode": spec["execution_mode"],
        "session_id": session_id,
    }
    if spec.get("context_paths"):
        message["context_paths"] = spec["context_paths"]
    if spec.get("repo_url"):
        message["repo_url"] = spec["repo_url"]
    if spec.get("repo_ref"):
        message["repo_ref"] = spec["repo_ref"]

    chat_ws_url = _make_ws_url(server_url, "/api/v1/ws/execution")

    errors: list[str] = []
    chat_events: list[dict[str, Any]] = []
    execution_events: list[dict[str, Any]] = []
    started = time.perf_counter()
    try:
        async with websockets.connect(
            chat_ws_url,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
        ) as chat_ws:
            await chat_ws.send(json.dumps(message))
            chat_events, terminal = await _collect_chat_until_terminal(chat_ws, spec["timeout_seconds"])
            execution_events = [
                event for event in chat_events if event.get("type") in {"execution_step", "execution_completed"}
            ]
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if chat_events:
            _write_jsonl(scenario_dir / "chat-events.jsonl", chat_events)
        if execution_events:
            _write_jsonl(scenario_dir / "execution-events.jsonl", execution_events)
        return ScenarioResult(
            scenario_id=scenario_id,
            name=spec["name"],
            passed=False,
            routing_decision=None,
            routing_ok=False,
            answer_ok=False,
            iterations=0,
            iterations_ok=False,
            llm_query_count=0,
            llm_query_ok=False,
            latency_ms=latency_ms,
            final_answer="",
            errors=[str(exc) or type(exc).__name__],
            output_dir=scenario_dir,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    _write_jsonl(scenario_dir / "chat-events.jsonl", chat_events)
    _write_jsonl(scenario_dir / "execution-events.jsonl", execution_events)
    _write_json(scenario_dir / "terminal.json", terminal)

    routing = _extract_routing_decision(chat_events)
    routing_ok = routing in spec["expected_routing"] if routing else False
    if not routing_ok:
        errors.append(f"routing_decision={routing!r}, expected one of {sorted(spec['expected_routing'])}")

    final_answer = _extract_final_answer(chat_events, terminal)
    answer_lower = final_answer.lower()
    answer_ok = all(fragment.lower() in answer_lower for fragment in spec["gold_contains"])
    if not answer_ok:
        errors.append(f"answer missing gold fragments: {spec['gold_contains']}")

    iterations = _count_repl_iterations(chat_events, execution_events)
    iterations_ok = iterations <= spec["max_iterations_budget"]
    if not iterations_ok:
        errors.append(f"iterations={iterations} exceeds budget {spec['max_iterations_budget']}")

    llm_query_count = _count_llm_queries(chat_events, execution_events)
    llm_query_ok = llm_query_count <= spec["max_llm_query_budget"]
    if not llm_query_ok:
        errors.append(f"llm_query/sub_rlm mentions={llm_query_count} exceeds budget {spec['max_llm_query_budget']}")

    result = ScenarioResult(
        scenario_id=scenario_id,
        name=spec["name"],
        passed=routing_ok and answer_ok and iterations_ok and llm_query_ok,
        routing_decision=routing,
        routing_ok=routing_ok,
        answer_ok=answer_ok,
        iterations=iterations,
        iterations_ok=iter_ok if (iter_ok := iterations_ok) else False,
        llm_query_count=llm_query_count,
        llm_query_ok=llm_query_ok,
        latency_ms=latency_ms,
        final_answer=final_answer[:4000],
        errors=errors,
        output_dir=scenario_dir,
    )
    _write_json(scenario_dir / "result.json", result.__dict__)
    return result


async def _main_async(args: argparse.Namespace) -> list[ScenarioResult]:
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(base_url=args.server_url, timeout=30.0) as client:
        status = (await client.get("/api/v1/runtime/status")).json()
        _write_json(output_root / "preflight-runtime-status.json", status)
        if not status.get("ready"):
            raise RuntimeError("Runtime is not ready; run LM and Daytona connection tests first.")

    headers = [
        ("X-Debug-Tenant-Id", "default"),
        ("X-Debug-User-Id", "alice"),
        ("X-Debug-Email", "alice@example.com"),
        ("X-Debug-Name", "RLM Browser Validation"),
    ]

    order = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    results: list[ScenarioResult] = []
    for scenario_id in order:
        if scenario_id not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_id}")
        print(f"\n=== Running scenario {scenario_id}: {SCENARIOS[scenario_id]['name']} ===")
        result = await _run_scenario(
            server_url=args.server_url,
            scenario_id=scenario_id,
            spec=SCENARIOS[scenario_id],
            output_root=output_root,
            headers=headers,
        )
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(
            f"{status} | route={result.routing_decision} | "
            f"iters~={result.iterations} | llm_refs={result.llm_query_count} | "
            f"{result.latency_ms}ms"
        )
        if result.errors:
            for err in result.errors:
                print(f"  - {err}")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenarios": [r.__dict__ for r in results],
        "all_passed": all(r.passed for r in results),
    }
    _write_json(output_root / "summary.json", summary)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RLM browser validation scenarios.")
    parser.add_argument("--server-url", default=DEFAULT_SERVER)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--scenarios",
        default="c,d,a,b",
        help="Comma-separated scenario ids: c,d,a,b",
    )
    args = parser.parse_args()
    results = asyncio.run(_main_async(args))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
