"""Session persistence orchestration for runtime services."""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fleet_rlm.api.runtime_services.stream_failures import PersistenceRequiredError
from fleet_rlm.db import (
    FleetRepository,
    MemoryKind,
    MemoryScope,
    MemorySource,
)
from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.db.repos.memory import MemoryItemCreateRequest
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log
from fleet_rlm.utils.time import now_iso

from ..dependencies import SessionCacheDeps

logger = logging.getLogger(__name__)


async def _with_session_persist_span(
    name: str,
    operation: Callable[[], Any],
    *,
    attributes: dict[str, Any] | None = None,
) -> Any:
    from fleet_rlm.integrations.observability.mlflow_context import (
        mlflow_child_span,
        set_mlflow_span_outputs,
    )

    manager = None
    span = None
    try:
        manager = mlflow_child_span(
            name,
            span_type="CHAIN",
            attributes={
                "fleet_rlm.execution_origin": "session_persistence",
                **(attributes or {}),
            },
        )
        span = manager.__enter__()
    except Exception:
        logger.debug("MLflow session persistence span skipped: %s", name, exc_info=True)
        manager = None

    try:
        result = operation()
        if inspect.isawaitable(result):
            result = await result
        set_mlflow_span_outputs(span, {"status": "ok"})
        return result
    except BaseException as exc:
        if manager is not None:
            try:
                manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                logger.debug("MLflow session persistence span exit skipped after error: %s", name, exc_info=True)
            finally:
                manager = None
        raise
    finally:
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                logger.debug("MLflow session persistence span exit skipped: %s", name, exc_info=True)


