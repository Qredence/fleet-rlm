"""Live Daytona proof for autonomous, post-commit Memory Candidates."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config import Settings
from fleet_rlm.daytona.interpreter import sync_sandbox
from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest
from fleet_rlm.files.volume_paths import volume_paths_from_settings
from tests.live.backend._database import upgrade_to_head
from tests.live.backend.test_fleet_rlm_daytona_mvp import (
    _SECRET_NAMES,
    _assert_secret_free,
    _assert_sse_stop,
    _live_settings,
    _sandbox_environment_names,
    _sse_chunks,
    _strict_cleanup,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1200)]

_RECEIPT_SCHEMA = "fleet.qre140-memory-candidate-proof/v1"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_PROBE_LEARNING = "operator prefers certified receipts under .scratch/p15 (probe ZEPHYR-4582)"


class _TrackingCleanup(SimpleNamespace):
    pass


def _candidate_metadata(settings: Settings) -> dict[str, object]:
    def _git(*args: str) -> str:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    return {
        "sha": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "tracked_tree_clean": not bool(_git("status", "--porcelain", "--untracked-files=no")),
        "versions": {
            "python": sys.version.split()[0],
            "dspy": importlib.metadata.version("dspy"),
            "daytona": importlib.metadata.version("daytona"),
        },
        "lockfile_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "models": {"root": settings.root_model, "sub": settings.sub_model},
    }


def _chunk_histogram(chunks: list[dict[str, Any]]) -> dict[str, object]:
    """Bounded metadata-only stream summary for live failure triage."""

    by_type: dict[str, int] = {}
    tool_events: dict[str, int] = {}
    tool_calls: list[dict[str, str | None]] = []
    for chunk in chunks:
        kind = str(chunk.get("type") or "unknown")
        by_type[kind] = by_type.get(kind, 0) + 1
        name = chunk.get("toolName")
        if name:
            tool_events[str(name)] = tool_events.get(str(name), 0) + 1
        if kind.startswith("tool-"):
            tool_calls.append(
                {
                    "type": kind,
                    "toolName": str(name) if name else None,
                    "toolCallId": str(chunk.get("toolCallId")) if chunk.get("toolCallId") else None,
                }
            )
    return {
        "chunk_count": len(chunks),
        "by_type": dict(sorted(by_type.items())),
        "tool_events": dict(sorted(tool_events.items())),
        "tool_calls": tool_calls,
    }


def _failure_receipt(
    candidate: dict[str, object],
    *,
    started_at: str,
    category: str,
    phase: str,
    stream: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": _RECEIPT_SCHEMA,
        "candidate": {key: candidate[key] for key in ("sha", "branch", "tracked_tree_clean")},
        "timing": {"started_at": started_at, "finished_at": datetime.now(UTC).isoformat()},
        "failure": {"category": category, "phase": phase},
        "stream": stream or {},
        "passed": False,
    }


def _write_receipt(payload: dict[str, object]) -> None:
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if raw_path:
        path = Path(raw_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + chr(10), encoding="utf-8")


def _tool_chunks(chunks: list[dict[str, Any]], tool_name: str, chunk_type: str) -> list[dict[str, Any]]:
    return [chunk for chunk in chunks if chunk.get("type") == chunk_type and chunk.get("toolName") == tool_name]


def _paired_tool_chunks(
    chunks: list[dict[str, Any]], tool_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair one tool's input chunks with their output/error chunks by toolCallId.

    The SSE projection carries ``toolName`` only on ``tool-input-available`` frames
    (matching both the in-repo ``ToolOutputAvailable`` model and the AI SDK stream
    protocol); outputs pair to their tool strictly through ``toolCallId``.
    """

    inputs = _tool_chunks(chunks, tool_name, "tool-input-available")
    call_ids = {str(chunk.get("toolCallId")) for chunk in inputs}
    outputs = [
        chunk
        for chunk in chunks
        if chunk.get("type") == "tool-output-available" and str(chunk.get("toolCallId")) in call_ids
    ]
    errors = [
        chunk
        for chunk in chunks
        if chunk.get("type") == "tool-output-error" and str(chunk.get("toolCallId")) in call_ids
    ]
    return inputs, outputs, errors


