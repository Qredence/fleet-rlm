"""Credential-free FastAPI/SSE server used by the Phase 4 local harness."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, File, Header, UploadFile
from fastapi.responses import StreamingResponse


def _load_cases(path: Path) -> dict[str, Mapping[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("fake campaign corpus is unavailable") from exc
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError("fake campaign corpus is invalid")
    cases: dict[str, Mapping[str, object]] = {}
    for item in raw:
        identifier = item.get("id")
        if isinstance(identifier, str):
            cases[identifier] = item
    if len(cases) != 12:
        raise ValueError("fake campaign corpus must contain 12 cases")
    return cases


def _write_cleanup(path: Path, trial: str, *, sandbox: bool) -> None:
    event = {
        "event": "turn_cleanup",
        "trial": trial,
        "cleanup": True,
        "created": 1 if sandbox else 0,
        "deleted": 1 if sandbox else 0,
        "sandbox_count": 1 if sandbox else 0,
        "sandbox_seconds": 1 if sandbox else 0,
        "shape": [4, 8, 8] if sandbox else None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        handle.flush()


def build_app(*, corpus: Path, telemetry: Path, recursive: bool) -> FastAPI:
    cases = _load_cases(corpus)
    sessions: dict[UUID, str] = {}
    app = FastAPI(title="fleet-phase4-local-fake")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready", "database": "not_configured"}

    @app.post("/api/attachments", status_code=201)
    async def attachment(attachment: Annotated[UploadFile, File()]) -> dict[str, str]:
        # Consume and discard the bounded body to exercise multipart upload
        # parsing without retaining source content in the fake process.
        while await attachment.read(64 * 1024):
            pass
        return {"id": str(uuid4())}

    @app.post("/api/sessions", status_code=201)
    async def session(body: Mapping[str, object]) -> dict[str, str]:
        title = body.get("title")
        if not isinstance(title, str) or not title.startswith("phase4-"):
            raise ValueError("invalid fake session title")
        raw_label = title.removeprefix("phase4-")
        case_id = next(
            (
                identifier
                for identifier in cases
                if raw_label == identifier
                or raw_label.startswith(f"A-{identifier}-r")
                or raw_label.startswith(f"B-{identifier}-r")
                or raw_label.startswith(f"C-{identifier}-r")
                or raw_label.startswith(f"D-{identifier}-r")
            ),
            raw_label,
        )
        if case_id not in cases:
            raise ValueError("unknown fake case")
        session_id = uuid4()
        sessions[session_id] = case_id
        return {"id": str(session_id)}

    @app.post("/api/sessions/{session_id}/turns")
    async def turn(
        session_id: UUID,
        body: Mapping[str, object],
        x_fleet_phase4_trial: str = Header(default="", alias="x-fleet-phase4-trial"),
    ) -> StreamingResponse:
        case_id = sessions.get(session_id)
        if case_id is None:
            raise ValueError("unknown fake session")
        case = cases[case_id]
        expected_answer = case.get("expected_answer")
        required_evidence = case.get("required_evidence")
        uncertainty = case.get("required_uncertainty")
        if (
            not isinstance(expected_answer, str)
            or not isinstance(required_evidence, list)
            or not isinstance(uncertainty, str)
        ):
            raise ValueError("fake case oracle is invalid")
        # Keep the request body consumed by FastAPI and intentionally ignore
        # its text/attachment content; the fake is a transport/lifecycle test.
        del body
        _write_cleanup(telemetry, x_fleet_phase4_trial, sandbox=recursive)
        usage = {
            "iterations": 1,
            "recursive_call_count": 1 if recursive else 0,
            "observed_lm_usage": {
                "root": {"input_tokens": 32, "output_tokens": 16},
                "child": {"input_tokens": 16, "output_tokens": 8},
            },
            "delegation_metrics": {
                "delegated_input_bytes": 64 if recursive else 0,
                "lm_call_counts": [
                    {"role": "root", "recursive_depth": 0, "count": 1},
                    {"role": "child", "recursive_depth": 1, "count": 1 if recursive else 0},
                ],
                "lm_token_totals": [
                    {"input_tokens": 32, "output_tokens": 16},
                    {"input_tokens": 16, "output_tokens": 8},
                ],
            },
        }
        value = {"answer": expected_answer, "evidence": required_evidence, "uncertainty": uncertainty}
        chunks = (
            {"type": "data-structured-result", "data": {"value": value}},
            {"type": "data-usage", "data": {"usage": usage}},
            {"type": "finish", "finishReason": "stop"},
        )

        async def stream():
            for chunk in chunks:
                yield f"data: {json.dumps(chunk, ensure_ascii=True, separators=(',', ':'))}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"x-vercel-ai-ui-message-stream": "v1"},
        )

    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--recursive", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = build_app(corpus=args.corpus, telemetry=args.telemetry, recursive=args.recursive)
    uvicorn.run(app, host=args.host, port=args.port, reload=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
