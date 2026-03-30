#!/usr/bin/env python3
"""QRE-301 live end-to-end tracing validation harness.

This script runs a live websocket chat turn against fleet-rlm, captures chat and
execution-stream payloads, and verifies persistence in session state + Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from sqlalchemy import and_, func, select, text

from fleet_rlm.integrations.database import (
    Artifact,
    DatabaseManager,
    FleetRepository,
    Run,
    RunStatus,
    RunStep,
)

_DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
_DEFAULT_WORKSPACE_ID = "default"
_DEFAULT_USER_ID = "alice"
_DEFAULT_DOCS_PATH = str(Path(__file__).resolve().parent.parent / "AGENTS.md")
_DEFAULT_OUTPUT_DIR = str(
    Path(__file__).resolve().parent.parent / "output/phase-04/qre-301"
)
_DEFAULT_TIMEOUT_SECONDS = 240
_DEFAULT_PROMPT = (
    "Analyze this repository architecture and summarize key execution flows, "
    "focusing on websocket streaming, execution events, and persistence pathways."
)


@dataclass(slots=True)
class ValidationResult:
    run_id: str
    session_id: str
    workspace_id: str
    user_id: str
    chat_terminal_kind: str
    execution_step_count: int
    run_status: str
    run_step_count: int
    artifact_count: int
    output_dir: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run QRE-301 live end-to-end tracing validation."
    )
    parser.add_argument("--server-url", default=_DEFAULT_SERVER_URL)
    parser.add_argument("--workspace-id", default=_DEFAULT_WORKSPACE_ID)
    parser.add_argument("--user-id", default=_DEFAULT_USER_ID)
    parser.add_argument("--session-id")
    parser.add_argument("--docs-path", default=_DEFAULT_DOCS_PATH)
    parser.add_argument("--prompt", default=_DEFAULT_PROMPT)
    parser.add_argument("--timeout-seconds", type=int, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def _make_ws_url(server_url: str, path: str, query: str = "") -> str:
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_parsed = parsed._replace(
        scheme=scheme,
        path=path,
        params="",
        query=query,
        fragment="",
    )
    return urlunparse(ws_parsed)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


async def _assert_ready_and_runtime_status(
    client: httpx.AsyncClient, output_dir: Path
) -> dict[str, Any]:
    ready_resp = await client.get("/ready")
    ready_resp.raise_for_status()
    ready = ready_resp.json()
    if ready.get("planner") != "ready":
        raise RuntimeError(f"/ready planner is not ready: {ready}")
    if ready.get("database") != "ready":
        raise RuntimeError(f"/ready database is not ready: {ready}")

    runtime_status_resp = await client.get("/api/v1/runtime/status")
    runtime_status_resp.raise_for_status()
    runtime_status = runtime_status_resp.json()
    _write_json(output_dir / "runtime-status.json", runtime_status)
    return runtime_status


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

        if payload.get("type") != "event":
            continue
        kind = payload.get("data", {}).get("kind")
        if kind in {"final", "error", "cancelled"}:
            return events, payload

    raise TimeoutError("Timed out waiting for terminal chat event")


async def _collect_execution_until_completed(
    execution_ws: Any, timeout_seconds: int
) -> list[dict[str, Any]]:
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


async def _persist_artifact_via_command(
    chat_ws: Any,
    *,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> None:
    command_message = {
        "type": "command",
        "command": "write_to_file",
        "args": {
            "path": f"/data/workspaces/{workspace_id}/users/{user_id}/artifacts/"
            f"{session_id}-qre301.txt",
            "content": "QRE-301 artifact persistence probe",
            "append": False,
        },
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
    }
    await chat_ws.send(json.dumps(command_message))
    command_raw = await asyncio.wait_for(chat_ws.recv(), timeout=30)
    command_result = json.loads(command_raw)
    if command_result.get("type") != "command_result":
        raise RuntimeError(f"Unexpected command response envelope: {command_result}")
    if command_result.get("result", {}).get("status") != "ok":
        raise RuntimeError(f"write_to_file command failed: {command_result}")

    # Trigger a local persist pass after command side effects.
    cancel_msg = {
        "type": "cancel",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
    }
    await chat_ws.send(json.dumps(cancel_msg))
    cancel_raw = await asyncio.wait_for(chat_ws.recv(), timeout=30)
    cancel_payload = json.loads(cancel_raw)
    if cancel_payload.get("type") != "cancelled":
        raise RuntimeError(f"Unexpected cancel response: {cancel_payload}")


async def _fetch_session_summary(
    client: httpx.AsyncClient,
    *,
    workspace_id: str,
    user_id: str,
    session_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    response = await client.get("/api/v1/sessions/state")
    response.raise_for_status()
    payload = response.json()
    _write_json(output_dir / "session-state.json", payload)

    for session in payload.get("sessions", []):
        if (
            session.get("workspace_id") == workspace_id
            and session.get("user_id") == user_id
            and session.get("session_id") == session_id
        ):
            if int(session.get("history_turns", 0)) < 1:
                raise RuntimeError(
                    "Session summary found but history_turns < 1; expected persisted chat turn."
                )
            return session

    raise RuntimeError(
        "Target session not found in /api/v1/sessions/state for QRE-301 validation."
    )


async def _verify_db_persistence(
    *,
    database_url: str,
    workspace_id: str,
    user_id: str,
    run_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    db = DatabaseManager(database_url)
    repo = FleetRepository(db)
    identity = await repo.upsert_identity(
        entra_tenant_id=workspace_id,
        entra_user_id=user_id,
        email=f"{user_id}@example.com",
        full_name="QRE 301 Validation",
    )

    verification: dict[str, Any] = {
        "tenant_id": str(identity.tenant_id),
        "run_id_external": run_id,
        "run_found": False,
        "run_status": None,
        "run_step_count": 0,
        "artifact_count": 0,
    }

    try:
        async with db.session() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(identity.tenant_id)},
                )
                run = (
                    await session.execute(
                        select(Run).where(
                            and_(
                                Run.tenant_id == identity.tenant_id,
                                Run.external_run_id == run_id,
                            )
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    raise RuntimeError(
                        f"Run not found in DB for external_run_id={run_id!r}."
                    )
                verification["run_found"] = True
                verification["run_status"] = run.status.value
                if run.status != RunStatus.COMPLETED:
                    raise RuntimeError(
                        f"Run status is {run.status.value!r}, expected 'completed'."
                    )

                run_step_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(RunStep)
                        .where(
                            and_(
                                RunStep.tenant_id == identity.tenant_id,
                                RunStep.run_id == run.id,
                            )
                        )
                    )
                ).scalar_one()
                verification["run_step_count"] = int(run_step_count or 0)
                if verification["run_step_count"] <= 0:
                    raise RuntimeError("No run_steps persisted for validated run.")

                artifact_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(Artifact)
                        .where(
                            and_(
                                Artifact.tenant_id == identity.tenant_id,
                                Artifact.run_id == run.id,
                            )
                        )
                    )
                ).scalar_one()
                verification["artifact_count"] = int(artifact_count or 0)
                if verification["artifact_count"] <= 0:
                    raise RuntimeError(
                        "No artifacts persisted for validated run after write_to_file."
                    )
    finally:
        await db.dispose()

    _write_json(output_dir / "db-verification.json", verification)
    return verification


async def _run_validation(args: argparse.Namespace) -> ValidationResult:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set for DB persistence verification.")

    session_id = args.session_id or f"qre301-{uuid.uuid4().hex[:10]}"
    timestamp_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir) / f"{timestamp_str}-{session_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "X-Debug-Tenant-Id": args.workspace_id,
        "X-Debug-User-Id": args.user_id,
        "X-Debug-Email": f"{args.user_id}@example.com",
        "X-Debug-Name": "QRE 301 Validation",
    }
    ws_headers = list(headers.items())

    async with httpx.AsyncClient(base_url=args.server_url, timeout=30.0) as client:
        await _assert_ready_and_runtime_status(client, output_dir)

        chat_ws_url = _make_ws_url(args.server_url, "/api/v1/ws/chat")
        execution_ws_url = _make_ws_url(
            args.server_url,
            "/api/v1/ws/execution",
            query=(
                f"workspace_id={args.workspace_id}&user_id={args.user_id}"
                f"&session_id={session_id}"
            ),
        )

        async with websockets.connect(
            execution_ws_url,
            additional_headers=ws_headers,
            max_size=4 * 1024 * 1024,
        ) as execution_ws:
            async with websockets.connect(
                chat_ws_url,
                additional_headers=ws_headers,
                max_size=4 * 1024 * 1024,
            ) as chat_ws:
                await chat_ws.send(
                    json.dumps(
                        {
                            "type": "message",
                            "content": args.prompt,
                            "docs_path": args.docs_path,
                            "trace": True,
                            "workspace_id": args.workspace_id,
                            "user_id": args.user_id,
                            "session_id": session_id,
                        }
                    )
                )

                chat_task = asyncio.create_task(
                    _collect_chat_until_terminal(chat_ws, args.timeout_seconds)
                )
                execution_task = asyncio.create_task(
                    _collect_execution_until_completed(
                        execution_ws, args.timeout_seconds
                    )
                )
                chat_events_with_terminal, execution_events = await asyncio.gather(
                    chat_task, execution_task
                )
                chat_events, terminal_chat_payload = chat_events_with_terminal

                _write_jsonl(output_dir / "chat-events.jsonl", chat_events)
                _write_jsonl(output_dir / "execution-events.jsonl", execution_events)

                execution_started = next(
                    (
                        event
                        for event in execution_events
                        if event.get("type") == "execution_started"
                    ),
                    None,
                )
                if execution_started is None:
                    raise RuntimeError("execution_started event missing from stream.")
                run_id = str(execution_started.get("run_id", "")).strip()
                if not run_id:
                    raise RuntimeError(
                        "execution_started event did not include run_id."
                    )

                step_events = [
                    event
                    for event in execution_events
                    if event.get("type") == "execution_step"
                ]
                if not step_events:
                    raise RuntimeError("No execution_step events captured.")

                if any(event.get("run_id") != run_id for event in execution_events):
                    raise RuntimeError(
                        "Execution stream contains inconsistent run_id values."
                    )

                for event in execution_events:
                    if event.get("workspace_id") != args.workspace_id:
                        raise RuntimeError("Execution event workspace_id mismatch.")
                    if event.get("user_id") != args.user_id:
                        raise RuntimeError("Execution event user_id mismatch.")
                    if event.get("session_id") != session_id:
                        raise RuntimeError("Execution event session_id mismatch.")

                terminal_kind = terminal_chat_payload.get("data", {}).get("kind")
                if terminal_kind != "final":
                    raise RuntimeError(
                        f"Terminal chat event kind is {terminal_kind!r}; expected 'final'."
                    )

                await _persist_artifact_via_command(
                    chat_ws,
                    workspace_id=args.workspace_id,
                    user_id=args.user_id,
                    session_id=session_id,
                )

        _ = await _fetch_session_summary(
            client,
            workspace_id=args.workspace_id,
            user_id=args.user_id,
            session_id=session_id,
            output_dir=output_dir,
        )

    db_verification = await _verify_db_persistence(
        database_url=database_url,
        workspace_id=args.workspace_id,
        user_id=args.user_id,
        run_id=run_id,
        output_dir=output_dir,
    )

    summary_lines = [
        "# QRE-301 Live Validation Summary",
        "",
        f"- Timestamp: `{datetime.now(UTC).isoformat()}`",
        f"- Server URL: `{args.server_url}`",
        f"- Workspace/User/Session: `{args.workspace_id}/{args.user_id}/{session_id}`",
        f"- docs_path: `{args.docs_path}`",
        f"- run_id: `{run_id}`",
        f"- chat terminal kind: `{terminal_kind}`",
        f"- execution_step events: `{len(step_events)}`",
        f"- DB run status: `{db_verification['run_status']}`",
        f"- DB run_steps: `{db_verification['run_step_count']}`",
        f"- DB artifacts: `{db_verification['artifact_count']}`",
        "",
        "## Evidence Files",
        f"- `{output_dir / 'chat-events.jsonl'}`",
        f"- `{output_dir / 'execution-events.jsonl'}`",
        f"- `{output_dir / 'runtime-status.json'}`",
        f"- `{output_dir / 'session-state.json'}`",
        f"- `{output_dir / 'db-verification.json'}`",
    ]
    (output_dir / "summary.md").write_text("\n".join(summary_lines) + "\n")

    return ValidationResult(
        run_id=run_id,
        session_id=session_id,
        workspace_id=args.workspace_id,
        user_id=args.user_id,
        chat_terminal_kind=str(terminal_kind),
        execution_step_count=len(step_events),
        run_status=str(db_verification["run_status"]),
        run_step_count=int(db_verification["run_step_count"]),
        artifact_count=int(db_verification["artifact_count"]),
        output_dir=output_dir,
    )


def _print_success(result: ValidationResult) -> None:
    print("QRE-301 validation passed")
    print(f"  run_id: {result.run_id}")
    print(f"  session: {result.workspace_id}/{result.user_id}/{result.session_id}")
    print(f"  terminal chat event: {result.chat_terminal_kind}")
    print(f"  execution steps: {result.execution_step_count}")
    print(
        "  DB persistence: "
        f"status={result.run_status}, steps={result.run_step_count}, "
        f"artifacts={result.artifact_count}"
    )
    print(f"  evidence: {result.output_dir}")


def main() -> int:
    args = _parse_args()
    try:
        result = asyncio.run(_run_validation(args))
    except KeyboardInterrupt:
        print("QRE-301 validation cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"QRE-301 validation failed: {exc}", file=sys.stderr)
        return 1

    _print_success(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