def _live_qre140_settings(tmp_path: Path) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "rlm_autonomous_memory_categories": ("operator preference",),
            "rlm_max_iterations": 4,
            "rlm_max_llm_calls": 8,
            "rlm_execution_timeout_s": 560,
            "turn_timeout_seconds": 560,
            "run_heartbeat_seconds": 10,
            "run_stale_after_seconds": 600,
            # Tracing is outside this proof's certification surface; the local
            # trace server may be down and its retry storm slows startup.
            "mlflow_tracing_enabled": False,
        }
    )
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'live-qre140.db').resolve()}"
    policy = settings.model_copy(update={"database_url": database_url, "volume_name": f"fleet-rlm-qre140-{uuid4()}"})
    upgrade_to_head(database_url)
    return policy


def test_live_memory_candidate_promotes_after_commit_and_retrieves_on_next_turn(tmp_path: Path) -> None:
    settings = _live_qre140_settings(tmp_path)
    app = create_app(settings=settings)
    started_at = datetime.now(UTC).isoformat()
    candidate = _candidate_metadata(settings)
    phase = "startup"
    sandbox_ids: set[str] = set()
    cleanup_failures: tuple[str, ...] = ()
    usage_gathered: list[dict[str, Any]] = []
    first_chunks: list[dict[str, Any]] = []
    second_chunks: list[dict[str, Any]] = []
    final_text = ""
    memory_id = ""
    run_ids: list[str] = []

    try:
        with TestClient(app) as client:
            resources = app.state.runtime_inventory.run_environment_resources
            preparation = app.state.runtime_inventory.run_preparation
            assert resources is not None and preparation is not None
            portal = client.portal
            assert portal is not None
            portal_loop = portal.call(lambda: asyncio.get_running_loop())
            try:
                phase = "first_turn"
                created = client.post("/api/sessions", json={"title": "QRE-140 live Memory Candidate proof"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                first = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "text": (
                            "First call propose_memory exactly once with"
                            f' key_learning={_PROBE_LEARNING!r} and category="operator preference"'
                            " in one code cell without any SUBMIT."
                            " Only after reading that observation, SUBMIT(a short answer)"
                            " alone in the next code cell. Use no other tools."
                        )
                    },
                    headers={"Idempotency-Key": f"live-qre140-first-{uuid4()}"},
                )
                assert first.status_code == 200
                first_chunks, first_done = _sse_chunks(first)
                run_ids.append(str(next(chunk["messageId"] for chunk in first_chunks if chunk["type"] == "start")))
                assert first_done == 1
                _assert_sse_stop(first_chunks, label="qre140_propose_turn")
                usage_gathered.extend(chunk for chunk in first_chunks if chunk.get("type") == "data-usage")

                proposal_inputs, proposal_outputs, proposal_errors = _paired_tool_chunks(first_chunks, "propose_memory")
                assert proposal_errors == []
                assert len(proposal_inputs) == len(proposal_outputs) == 1
                assert proposal_inputs[0]["input"]["category"] == "operator preference"
                assert proposal_inputs[0]["input"]["supersedes"] is False
                proposal_output = proposal_outputs[0]["output"]
                assert proposal_output["ok"] is True
                assert len(str(proposal_output["candidate_id"])) == 12
                assert proposal_output["candidate_count"] == 1
                assert proposal_output["category"] == "operator preference"
                assert int(proposal_output["byte_size"]) > 0

                phase = "read_promoted_memory"
                binding = portal.call(resources.bindings.get, session_id)
                assert binding is not None and binding.sandbox_id is not None
                sandbox_ids.add(binding.sandbox_id)
                sandbox = sync_sandbox(portal.call(resources.platform.get, binding.sandbox_id), portal_loop)
                paths = volume_paths_from_settings(settings)
                memory_store = __import__(
                    "fleet_rlm.daytona.workspace_memory", fromlist=["DaytonaWorkspaceMemoryStore"]
                ).DaytonaWorkspaceMemoryStore(
                    sandbox,
                    volume_paths=paths,
                    max_upload_bytes=settings.max_upload_bytes,
                )
                entries = memory_store.list_entries(limit=16).entries
                promoted = [
                    entry
                    for entry in entries
                    if entry.source == "agent_candidate"
                    and entry.category == "operator preference"
                    and "ZEPHYR-4582" in entry.learning
                ]
                assert len(promoted) == 1
                memory_record = promoted[0]
                memory_id = memory_record.memory_id
                assert memory_record.active is True and memory_record.supersedes_id is None

                phase = "second_turn"
                second = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "text": (
                            "VERIFY ZEPHYR-4582 candidate memory. Do NOT call propose_memory again —"
                            " it was already done. Call search_memories once with"
                            ' query="ZEPHYR-4582 certified receipts" and limit=8, read the observation,'
                            " then SUBMIT an answer that quotes the 8-character memory id from"
                            " the search result and the entry source value."
                        )
                    },
                    headers={"Idempotency-Key": f"live-qre140-second-{uuid4()}"},
                )
                assert second.status_code == 200
                second_chunks, second_done = _sse_chunks(second)
                run_ids.append(str(next(chunk["messageId"] for chunk in second_chunks if chunk["type"] == "start")))
                assert second_done == 1
                _assert_sse_stop(second_chunks, label="qre140_verify_turn")
                usage_gathered.extend(chunk for chunk in second_chunks if chunk.get("type") == "data-usage")

                search_inputs, search_outputs, search_errors = _paired_tool_chunks(second_chunks, "search_memories")
                assert search_errors == []
                assert len(search_inputs) == len(search_outputs) == 1
                search_output = search_outputs[0]["output"]
                # The bounded search event view intentionally carries ids only (no
                # learning/query/source on the wire); provenance is certified on the
                # store readback and the injection digest below.
                assert memory_id in search_output.get("top_memory_ids", ())
                final_text = "".join(
                    str(chunk.get("delta", "")) for chunk in second_chunks if chunk.get("type") == "text-delta"
                )
                assert memory_id in final_text

                phase = "injection_readback"
                digest = read_workspace_memory_injection_digest(
                    memory_store,
                    request="VERIFY ZEPHYR-4582 candidate memory",
                )
                assert memory_id in digest
                assert "source:agent_candidate" in digest
                assert "ZEPHYR-4582" in digest

                phase = "secret_audit"
                secret_values = tuple(
                    value
                    for secret in (settings.daytona_api_key, settings.llm_api_key)
                    if secret is not None
                    for value in (secret.get_secret_value(),)
                    if value
                )
                _assert_secret_free(secret_values, first_chunks, second_chunks, final_text)
                env_names = _sandbox_environment_names(sandbox)
                assert not set(_SECRET_NAMES) & env_names
                phase = "cleanup"
            finally:
                if sandbox_ids:
                    cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    except Exception as exc:
        _write_receipt(
            _failure_receipt(
                candidate,
                started_at=started_at,
                category="proof_failed",
                phase=phase,
                stream={
                    "first_turn": _chunk_histogram(first_chunks),
                    "second_turn": _chunk_histogram(second_chunks),
                },
            )
        )
        raise exc

    assert cleanup_failures == (), cleanup_failures
    receipt: dict[str, object] = {
        "schema": _RECEIPT_SCHEMA,
        "candidate": {
            **candidate,
            "versions": candidate["versions"],
            "lockfile_sha256": candidate["lockfile_sha256"],
            "models": candidate["models"],
        },
        "timing": {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
        },
        "resources": {
            "session_id": str(session_id),
            "run_ids": run_ids,
            "sandbox_ids": sorted(sandbox_ids),
            "volume_name": settings.volume_name,
        },
        "counts": {
            "turns": len(run_ids),
            "propose_memory_calls": sum(
                chunk.get("toolName") == "propose_memory"
                for chunk in first_chunks
                if chunk["type"] == "tool-input-available"
            ),
            "search_memory_calls": sum(
                chunk.get("toolName") == "search_memories"
                for chunk in second_chunks
                if chunk["type"] == "tool-input-available"
            ),
            "usage_events": len(usage_gathered),
            "memory_id_chars": len(memory_id),
        },
        "assertions": {
            "proposal_observed": True,
            "post_commit_v3_promoted": bool(memory_id),
            "searchable_on_next_turn": True,
            "injectable_on_next_turn": True,
            "secret_audit_passed": True,
            "cleanup_passed": cleanup_failures == (),
        },
        "memory": {
            "memory_id": memory_id,
            "category": "operator preference",
            "source": "agent_candidate",
            "learning_sha256": hashlib.sha256(_PROBE_LEARNING.encode()).hexdigest(),
        },
        "passed": True,
    }
    _write_receipt(receipt)
