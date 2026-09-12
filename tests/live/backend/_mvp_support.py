"""Shared scenario setup; not a collected test module."""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

from fleet_rlm.config.loader import active_profile_contract, load_profile_environment_contracts, load_runtime_settings
from fleet_rlm.config.settings import Settings
from tests.live.backend._database import upgrade_to_head

_REPO_ROOT = Path(__file__).resolve().parents[3]


_P27_SESSION_SNAPSHOT_ENV = "FLEET_P27_SESSION_SNAPSHOT"


_SECRET_NAMES = tuple(
    name for contract in load_profile_environment_contracts() for name in contract.provider_environment_names
)


_CLEANUP_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
_EPHEMERAL_PROOF_VOLUME_PREFIXES = (
    "fleet-rlm-live-mvp-",
    "fleet-rlm-live-cancel-",
    "fleet-rlm-live-deadline-",
    "fleet-rlm-qre140-",
    "fleet-rlm-qre142-",
)


_LIVE_ROOT_MODEL = os.environ.get("FLEET_LIVE_ROOT_MODEL", "databricks-deepseek-v4-flash-0731")


_LIVE_SUB_MODEL = os.environ.get("FLEET_LIVE_SUB_MODEL", "databricks-deepseek-v4-flash-0731")


_APPROVED_MODELS = frozenset(
    name
    for base in {
        _LIVE_ROOT_MODEL,
        _LIVE_ROOT_MODEL.removesuffix("-0731"),
        _LIVE_SUB_MODEL,
        _LIVE_SUB_MODEL.removesuffix("-0731"),
    }
    for name in (base, f"openai/{base}")
)


