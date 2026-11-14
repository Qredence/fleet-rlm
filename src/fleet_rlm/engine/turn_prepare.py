"""Turn input preparation, reviewed-memory formatting, replay snapshot assembly, and trace description."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import dspy
import mlflow

from fleet_rlm.contracts import RequiredSkill, Route, SkillAdmissionError
from fleet_rlm.engine.reasoning import serialize_rlm_turn_history
from fleet_rlm.observability import phase_span, stream_callbacks
from fleet_rlm.replay import MAX_REPLAY_FILE_BYTES, ReplaySizeError, encode_snapshot
from fleet_rlm.sandbox.manager import SANDBOX_TRANSPORT

if TYPE_CHECKING:
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntime

_TRACE_PREVIEW_CHARS = 200
_MAX_MEMORIES_CHARS = 12_000
_ROOT_SANDBOX_ATTRIBUTE = "root_sandbox_id"
_PROVIDER_BOUND_KEYS = ("ttl_minutes", "auto_stop_interval", "maximum_lifespan_minutes", "provider_owned")


def _turn_facade() -> Any:
    return sys.modules.get("fleet_rlm.engine.turn")


def _max_memories_chars() -> int:
    facade = _turn_facade()
    value = getattr(facade, "_MAX_MEMORIES_CHARS", _MAX_MEMORIES_CHARS)
    return int(value)


def _max_replay_file_bytes() -> int:
    facade = _turn_facade()
    value = getattr(facade, "MAX_REPLAY_FILE_BYTES", MAX_REPLAY_FILE_BYTES)
    return int(value)


def _live_sandbox_id(manager: Any) -> str | None:
    """Return the root provider handle id, or None when nothing was provisioned."""
    reader = getattr(manager, "live_sandbox_id", None)
    sandbox_id = reader() if callable(reader) else reader
    return sandbox_id if isinstance(sandbox_id, str) and sandbox_id else None


def _provider_bounds(manager: Any) -> dict[str, Any]:
    """Read the provider-owned bounds from the manager, or nothing when it cannot report them."""
    reader = getattr(manager, "provider_bounds", None)
    bounds = reader() if callable(reader) else {}
    return bounds if isinstance(bounds, dict) else {}


def _provider_bounds_attribute(bounds: dict[str, Any]) -> str | None:
    """Serialize only the provider-bound fields for one bounded span attribute."""
    values = {key: bounds[key] for key in _PROVIDER_BOUND_KEYS if key in bounds}
    return json.dumps(values, sort_keys=True) if values else None


def _turn_environment(manager: Any) -> dict[str, Any]:
    """Snapshot, deadline, root sandbox id, and provider bounds for a Turn outcome."""
    root_sandbox_id = _live_sandbox_id(manager)
    return {
        **manager.snapshot_provenance(),
        **manager.deadline_provenance(),
        **({_ROOT_SANDBOX_ATTRIBUTE: root_sandbox_id} if root_sandbox_id else {}),
        **_provider_bounds(manager),
    }


def _unpack_context(runtime: Any, session_id: str, *, include_rlm: bool = False) -> tuple[str, str, Any]:
    """Read `(memory_summary, session_history, rlm_history)` from SessionContext or legacy tuple mocks."""
    loaded = runtime.load_context(session_id)
    if isinstance(loaded, tuple):
        memory_summary, session_history = loaded
        if include_rlm and callable(load_rlm := getattr(runtime, "load_rlm_history", None)):
            return str(memory_summary), str(session_history), load_rlm(session_id)
        return str(memory_summary), str(session_history), None
    return (
        str(loaded.memory_summary),
        str(loaded.session_history),
        loaded.rlm_history if include_rlm else None,
    )


def _format_reviewed_memories(memories: list[dict[str, Any]]) -> str:
    """Format reviewed Session memories as clean Markdown bullets bounded to 12,000 characters."""
    if not memories:
        return ""
    limit = _max_memories_chars()
    lines: list[str] = []
    total = 0
    for item in memories:
        if not isinstance(item, dict):
            line = f"- {item}"
        else:
            kind = str(item.get("kind") or "memory")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            line = f"- [{kind}] {content}"
        extra = len(line) + (1 if lines else 0)
        if total + extra > limit:
            remaining = limit - total - (1 if lines else 0)
            if remaining > 4:
                lines.append(line[: remaining - 1] + "…")
            break
        lines.append(line)
        total += extra
    return "\n".join(lines)


def _join_memory_sections(*parts: str) -> str:
    """Join non-empty memory summary, environment hint, and reviewed memory bullets cleanly."""
    return "\n".join(part for part in parts if part)


def _prepare_empty_direct_inputs(
    runtime: SessionRuntime,
    session_id: str,
    task: str,
    *,
    callback: Any,
    record_replay: bool,
    required_skills: tuple[RequiredSkill, ...],
    dspy_module: Any = dspy,
) -> tuple[dict[str, Any] | None, Route | None]:
    """Preview the route on a known-empty workspace and assemble local inputs on Direct."""
    can_route_empty = (
        runtime.workspace_state == "known_empty"
        and runtime.context is None
        and not required_skills
        and callable(getattr(runtime.engine, "preview_route", None))
    )
    if not can_route_empty:
        return None, None
    local_files: dict[str, bytes] = {}
    try:
        if callable(stage_local := getattr(runtime.skill_mgr, "stage_local_skills", None)):
            local_catalog, local_files = stage_local()
        else:
            local_catalog = runtime.skill_mgr.build_local_catalog_xml()
            local_files = (
                local_files_fn()
                if callable(local_files_fn := getattr(runtime.skill_mgr, "local_skill_files", None))
                else {}
            )
    except (OSError, UnicodeError, ValueError):
        local_catalog = None
    if local_catalog is None:
        return None, None
    with phase_span("turn.skill_catalog", source="local") as skill_phase:
        skill_phase.set_outputs({"chars": len(local_catalog)})
    empty_manifest = ", ".join(f"/workspace/{path}" for path in sorted(local_files)) or "/workspace/files (empty)"
    with dspy_module.context(callbacks=stream_callbacks(callback), disable_history=True):
        selected_route = runtime.engine.preview_route(
            task=task, workspace_manifest=empty_manifest, skills_catalog=local_catalog
        )
    if selected_route is not Route.DIRECT:
        # Any execution route may write files, so future Turns must inspect the Volume.
        runtime.set_workspace_state("unknown")
        return None, selected_route
    with phase_span("turn.load_context", session_id=session_id) as context_phase:
        memory_summary, session_history, _rlm_history = _unpack_context(runtime, session_id)
        context_phase.set_outputs({"memory_chars": len(memory_summary), "history_chars": len(session_history)})
    with phase_span("turn.reviewed_memories", task_chars=len(task)) as memory_phase:
        memories = runtime.memories(task, reviewed_only=True)
        memory_phase.set_outputs({"memories": len(memories)})
    replay_files: dict[str, str] = {}
    replay_selected = False
    replay_skipped_bytes: int | None = None
    if record_replay:
        max_replay_bytes = _max_replay_file_bytes()
        total_bytes = sum(map(len, local_files.values()))
        if total_bytes <= max_replay_bytes:
            replay_files = {
                path: base64.b64encode(content).decode("ascii") for path, content in sorted(local_files.items())
            }
            replay_selected = True
        else:
            replay_skipped_bytes = total_bytes
    inputs = {
        "manifest": empty_manifest,
        "context_files": {path: hashlib.sha256(content).hexdigest() for path, content in sorted(local_files.items())},
        "skills_catalog": local_catalog,
        "memory_summary": _join_memory_sections(memory_summary, _format_reviewed_memories(memories)),
        "session_history": session_history,
        "replay_files": replay_files,
        "replay_selected": replay_selected,
        "replay_skipped_bytes": replay_skipped_bytes,
    }
    return inputs, selected_route


def _prepare_inputs(
    runtime: SessionRuntime,
    session_id: str,
    task: str,
    *,
    record_replay: bool = True,
    required_skills: tuple[RequiredSkill, ...] = (),
) -> dict[str, Any]:
    """Sync Skills and assemble workspace digests, memory, history, and the Skill catalog."""
    with phase_span("turn.load_context", session_id=session_id) as phase:
        memory_summary, session_history, rlm_history = _unpack_context(runtime, session_id, include_rlm=True)
        phase.set_outputs({"memory_chars": len(memory_summary), "history_chars": len(session_history)})
    # Sync can partially write even on failure; invalidate before touching the Volume.
    runtime.set_workspace_state("unknown")
    # Skills land first so their files are hashed; everything else remote arrives in one exec.
    with phase_span("turn.sync_skills") as phase:
        phase.set_outputs(
            {
                "skills": runtime.skill_mgr.sync_local_skills_to_volume(
                    **({"required_skills": required_skills} if required_skills else {})
                )
            }
        )
    with phase_span("turn.workspace_inputs") as phase:
        remote = runtime.sandbox_mgr.turn_inputs()
        phase.set_outputs({"files": len(remote["digests"]), "has_environment": bool(remote["environment"])})
    # Workspace files are model-writable, so they cannot approve executable programs.
    context_files = remote["digests"]
    required_files = {
        f"skills/{skill.name}/{path}": content for skill in required_skills for path, content in skill.files
    }
    for skill in required_skills:
        prefix = f"skills/{skill.name}/"
        if {path for path in context_files if path.startswith(prefix)} != {
            path for path in required_files if path.startswith(prefix)
        }:
            raise SkillAdmissionError("Required Skill resource manifest changed during preparation")
    for path, content in required_files.items():
        if context_files.get(path) != hashlib.sha256(content).hexdigest():
            raise SkillAdmissionError("Required Skill files changed during preparation")
    files = sorted(context_files)
    replay_files: dict[str, str] = {}
    replay_bytes = 0
    replay_selected = record_replay
    replay_skipped_bytes: int | None = None
    max_replay_bytes = _max_replay_file_bytes()
    if record_replay:
        # Remote sizes decide before any download: an oversized workspace is never pulled just to be dropped.
        remote_sizes = remote.get("sizes", {})
        known_bytes = sum(len(required_files[p]) if p in required_files else remote_sizes.get(p, 0) for p in files)
        if known_bytes > max_replay_bytes:
            replay_selected, replay_skipped_bytes = False, known_bytes
        with phase_span("turn.replay_files") as phase:
            remote_paths = [path for path in files if path not in required_files] if replay_selected else []
            downloaded = runtime.sandbox_mgr.read_workspace_files(remote_paths) if remote_paths else {}
            for path in files if replay_selected else ():
                content = required_files[path] if path in required_files else downloaded[path]
                if hashlib.sha256(content).hexdigest() != context_files[path]:
                    raise ValueError(f"Workspace file changed while the Turn was prepared: {path}")
                replay_bytes += len(content)
                if replay_bytes > max_replay_bytes:
                    # Capture is best-effort: an oversized workspace skips the snapshot, not the Turn.
                    replay_files.clear()
                    replay_selected = False
                    replay_skipped_bytes = replay_bytes
                    break
                replay_files[path] = base64.b64encode(content).decode("ascii")
            phase.set_outputs({"files": len(replay_files), "bytes": replay_bytes})
    with phase_span("turn.reviewed_memories", task_chars=len(task)) as phase:
        memories = runtime.memories(task, reviewed_only=True)
        phase.set_outputs({"memories": len(memories)})
    with phase_span("turn.skill_catalog") as phase:
        skills_catalog = runtime.skill_mgr.build_catalog_xml()
        phase.set_outputs({"chars": len(skills_catalog)})
    return {
        "manifest": ", ".join(f"/workspace/{path}" for path in files) or "/workspace/files (empty)",
        "context_files": context_files,
        "skills_catalog": skills_catalog,
        "memory_summary": _join_memory_sections(
            memory_summary, remote["environment"], _format_reviewed_memories(memories)
        ),
        "session_history": session_history,
        "rlm_history": rlm_history,
        "replay_files": replay_files,
        "replay_selected": replay_selected,
        "replay_skipped_bytes": replay_skipped_bytes,
    }


def _capture_replay_snapshot(
    runtime: SessionRuntime,
    settings: FleetSettings,
    *,
    session_id: str,
    turn_id: str,
    task: str,
    inputs: dict[str, Any],
    policy: Any,
    snapshot_provenance: dict[str, Any],
    requested_skills: tuple[str, ...],
    skill_intent: dict[str, Any],
    emit: Any,
    encoder: Any = encode_snapshot,
) -> tuple[str, bytes] | None:
    """Build and encode the immutable replay snapshot, emitting `replay_skipped` if oversized."""
    from fleet_rlm.session import runtime_configuration

    replay_snapshot: tuple[str, bytes] | None = None
    with phase_span("turn.capture_replay", turn_id=turn_id) as phase:
        snapshot = {
            "schema_version": 2,
            "requested_skills": list(requested_skills),
            "required_skills": skill_intent.get("required_skills", []),
            "turn_id": turn_id,
            "session_id": session_id,
            "task": task,
            "workspace_manifest": inputs["manifest"],
            "context_files": inputs["context_files"],
            "skill_revision_sha256": hashlib.sha256(
                json.dumps(
                    {
                        path: digest
                        for path, digest in sorted(inputs["context_files"].items())
                        if path.startswith("skills/")
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "replay_files": inputs["replay_files"],
            "skills_catalog": inputs["skills_catalog"],
            "memory_summary": inputs["memory_summary"],
            "session_history": inputs["session_history"],
            "rlm_history": [
                {
                    "rlm_history": serialize_rlm_turn_history(
                        dspy.History(messages=inputs["rlm_history"].messages[i : i + 8])
                    )
                }
                for i in range(0, len(inputs["rlm_history"].messages), 8)
            ]
            if inputs.get("rlm_history") is not None
            else [],
            "dataset_context": runtime.context.to_descriptor() if runtime.context is not None else None,
            "execution_mode": runtime.engine.execution_mode,
            "turn_timeout_seconds": runtime.sandbox_mgr.turn_timeout_seconds,
            "deadline_source": policy.source,
            "effective_seconds": policy.effective_seconds,
            "sandbox_transport": SANDBOX_TRANSPORT,
            **snapshot_provenance,
            "configuration": runtime_configuration(settings, runtime),
            "provenance": {"fleet_rlm_version": version("fleet-rlm")},
        }
        try:
            replay_snapshot = encoder(snapshot)
        except ReplaySizeError as error:
            inputs["replay_selected"] = False
            emit({"event": "replay_skipped", "bytes": error.size, "limit": error.limit})
        phase.set_attributes(
            {
                "snapshot_id": replay_snapshot[0] if replay_snapshot else None,
                "snapshot_bytes": len(replay_snapshot[1]) if replay_snapshot else 0,
                "replay_files": len(inputs["replay_files"]),
            }
        )
    return replay_snapshot


def _describe_turn(
    span: Any,
    runtime: SessionRuntime,
    settings: FleetSettings,
    session_id: str,
    task: str,
    turn_id: str,
    idempotency_key: str | None,
    inputs: dict[str, Any],
    replay_snapshot: tuple[str, bytes] | None,
    *,
    skill_intent: dict[str, Any] | None = None,
) -> None:
    """Record prepared input references, configuration, and sandbox provenance on the Turn trace."""
    from fleet_rlm.session import runtime_configuration

    configuration = runtime_configuration(settings, runtime)
    root_sandbox_id = _live_sandbox_id(runtime.sandbox_mgr)
    span.set_inputs(
        {
            "session_id": session_id,
            "task": task,
            "turn_id": turn_id,
            **(skill_intent or {}),
            "workspace_manifest": inputs["manifest"],
            "context_files": inputs["context_files"],
            "replay_snapshot_selected": replay_snapshot is not None,
            "replay_snapshot_sha256": replay_snapshot[0] if replay_snapshot else "",
        }
    )
    span.set_attributes(
        {
            "execution_mode": runtime.engine.execution_mode,
            "turn_timeout_seconds": runtime.sandbox_mgr.turn_timeout_seconds,
            **runtime.sandbox_mgr.deadline_provenance(),
            "sandbox_transport": SANDBOX_TRANSPORT,
            "configuration_digest": configuration["digest"],
            **runtime.sandbox_mgr.snapshot_provenance(),
            **({_ROOT_SANDBOX_ATTRIBUTE: root_sandbox_id} if root_sandbox_id else {}),
            "model_settings": {
                name: value
                for name, value in configuration["values"].items()
                if name.startswith(("fleet_root_", "fleet_sub_"))
            },
        }
    )
    mlflow.update_current_trace(
        metadata={"mlflow.trace.session": session_id},
        client_request_id=turn_id,
        tags={
            "fleet.execution_mode": runtime.engine.execution_mode,
            "fleet.idempotency_key": idempotency_key or turn_id,
            "fleet.configuration_digest": configuration["digest"],
        },
        request_preview=task[:_TRACE_PREVIEW_CHARS],
    )