def ensure_manifest_shape(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize mutable manifest structure and expected keys."""
    if not isinstance(manifest.get("logs"), list):
        manifest["logs"] = []
    if not isinstance(manifest.get("memory"), list):
        manifest["memory"] = []
    if not isinstance(manifest.get("generated_docs"), list):
        manifest["generated_docs"] = []
    if not isinstance(manifest.get("artifacts"), list):
        manifest["artifacts"] = []
    if not isinstance(manifest.get("metadata"), dict):
        manifest["metadata"] = {}
    return manifest


def update_manifest_from_exported_state(
    *,
    manifest: dict[str, Any],
    exported_state: dict[str, Any],
    latest_user_message: str,
) -> tuple[int, int]:
    """Update manifest with latest state snapshot and optional user message entry."""
    ensure_manifest_shape(manifest)

    logs = manifest["logs"]
    memory = manifest["memory"]
    generated_docs = manifest["generated_docs"]
    artifacts = manifest["artifacts"]
    metadata = manifest["metadata"]

    if latest_user_message:
        logs.append(
            {
                "timestamp": now_iso(),
                "user_message": latest_user_message,
                "history_turns": len(exported_state.get("history", [])),
            }
        )
        memory.append(
            {
                "timestamp": now_iso(),
                "content": latest_user_message[:400],
            }
        )

    generated_docs[:] = sorted(list(exported_state.get("documents", {}).keys()))

    previous_rev_raw = manifest.get("rev", 0)
    previous_rev_candidate = previous_rev_raw if isinstance(previous_rev_raw, (int, float, str)) else 0
    try:
        previous_rev = int(previous_rev_candidate)
    except (TypeError, ValueError):
        previous_rev = 0

    next_rev = previous_rev + 1
    manifest["rev"] = next_rev
    metadata["updated_at"] = now_iso()
    metadata["history_turns"] = len(exported_state.get("history", []))
    metadata["document_count"] = len(exported_state.get("documents", {}))
    metadata["artifact_count"] = len(artifacts)
    manifest["state"] = exported_state
    return previous_rev, next_rev


def sync_session_record_state(
    *,
    session_cache: SessionCacheDeps,
    session_record: dict[str, Any],
    exported_state: dict[str, Any],
) -> None:
    """Propagate exported state into session record and state cache."""
    session_data = session_record.get("session")
    if not isinstance(session_data, dict):
        session_data = {}
        session_record["session"] = session_data
    session_data["state"] = exported_state
    session_data["session_id"] = session_record.get("session_id")

    record_key = session_record.get("key")
    if isinstance(record_key, str):
        session_cache.sessions[record_key] = session_record


async def persist_memory_item_if_needed(
    *,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    active_run_db_id: Any,
    latest_user_message: str,
    persistence_required: bool,
) -> None:
    """Persist a user-input memory item when repository context is available."""
    if not latest_user_message or repository is None or identity_rows is None:
        return
    try:
        await repository.store_memory_item(
            MemoryItemCreateRequest(
                tenant_id=identity_rows.tenant_id,
                workspace_id=identity_rows.workspace_id,
                user_id=identity_rows.user_id,
                run_id=active_run_db_id,
                scope=MemoryScope.RUN if active_run_db_id is not None else MemoryScope.USER,
                scope_id=str(active_run_db_id or identity_rows.user_id),
                kind=MemoryKind.NOTE,
                source=MemorySource.USER_INPUT,
                content_text=latest_user_message[:1000],
                tags=["ws", "chat"],
            )
        )
    except Exception as exc:
        if persistence_required:
            raise PersistenceRequiredError(
                "memory_item_persist_failed",
                f"Failed to persist memory item: {exc}",
            ) from exc
        logger.warning("Failed to persist memory item: %s", _sanitize_for_log(exc))


async def _persist_manifest_to_local_store(
    *,
    persistence: Any,
    sess_id: str,
    manifest: dict[str, Any],
) -> None:
    """Write the manifest into LocalStore/FleetRepository session metadata.

    Used as a fallback when no Daytona volume is available (interpreter=None) so
    that session state survives process restarts between WebSocket connections.
    """
    if persistence is None:
        return
    update_fn = getattr(persistence, "update_chat_session", None)
    if not callable(update_fn):
        return
    try:
        sig = inspect.signature(update_fn)
        # LocalStore.update_chat_session requires tenant_id + session_id UUIDs; the
        # async FleetRepository variant has the same shape.  Both accept metadata_json.
        # We store under the raw external_session_id key so the restore helper can
        # locate it without a UUID round-trip.
        params = set(sig.parameters)
        if "external_session_id" in params:
            result = update_fn(external_session_id=sess_id, metadata_json={"_manifest_state": manifest})
            if inspect.iscoroutine(result):
                await result
        else:
            # Async path: skip – we cannot derive the UUID here without identity_rows.
            pass
    except Exception:
        logger.debug("Best-effort manifest persist to local store failed", exc_info=True)


async def _restore_manifest_from_local_store(
    *,
    persistence: Any,
    sess_id: str,
) -> dict[str, Any]:
    """Read a previously persisted manifest from LocalStore session metadata.

    Returns an empty dict when nothing is found or an error occurs.
    """
    if persistence is None:
        return {}
    get_fn = getattr(persistence, "get_chat_session_by_external_id", None)
    if not callable(get_fn):
        return {}
    try:
        result = get_fn(external_session_id=sess_id)
        row = await result if inspect.iscoroutine(result) else result
        if row is None:
            return {}
        metadata = getattr(row, "metadata_json", None)
        if not isinstance(metadata, dict):
            return {}
        manifest = metadata.get("_manifest_state")
        return manifest if isinstance(manifest, dict) else {}
    except Exception:
        logger.debug("Best-effort manifest restore from local store failed", exc_info=True)
        return {}


def _history_turns_from_exported_state(exported_state: dict[str, Any]) -> list[dict[str, Any]]:
    raw_turns = exported_state.get("turns")
    if not isinstance(raw_turns, list):
        raw_turns = exported_state.get("history")
    if not isinstance(raw_turns, list):
        return []

    turns: list[dict[str, Any]] = []
    for raw_turn in raw_turns:
        if not isinstance(raw_turn, dict):
            continue
        user_message = str(raw_turn.get("user_message") or "").strip()
        if not user_message:
            continue
        turns.append(
            {
                "user_message": user_message,
                "response": str(raw_turn.get("response") or raw_turn.get("assistant_message") or ""),
            }
        )
    return turns


async def _persist_local_turns_from_exported_state(
    *,
    persistence: Any,
    session_record: dict[str, Any],
    exported_state: dict[str, Any],
    identity_rows: IdentityUpsertResult | None,
) -> None:
    """Populate LocalStore chat_turns from runtime history for no-Postgres dev."""
    if persistence is None or identity_rows is None:
        return
    replace_turns = getattr(persistence, "replace_chat_turns_from_history", None)
    if not callable(replace_turns):
        return

    db_session_id = str(session_record.get("db_session_id") or "").strip()
    if not db_session_id:
        manifest = session_record.get("manifest")
        if isinstance(manifest, dict):
            metadata = manifest.get("metadata")
            if isinstance(metadata, dict):
                db_session_id = str(metadata.get("db_session_id") or "").strip()
    if not db_session_id:
        return

    try:
        session_uuid = uuid.UUID(db_session_id)
    except ValueError:
        return

    turns = _history_turns_from_exported_state(exported_state)
    if not turns:
        return

    try:
        result = replace_turns(
            tenant_id=identity_rows.tenant_id,
            session_id=session_uuid,
            turns=turns,
            user_id=identity_rows.user_id,
            workspace_id=identity_rows.workspace_id,
        )
        if inspect.iscoroutine(result):
            await result
    except Exception:
        logger.debug("Best-effort local chat turn persist failed", exc_info=True)


async def _persist_repository_turns_from_exported_state(
    *,
    repository: FleetRepository | None,
    session_record: dict[str, Any],
    exported_state: dict[str, Any],
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
) -> None:
    """Populate repository chat_turns from runtime history for Postgres-backed auth."""
    if repository is None or identity_rows is None:
        return
    replace_turns = getattr(repository, "replace_chat_turns_from_history", None)
    if not callable(replace_turns):
        return

    db_session_id = str(session_record.get("db_session_id") or "").strip()
    if not db_session_id:
        manifest = session_record.get("manifest")
        if isinstance(manifest, dict):
            metadata = manifest.get("metadata")
            if isinstance(metadata, dict):
                db_session_id = str(metadata.get("db_session_id") or "").strip()
    if not db_session_id:
        return

    try:
        session_uuid = uuid.UUID(db_session_id)
    except ValueError:
        return

    turns = _history_turns_from_exported_state(exported_state)
    if not turns:
        return

    try:
        result = replace_turns(
            tenant_id=identity_rows.tenant_id,
            session_id=session_uuid,
            turns=turns,
            user_id=identity_rows.user_id,
            workspace_id=identity_rows.workspace_id,
        )
        if inspect.iscoroutine(result):
            await result
    except Exception as exc:
        if persistence_required:
            raise PersistenceRequiredError(
                "chat_turn_persist_failed",
                f"Failed to persist chat turns: {exc}",
            ) from exc
        logger.debug("Best-effort repository chat turn persist failed", exc_info=True)


async def persist_session_state(
    *,
    session_cache: SessionCacheDeps,
    agent: Any,
    session_record: dict[str, Any] | None,
    active_manifest_path: str | None,
    active_run_db_id: uuid.UUID | None,
    interpreter: Any | None,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    include_volume_save: bool = True,
    latest_user_message: str = "",
    persistence: Any = None,
    allow_volume_session_create: bool = True,
    release_idle_session: bool = False,
) -> None:
    """Persist current session state and optionally release the live Daytona sandbox."""
    try:
        await _persist_session_state_impl(
            session_cache=session_cache,
            agent=agent,
            session_record=session_record,
            active_manifest_path=active_manifest_path,
            active_run_db_id=active_run_db_id,
            interpreter=interpreter,
            repository=repository,
            identity_rows=identity_rows,
            persistence_required=persistence_required,
            include_volume_save=include_volume_save,
            latest_user_message=latest_user_message,
            persistence=persistence,
            allow_volume_session_create=allow_volume_session_create,
        )
    finally:
        if release_idle_session:
            from fleet_rlm.api.runtime_services.session_manifest import release_idle_daytona_session

            await _with_session_persist_span(
                "fleet_rlm.session_release_idle_daytona",
                lambda: release_idle_daytona_session(agent),
            )


async def _persist_session_state_impl(
    *,
    session_cache: SessionCacheDeps,
    agent: Any,
    session_record: dict[str, Any] | None,
    active_manifest_path: str | None,
    active_run_db_id: uuid.UUID | None,
    interpreter: Any | None,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    include_volume_save: bool = True,
    latest_user_message: str = "",
    persistence: Any = None,
    allow_volume_session_create: bool = True,
) -> None:
    """Persist current session state to in-memory cache, volume, and DB."""
    if session_record is None:
        return
    exported_state = await _with_session_persist_span(
        "fleet_rlm.session_export_state",
        agent.export_session_state,
    )
    manifest = session_record.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
        session_record["manifest"] = manifest

    ensure_manifest_shape(manifest)
    previous_rev, _next_rev = update_manifest_from_exported_state(
        manifest=manifest,
        exported_state=exported_state,
        latest_user_message=latest_user_message,
    )
    sync_session_record_state(
        session_cache=session_cache,
        session_record=session_record,
        exported_state=exported_state,
    )

    if include_volume_save and active_manifest_path and interpreter is not None:
        from fleet_rlm.api.runtime_services.session_manifest import (
            _aget_daytona_session,
            load_manifest_from_volume,
            save_manifest_to_volume,
        )

        existing_session = None
        if not allow_volume_session_create:
            existing_session = await _aget_daytona_session(agent, allow_create=False)
        if allow_volume_session_create or existing_session is not None:
            remote_manifest = await _with_session_persist_span(
                "fleet_rlm.session_volume_manifest_load",
                lambda: load_manifest_from_volume(
                    agent,
                    active_manifest_path,
                    allow_session_create=allow_volume_session_create,
                ),
                attributes={
                    "fleet_rlm.active_manifest_path": active_manifest_path,
                    "fleet_rlm.allow_volume_session_create": str(allow_volume_session_create).lower(),
                },
            )
            remote_rev_raw = remote_manifest.get("rev", 0)
            remote_rev_candidate = remote_rev_raw if isinstance(remote_rev_raw, (int, float, str)) else 0
            try:
                remote_rev = int(remote_rev_candidate)
            except (TypeError, ValueError):
                remote_rev = 0

            if remote_rev > previous_rev:
                message = (
                    f"Session manifest revision conflict detected (remote_rev={remote_rev}, local_rev={previous_rev})"
                )
                if persistence_required:
                    raise PersistenceRequiredError("manifest_conflict", message)
                logger.warning(message)
            else:
                saved_path = await _with_session_persist_span(
                    "fleet_rlm.session_volume_manifest_save",
                    lambda: save_manifest_to_volume(
                        agent,
                        active_manifest_path,
                        manifest,
                        allow_session_create=allow_volume_session_create,
                    ),
                    attributes={
                        "fleet_rlm.active_manifest_path": active_manifest_path,
                        "fleet_rlm.manifest_rev": str(manifest.get("rev", "")),
                    },
                )
                if saved_path is None:
                    message = f"Failed to save session manifest to volume (path={active_manifest_path})"
                    if persistence_required:
                        raise PersistenceRequiredError("manifest_write_failed", message)
                    logger.warning(message)
        else:
            logger.debug(
                "Skipping Daytona volume persistence because cleanup has no active session (path=%s)",
                active_manifest_path,
            )
    # Always persist to local store when persistence is available — this is the
    # durable fallback that survives sandbox churn. Pool-based dispatch means
    # each turn may acquire a *different* Daytona sandbox, so the volume save
    # above lands on the current sandbox while the *next* turn's new sandbox
    # volume starts empty. The local store is sandbox-independent and bridges
    # the gap. We write it regardless of whether a volume save also happened.
    if include_volume_save and persistence is not None:
        sess_id = str(session_record.get("session_id") or "")
        if sess_id:
            await _with_session_persist_span(
                "fleet_rlm.session_local_manifest_persist",
                lambda: _persist_manifest_to_local_store(
                    persistence=persistence,
                    sess_id=sess_id,
                    manifest=manifest,
                ),
                attributes={"fleet_rlm.session_id": sess_id},
            )
            await _with_session_persist_span(
                "fleet_rlm.session_local_turns_persist",
                lambda: _persist_local_turns_from_exported_state(
                    persistence=persistence,
                    session_record=session_record,
                    exported_state=exported_state,
                    identity_rows=identity_rows,
                ),
                attributes={"fleet_rlm.session_id": sess_id},
            )

    await _with_session_persist_span(
        "fleet_rlm.session_repository_turns_persist",
        lambda: _persist_repository_turns_from_exported_state(
            repository=repository,
            session_record=session_record,
            exported_state=exported_state,
            identity_rows=identity_rows,
            persistence_required=persistence_required,
        ),
        attributes={"fleet_rlm.repository_configured": str(repository is not None).lower()},
    )

    await _with_session_persist_span(
        "fleet_rlm.session_memory_persist",
        lambda: persist_memory_item_if_needed(
            repository=repository,
            identity_rows=identity_rows,
            active_run_db_id=active_run_db_id,
            latest_user_message=latest_user_message,
            persistence_required=persistence_required,
        ),
        attributes={"fleet_rlm.has_latest_user_message": str(bool(latest_user_message)).lower()},
    )


def build_local_persist_fn(
    *,
    session_cache: SessionCacheDeps,
    runtime: Any,
    agent: Any,
    interpreter: Any,
    session: Any,
):
    async def local_persist(
        *,
        include_volume_save: bool = True,
        latest_user_message: str = "",
        allow_volume_session_create: bool = True,
        release_idle_session: bool = False,
    ) -> None:
        try:
            await persist_session_state(
                session_cache=session_cache,
                agent=agent,
                session_record=session.session_record,
                active_manifest_path=session.active_manifest_path,
                active_run_db_id=session.active_run_db_id,
                interpreter=interpreter,
                repository=runtime.repository,
                identity_rows=runtime.identity_rows,
                persistence_required=runtime.persistence_required,
                include_volume_save=include_volume_save,
                latest_user_message=latest_user_message,
                persistence=runtime.persistence,
                allow_volume_session_create=allow_volume_session_create,
                release_idle_session=False,
            )
        finally:
            if release_idle_session:
                from fleet_rlm.api.runtime_services.session_manifest import release_idle_daytona_session

                await _with_session_persist_span(
                    "fleet_rlm.session_release_idle_daytona",
                    lambda: release_idle_daytona_session(agent),
                )

    return local_persist


__all__ = [
    "persist_session_state",
    "build_local_persist_fn",
    "ensure_manifest_shape",
    "update_manifest_from_exported_state",
    "sync_session_record_state",
    "persist_memory_item_if_needed",
    "_persist_manifest_to_local_store",
    "_restore_manifest_from_local_store",
]
