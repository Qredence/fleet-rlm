"""Run the bounded OOLONG benchmark against a live Fleet API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

if __package__ in {None, ""}:  # direct ``python scripts/benchmarks/evaluate_oolong.py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.benchmarks.oolong import generate_oolong_dataset
from scripts.benchmarks.oolong_scorer import aggregate_scores, score

RECEIPT_SCHEMA = "fleet.oolong-evaluation/v1"
DEFAULT_API_URL = "http://127.0.0.1:8000"
_LIVE_VALUES = frozenset({"1", "true", "yes"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-len", type=int, default=1024)
    parser.add_argument("--max-tasks", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _api_url() -> str:
    return os.environ.get("FLEET_API_URL", DEFAULT_API_URL).rstrip("/")


def _task_request(task: Mapping[str, Any], context_len: int) -> str:
    context = str(task.get("context", ""))[:context_len]
    return (
        "Use the supplied context to answer the question. You may write Python to process it if useful.\n\n"
        f"Context:\n{context}\n\n"
        f"Question:\n{task['question']}\n\n"
        "Return only the final answer."
    )


def _task_score(task: Mapping[str, Any], answer: str) -> float:
    if task.get("task_type") == "classification":
        gold = repr([task["expected_answer"]])
        answer_type = ""
    else:
        gold = f"[{task['expected_numeric']}]"
        answer_type = "ANSWER_TYPE.NUMERIC"
    return score(gold, answer_type, answer)


def _answer_from_chunk(chunk: Mapping[str, Any]) -> str | None:
    if chunk.get("type") == "text-delta" and isinstance(chunk.get("delta"), str):
        return str(chunk["delta"])
    if chunk.get("type") == "text" and isinstance(chunk.get("text"), str):
        return str(chunk["text"])
    if chunk.get("type") == "data-structured-result":
        data = chunk.get("data")
        if isinstance(data, Mapping):
            value = data.get("value")
            if isinstance(value, Mapping) and isinstance(value.get("answer"), str):
                return value["answer"]
            if isinstance(value, str):
                return value
    return None


async def _sse_chunks(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            return
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


async def evaluate_task(client: httpx.AsyncClient, task: Mapping[str, Any], *, context_len: int) -> dict[str, Any]:
    session = await client.post("/api/sessions", json={"title": f"OOLONG {task['id']}"})
    session.raise_for_status()
    session_id = session.json()["id"]
    answer_parts: list[str] = []
    structured_answer: str | None = None
    headers = {"Idempotency-Key": f"oolong-{task['id']}-{uuid4()}"}
    body = {"text": _task_request(task, context_len)}
    async with client.stream("POST", f"/api/sessions/{session_id}/turns", json=body, headers=headers) as response:
        response.raise_for_status()
        async for chunk in _sse_chunks(response):
            answer = _answer_from_chunk(chunk)
            if answer is None:
                continue
            if chunk.get("type") == "data-structured-result":
                structured_answer = answer
            else:
                answer_parts.append(answer)
    answer = structured_answer or "".join(answer_parts)
    return {
        "task_id": str(task["id"]),
        "task_type": str(task.get("task_type", "unknown")),
        "answer_type": "ANSWER_TYPE.NUMERIC" if task.get("task_type") != "classification" else "",
        "context_len": len(str(task.get("context", ""))[:context_len]),
        "score": _task_score(task, answer),
    }


async def evaluate(*, context_len: int, max_tasks: int, api_url: str) -> dict[str, Any]:
    if context_len < 1 or max_tasks < 1:
        raise ValueError("context length and max tasks must be positive")
    tasks = generate_oolong_dataset(count=max_tasks)
    started_at = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []
    failure: dict[str, str] | None = None
    async with httpx.AsyncClient(base_url=api_url, timeout=900.0) as client:
        for task in tasks[:max_tasks]:
            try:
                results.append(await evaluate_task(client, task, context_len=context_len))
            except Exception as exc:  # bounded receipt; details stay in local logs
                failure = {"task_id": str(task["id"]), "error_type": type(exc).__name__}
                break
    return {
        "schema": RECEIPT_SCHEMA,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "api_url": api_url,
        "context_len": context_len,
        "tasks_requested": max_tasks,
        "tasks_completed": len(results),
        "results": results,
        "aggregate": aggregate_scores(results),
        "failure": failure,
    }


async def _run(args: argparse.Namespace) -> int:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise SystemExit("FLEET_LIVE=1 is required")
    receipt = await evaluate(context_len=args.context_len, max_tasks=args.max_tasks, api_url=_api_url())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "tasks_completed": receipt["tasks_completed"]}, sort_keys=True))
    return 0 if receipt["failure"] is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
