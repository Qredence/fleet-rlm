from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scripts.benchmarks import run_official_oolong as runner


class _StreamResponse:
    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        chunks = [
            {"type": "text-delta", "delta": "wrong"},
            {"type": "data-structured-result", "data": {"value": {"answer": "\\boxed{42}"}}},
            {"type": "data-usage", "data": {"usage": {"iterations": 3}}},
            {"type": "finish", "finishReason": "stop"},
        ]
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}"
        yield "data: [DONE]"


class _StreamContext:
    async def __aenter__(self) -> _StreamResponse:
        return _StreamResponse()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.uploads = 0
        self.turns: list[dict[str, Any]] = []

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        del kwargs
        if path == "/api/attachments":
            self.uploads += 1
            return httpx.Response(
                201, json={"id": f"attachment-{self.uploads}"}, request=httpx.Request("POST", "http://test")
            )
        return httpx.Response(
            201, json={"id": f"session-{len(self.turns)}"}, request=httpx.Request("POST", "http://test")
        )

    async def get(self, path: str) -> httpx.Response:
        assert path == "/api/settings"
        return httpx.Response(
            200,
            json={
                "active_profile": "daytona-bench",
                "scopes": [
                    {
                        "name": "daytona-bench",
                        "fields": [
                            {
                                "path": "llm.root.model",
                                "value": "databricks-qwen35-122b-a10b",
                            },
                            {
                                "path": "rlm.max_iterations",
                                "value": 20,
                            },
                        ],
                    }
                ],
            },
            request=httpx.Request("GET", "http://test"),
        )

    def stream(self, _method: str, _path: str, **kwargs: Any) -> _StreamContext:
        self.turns.append(kwargs)
        return _StreamContext()


def _args(tmp_path: Path) -> Any:
    return runner.build_parser().parse_args(["--output", str(tmp_path / "receipt.json")])


def test_select_rows_groups_context_windows_deterministically() -> None:
    rows = [
        {"id": "b", "context_window_id": "two", "context_len": 10},
        {"id": "a", "context_window_id": "one", "context_len": 10},
        {"id": "skip", "context_window_id": "zero", "context_len": 9},
    ]
    assert [row["id"] for row in runner.select_rows(rows, min_len=10, max_len=10, limit=2)] == ["a", "b"]


def test_load_rows_pages_official_split_reuses_context_count_and_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    encoded_contexts: list[str] = []

    class _Encoder:
        def encode(self, text: str) -> list[int]:
            encoded_contexts.append(text)
            return [0] * len(text)

    pages = [
        {
            "num_rows_total": 3,
            "rows": [
                {"row": {"id": "b", "context_window_id": "shared", "context_window_text": "0123456789"}},
                {"row": {"id": "a", "context_window_id": "shared", "context_window_text": "0123456789"}},
            ],
        },
        {
            "num_rows_total": 3,
            "rows": [{"row": {"id": "skip", "context_window_id": "other", "context_window_text": "short"}}],
        },
    ]

    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=lambda _name: _Encoder()))
    monkeypatch.setattr(
        runner.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: pages.pop(0)),
    )

    rows = runner._load_rows("real", min_len=10, max_len=10, limit=2, source_page_size=2)

    assert [row["id"] for row in rows] == ["a", "b"]
    assert encoded_contexts == ["0123456789"]
    assert "selecting real rows 1-2 of 3" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_evaluate_reuses_attachment_and_prefers_structured_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    client = _Client()
    rows = [
        {
            "id": "one",
            "context_window_id": "shared",
            "context_window_text": "context",
            "question": "q1",
            "context_len": 10,
        },
        {
            "id": "two",
            "context_window_id": "shared",
            "context_window_text": "context",
            "question": "q2",
            "context_len": 10,
        },
    ]

    def score(row: dict[str, Any], answer: str, model: str) -> dict[str, Any]:
        return {
            "id": row["id"],
            "score": answer == "\\boxed{42}",
            "parse_confidence": "high",
            "model": model,
        }

    monkeypatch.setattr(runner, "_official_helpers", lambda _root: (score, lambda *_args: {}, "abc"))

    class _ClientContext:
        async def __aenter__(self) -> _Client:
            return client

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(runner.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())
    receipt = await runner.evaluate(args, rows=rows)

    assert client.uploads == 1
    assert len(client.turns) == 2
    assert receipt["oolong_revision"] == "abc"
    assert receipt["profile"] == "daytona-bench"
    assert receipt["model"] == "databricks-qwen35-122b-a10b"
    assert receipt["aggregate"]["mean_score"] == 1.0
    assert all(turn["json"]["attachment_ids"] == ["attachment-1"] for turn in client.turns)


@pytest.mark.asyncio
async def test_run_row_rejects_stream_without_successful_finish() -> None:
    class _TruncatedResponse(_StreamResponse):
        async def aiter_lines(self) -> AsyncIterator[str]:
            yield 'data: {"type":"data-structured-result","data":{"value":{"answer":"42"}}}'
            yield "data: [DONE]"

    class _TruncatedContext:
        async def __aenter__(self) -> _TruncatedResponse:
            return _TruncatedResponse()

        async def __aexit__(self, *_args: object) -> None:
            return None

    client = _Client()
    client.stream = lambda *_args, **_kwargs: _TruncatedContext()  # type: ignore[method-assign]

    with pytest.raises(runner.TurnStreamError, match="successful finish"):
        await runner.run_row(
            client,  # type: ignore[arg-type]
            {"id": "one", "question": "question"},
            split="real",
            attachment_id="attachment-1",
            skills=[],
        )


@pytest.mark.asyncio
async def test_evaluate_rejects_mismatched_expected_server_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = runner.build_parser().parse_args(
        [
            "--expected-profile",
            "daytona-bench-40",
            "--output",
            str(tmp_path / "receipt.json"),
        ]
    )
    client = _Client()
    monkeypatch.setattr(runner, "_official_helpers", lambda _root: (lambda *_args: {}, lambda *_args: {}, "abc"))

    class _ClientContext:
        async def __aenter__(self) -> _Client:
            return client

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(runner.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())

    with pytest.raises(runner.OolongPreflightError, match=r"daytona-bench-40.*daytona-bench"):
        await runner.evaluate(
            args,
            rows=[
                {
                    "id": "one",
                    "context_window_id": "shared",
                    "context_window_text": "context",
                    "question": "q1",
                    "context_len": 10,
                }
            ],
        )


def test_main_requires_explicit_live_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_LIVE", raising=False)
    assert runner.main(["--output", str(tmp_path / "receipt.json")]) == 2


def test_validate_args_requires_complete_skill_reference(tmp_path: Path) -> None:
    args = _args(tmp_path)
    args.skill_id = "only-id"
    with pytest.raises(runner.OolongPreflightError, match="together"):
        runner._validate_args(args)


def test_answer_for_official_scorer_formats_fleet_scalar_result() -> None:
    assert runner._answer_for_official_scorer("42", split="real") == "\\boxed{42}"
    assert runner._answer_for_official_scorer("42", split="synth") == "Answer: 42"
