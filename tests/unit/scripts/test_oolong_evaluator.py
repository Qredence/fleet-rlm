from __future__ import annotations

import json as jsonlib
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from scripts.benchmarks.evaluate_oolong import evaluate_task


class _StreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _StreamContext:
    def __init__(self, response: _StreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _StreamResponse:
        return self._response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    async def post(self, path: str, *, json: dict[str, Any]) -> httpx.Response:
        self.requests.append(("POST", path, json))
        response = httpx.Response(
            201,
            json={"id": "session-id"},
            request=httpx.Request("POST", "http://test/api/sessions"),
        )
        return response

    def stream(self, method: str, path: str, *, json: dict[str, Any], headers: dict[str, str]) -> _StreamContext:
        self.requests.append((method, path, {"json": json, "headers": headers}))
        chunks = [
            {"type": "text-delta", "delta": "42"},
            {"type": "finish", "finishReason": "stop"},
        ]
        return _StreamContext(_StreamResponse([f"data: {jsonlib.dumps(chunk)}" for chunk in chunks] + ["data: [DONE]"]))


@pytest.mark.asyncio
async def test_evaluate_task_creates_session_and_scores_sse_answer() -> None:
    client = _FakeClient()
    task = {
        "id": "task-1",
        "task_type": "counting",
        "context": "[1, 2, 3]",
        "question": "How many values?",
        "expected_numeric": 42,
    }

    result = await evaluate_task(client, task, context_len=1024)  # type: ignore[arg-type]

    assert result["task_id"] == "task-1"
    assert result["score"] == 1.0
    assert client.requests[0][1] == "/api/sessions"
    assert client.requests[1][1] == "/api/sessions/session-id/turns"
    assert client.requests[1][2]["headers"]["Idempotency-Key"].startswith("oolong-task-1-")