def _load_repo_env() -> None:
    """Load repo ``.env`` into the process without overriding exported values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _live_settings(tmp_path: Path) -> Settings:
    """
    Load settings for the live Daytona MVP proof.

    Parameters:
        tmp_path (Path): Temporary directory used for the proof database.

    Returns:
        Settings: Runtime settings with a temporary database, the configured
            Workspace Volume, bounded proof limits, and MLflow tracing disabled.
    """
    _load_repo_env()
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for the complete Daytona MVP proof")
    required_environment = active_profile_contract().provider_environment_names
    missing = [name for name in required_environment if not os.environ.get(name)]
    if missing:
        pytest.fail("Live Daytona MVP proof missing required credentials: " + ", ".join(missing))
    policy = load_runtime_settings()
    if policy.root_model not in _APPROVED_MODELS or policy.sub_model not in _APPROVED_MODELS:
        pytest.fail("Live Daytona MVP proof requires the committed Root and Sub policy")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'live-mvp.db').resolve()}"
    upgrade_to_head(database_url)
    overrides: dict[str, object] = {
        "database_url": database_url,
        "rlm_max_iters": 8,
        "rlm_max_llm_calls": 12,
        "turn_timeout_seconds": 840,
        # Live product evidence is RuntimeEvents → SSE → TUI. Keep optional
        # MLflow out of the MVP lane so a dead local tracking URI cannot
        # starve claim heartbeats during preparation.
        "mlflow_tracing_enabled": False,
    }
    if candidate_snapshot := os.environ.get(_P27_SESSION_SNAPSHOT_ENV):
        overrides["daytona_snapshot"] = candidate_snapshot
    return policy.model_copy(update=overrides)


def _sse_chunks(response: Any) -> tuple[list[dict[str, Any]], int]:
    chunks: list[dict[str, Any]] = []
    done = 0
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            done += 1
        else:
            chunks.append(json.loads(payload))
    return chunks, done


def _call_shapes(chunks: list[dict[str, Any]], call_name: str) -> list[dict[str, object]]:
    shapes: list[dict[str, object]] = []
    for chunk in chunks:
        if chunk.get("type") != "data-rlm-code":
            continue
        code = str(chunk.get("data", {}).get("code", ""))
        if call_name not in code:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            shapes.append({"parse": "invalid"})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name != call_name:
                continue
            shapes.append(
                {
                    "positional_count": len(node.args),
                    "positional_kinds": [type(argument).__name__ for argument in node.args],
                    "keyword_names": [keyword.arg or "**" for keyword in node.keywords],
                    "keyword_kinds": {keyword.arg or "**": type(keyword.value).__name__ for keyword in node.keywords},
                }
            )
    return shapes


def _semantic_tool_diagnostic(chunks: list[dict[str, Any]]) -> dict[str, object]:
    inputs = [
        chunk.get("input") if isinstance(chunk.get("input"), dict) else {}
        for chunk in chunks
        if chunk.get("type") == "tool-input-available" and chunk.get("toolName") == "verify_semantic_work"
    ]
    call_ids = {
        str(chunk.get("toolCallId", ""))
        for chunk in chunks
        if chunk.get("type") == "tool-input-available" and chunk.get("toolName") == "verify_semantic_work"
    }
    failures = [
        str(chunk.get("errorText", ""))
        for chunk in chunks
        if chunk.get("type") == "tool-output-error" and str(chunk.get("toolCallId", "")) in call_ids
    ]
    if not inputs:
        classification = "semantic_tool_not_observed"
    elif not failures:
        classification = "semantic_tool_not_failed"
    elif any(not values for values in inputs):
        classification = "signature_bind_failed"
    elif any(error == "Tool arguments are invalid" for error in failures):
        expected_types = {
            "iteration_token_type": "str",
            "single_result_type": "str",
            "batch_results_type": "list",
            "batch_result_item_types": ["str"],
            "accumulator_type": "list",
            "accumulator_item_types": ["str"],
        }
        classification = (
            "validator_or_transport_mismatch"
            if all(all(values.get(key) == value for key, value in expected_types.items()) for values in inputs)
            else "dspy_type_validation_failed"
        )
    else:
        classification = "semantic_tool_execution_failed"
    return {
        "call_shapes": _call_shapes(chunks, "verify_semantic_work"),
        "bound_shapes": inputs,
        "classification": classification,
    }


def _sse_finish_diagnostic(chunks: list[dict[str, Any]]) -> str:
    """Bounded summary for failed stop assertions (no code dumps or secrets)."""
    type_counts: dict[str, int] = {}
    tool_names_by_call: dict[str, str] = {}
    for chunk in chunks:
        kind = str(chunk.get("type", "?"))
        type_counts[kind] = type_counts.get(kind, 0) + 1
        if kind == "tool-input-available":
            call_id = str(chunk.get("toolCallId", ""))
            name = str(chunk.get("toolName", ""))
            if call_id and name:
                tool_names_by_call[call_id] = name
    error_texts = [str(chunk.get("errorText", ""))[:200] for chunk in chunks if chunk.get("type") == "error"]
    finish_reasons = [str(chunk.get("finishReason", "")) for chunk in chunks if chunk.get("type") == "finish"]
    tool_errors = [
        {
            "toolName": tool_names_by_call.get(str(chunk.get("toolCallId", "")), "unknown"),
            "errorText": str(chunk.get("errorText", ""))[:200],
        }
        for chunk in chunks
        if chunk.get("type") == "tool-output-error"
    ]
    return (
        f"chunk_types={dict(sorted(type_counts.items()))} "
        f"finish_reasons={finish_reasons} "
        f"error_texts={error_texts} "
        f"tool_errors={tool_errors} "
        f"semantic_tool={_semantic_tool_diagnostic(chunks)} "
        f"submit_call_shapes={_call_shapes(chunks, 'SUBMIT')}"
    )


def _assert_sse_stop(chunks: list[dict[str, Any]], *, label: str) -> None:
    if not chunks:
        pytest.fail(f"{label}: no SSE chunks")
    last = chunks[-1]
    if last.get("type") != "finish" or last.get("finishReason") != "stop":
        pytest.fail(
            f"{label}: expected finish/stop, got type={last.get('type')!r} "
            f"finishReason={last.get('finishReason')!r}; {_sse_finish_diagnostic(chunks)}"
        )


def _assert_secret_free(secret_values: tuple[str, ...], *values: object) -> None:
    payload = json.dumps(values, ensure_ascii=False, default=str)
    if any(secret and secret in payload for secret in secret_values):
        pytest.fail("provider credential leaked into a client-visible or durable proof surface")


def _sandbox_environment_names(sandbox: Any) -> set[str]:
    result = sandbox.process.code_run(
        "import json, os\nprint(json.dumps(sorted(os.environ)))",
        timeout=30,
    )
    if result.exit_code != 0:
        raise AssertionError("could not inspect Sandbox environment names")
    return set(json.loads(result.result.strip()))


async def _retry_cleanup(operation: Any) -> bool:
    for attempt, delay in enumerate((0.0, *_CLEANUP_RETRY_DELAYS)):
        try:
            await operation()
            return True
        except Exception:
            if attempt == len(_CLEANUP_RETRY_DELAYS):
                return False
            if delay:
                await asyncio.sleep(delay)
    return False


def _owns_ephemeral_proof_volume(volume_name: str) -> bool:
    """Return whether live-proof cleanup should delete this Daytona volume."""
    return volume_name.startswith(_EPHEMERAL_PROOF_VOLUME_PREFIXES)


async def _strict_cleanup(resources: Any, sandbox_ids: set[str], volume_name: str) -> tuple[str, ...]:
    failures: list[str] = []
    tracked_ids = sandbox_ids | set(resources._sandbox_ids)
    for sandbox_id in sorted(tracked_ids):

        async def delete_sandbox(sandbox_id: str = sandbox_id) -> None:
            sandbox = await resources.platform.get(sandbox_id)
            if sandbox is None:
                return
            state = str(getattr(getattr(sandbox, "state", None), "value", getattr(sandbox, "state", None)) or "")
            if state.strip().lower() in {"destroyed", "deleted", "archived"}:
                return
            await resources.platform.delete(sandbox)

        if not await _retry_cleanup(delete_sandbox):
            failures.append("sandbox")
    try:
        resources._sandbox_ids.clear()
    except Exception:
        failures.append("tracking")

    if not _owns_ephemeral_proof_volume(volume_name):
        return tuple(failures)

    async def delete_volume() -> None:
        volume = await resources.client.volume.get(volume_name, create=False)
        if volume is not None:
            await resources.client.volume.delete(volume)

    if not await _retry_cleanup(delete_volume):
        failures.append("volume")
    return tuple(failures)
