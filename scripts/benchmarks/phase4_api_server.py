"""Run one campaign-scoped Fleet FastAPI server with safe lifecycle telemetry.

The campaign driver starts this module in the selected checkout.  It is a
thin ASGI bootstrap: Fleet still owns the FastAPI routes, Turn lifecycle, and
Daytona resources.  The wrapper only applies the sealed campaign settings,
closes the retained root Session after each campaign Turn, and writes bounded
resource observations to an ephemeral NDJSON file for the parent driver.
"""

from __future__ import annotations

import argparse
import contextvars
import inspect
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final
from uuid import UUID

_CANDIDATE_ROOT = Path(__file__).resolve().parents[2]
_CHECKOUT_ROOT = Path.cwd().resolve()
for _path in (str(_CHECKOUT_ROOT / "src"), str(_CANDIDATE_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import uvicorn

_TRIAL_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TURN_PATH_RE: Final = re.compile(r"^/api/sessions/([0-9a-fA-F-]{36})/turns$")
_TRIAL_CONTEXT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fleet_phase4_trial",
    default=None,
)


def _bounded_trial(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _TRIAL_RE.fullmatch(value) else None


def _write_event(path: Path, event: Mapping[str, object]) -> None:
    """Append one bounded, content-safe telemetry record and flush it."""
    safe = {
        key: value
        for key, value in event.items()
        if key
        in {
            "event",
            "trial",
            "ordinal",
            "shape",
            "duration_ms",
            "success",
            "created",
            "deleted",
            "cleanup",
            "sandbox_count",
            "sandbox_seconds",
            "error_category",
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, ensure_ascii=True, separators=(",", ":")) + "\n")
        handle.flush()


def _shape(resources: Any, profile: object) -> tuple[int, int, int] | None:
    try:
        resolver = getattr(resources.platform, "spec_for_profile", None)
        # ``SandboxProvisioner`` omits ``profile`` for the normal Session
        # sandbox, relying on the platform's default.  Resolve that omission
        # to the platform's configured default spec instead of asking a
        # profile resolver to look up ``None``.
        spec = resolver(profile) if callable(resolver) and profile is not None else resources.sandbox_spec
        value = (int(spec.cpu), int(spec.memory_gib), int(spec.disk_gib))
    except (AttributeError, TypeError, ValueError):
        return None
    if any(item <= 0 for item in value):
        return None
    return value


class LifecycleObserver:
    """Patch the selected Daytona platform with a bounded local observer."""

    # Default root-close deadline. Shutdown runs interpreter settlement,
    # broker stop, sandbox delete, and binding persistence in sequence, and
    # provider deletes alone have been observed near seven seconds each, so
    # a tight deadline converts slow-but-complete cleanup into a safety
    # fault. Operators may raise it via the environment for diagnosis;
    # slowness then surfaces in the latency gates instead of masking as a
    # cleanup failure. Leaks (created != deleted) still fail regardless of
    # the deadline, and a genuinely hung close still fails closed here.
    CLOSE_DEADLINE_DEFAULT_SECONDS: Final = 120.0

    @staticmethod
    def close_deadline_seconds() -> float:
        """Return the operator-overridable root-close deadline in seconds."""
        try:
            configured = float(os.environ.get("FLEET_P4_CLOSE_DEADLINE_S", "").strip())
        except ValueError:
            return LifecycleObserver.CLOSE_DEADLINE_DEFAULT_SECONDS
        if not 1.0 <= configured <= 600.0:
            return LifecycleObserver.CLOSE_DEADLINE_DEFAULT_SECONDS
        return configured

    def __init__(self, app: Any, path: Path) -> None:
        self.app = app
        self.path = path
        self._ordinal = 0
        self._created: dict[str, tuple[int, str | None, float, tuple[int, int, int] | None]] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._attached = False

    def attach(self) -> None:
        if self._attached:
            return
        inventory = getattr(getattr(self.app, "state", None), "runtime_inventory", None)
        resources = getattr(inventory, "run_environment_resources", None)
        platform = getattr(resources, "platform", None)
        if platform is None:
            return
        original_create = getattr(platform, "create", None)
        original_delete = getattr(platform, "delete", None)
        if not callable(original_create) or not callable(original_delete):
            return

        async def observed_create(*args: object, **kwargs: object) -> object:
            result = await original_create(*args, **kwargs)
            identifier = getattr(result, "id", None)
            if identifier is not None:
                raw_identifier = str(identifier)
                self._ordinal += 1
                profile = kwargs.get("profile")
                resource_shape = _shape(resources, profile)
                trial = _TRIAL_CONTEXT.get()
                self._created[raw_identifier] = (self._ordinal, trial, time.perf_counter(), resource_shape)
                if trial is not None:
                    stats = self._stats.setdefault(
                        trial,
                        {"created": 0, "deleted": 0, "delete_failures": 0, "sandbox_seconds": 0, "shape": None},
                    )
                    stats["created"] += 1
                    if resource_shape is not None:
                        stats["shape"] = list(resource_shape)
                _write_event(
                    self.path,
                    {
                        "event": "sandbox_created",
                        "trial": trial,
                        "ordinal": self._ordinal,
                        "shape": list(resource_shape) if resource_shape is not None else None,
                    },
                )
            return result

        async def observed_delete(target: object) -> object:
            identifier = getattr(target, "id", None)
            raw_identifier = str(identifier) if identifier is not None else str(target)
            record = self._created.get(raw_identifier)
            succeeded = False
            try:
                result = await original_delete(target)
                succeeded = True
            except BaseException as exc:
                trial = record[1] if record is not None else _TRIAL_CONTEXT.get()
                if trial is not None:
                    stats = self._stats.setdefault(
                        trial,
                        {"created": 0, "deleted": 0, "delete_failures": 0, "sandbox_seconds": 0, "shape": None},
                    )
                    stats["delete_failures"] += 1
                _write_event(
                    self.path,
                    {
                        "event": "sandbox_deleted",
                        "trial": record[1] if record is not None else _TRIAL_CONTEXT.get(),
                        "ordinal": record[0] if record is not None else None,
                        "duration_ms": (
                            max(0, int((time.perf_counter() - record[2]) * 1000)) if record is not None else None
                        ),
                        "success": False,
                        "error_category": type(exc).__name__[:64],
                    },
                )
                raise
            finally:
                if record is not None and succeeded:
                    trial = record[1] if record is not None else _TRIAL_CONTEXT.get()
                    duration_ms = max(0, int((time.perf_counter() - record[2]) * 1000))
                    if trial is not None:
                        stats = self._stats.setdefault(
                            trial,
                            {"created": 0, "deleted": 0, "delete_failures": 0, "sandbox_seconds": 0, "shape": None},
                        )
                        stats["deleted"] += 1
                        stats["sandbox_seconds"] += duration_ms / 1000
                    _write_event(
                        self.path,
                        {
                            "event": "sandbox_deleted",
                            "trial": record[1] if record is not None else _TRIAL_CONTEXT.get(),
                            "ordinal": record[0] if record is not None else None,
                            "duration_ms": duration_ms,
                            "success": True,
                        },
                    )
                    self._created.pop(raw_identifier, None)
            return result

        platform.create = observed_create  # type: ignore[method-assign]
        platform.delete = observed_delete  # type: ignore[method-assign]
        self._attached = True

    async def close_turn_root(self, session_id: UUID) -> None:
        """Close the retained root belonging to one streamed campaign Turn."""
        inventory = getattr(getattr(self.app, "state", None), "runtime_inventory", None)
        resources = getattr(inventory, "run_environment_resources", None)
        runtime = getattr(resources, "runtime", None)
        close_root = getattr(runtime, "close_root_session", None)
        trial = _TRIAL_CONTEXT.get()
        from fleet_rlm.api.local_scope import LocalScope

        if not callable(close_root):
            _write_event(
                self.path,
                {"event": "turn_cleanup", "trial": trial, "cleanup": False, "error_category": "cleanup_unavailable"},
            )
            return
        try:
            await close_root(
                LocalScope().workspace_id, session_id, deadline=time.monotonic() + self.close_deadline_seconds()
            )
        except BaseException as exc:
            _write_event(
                self.path,
                {"event": "turn_cleanup", "trial": trial, "cleanup": False, "error_category": type(exc).__name__[:64]},
            )
            return
        stats = self._stats.pop(
            trial or "",
            {"created": 0, "deleted": 0, "delete_failures": 0, "sandbox_seconds": 0, "shape": None},
        )
        cleanup = stats["delete_failures"] == 0 and stats["created"] == stats["deleted"]
        _write_event(
            self.path,
            {
                "event": "turn_cleanup",
                "trial": trial,
                "created": stats["created"],
                "deleted": stats["deleted"],
                "cleanup": cleanup,
                "sandbox_count": stats["created"],
                "sandbox_seconds": int(stats["sandbox_seconds"] + 0.999),
                "shape": stats["shape"],
            },
        )


class CampaignMiddleware:
    """Attach trial context and close retained roots after streamed Turns."""

    def __init__(self, app: Any, observer: LifecycleObserver) -> None:
        self.app = app
        self.observer = observer

    async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", ()))
        raw_trial = headers.get(b"x-fleet-phase4-trial", b"").decode("ascii", "ignore")
        trial = _bounded_trial(raw_trial or None)
        path = scope.get("path")
        method = scope.get("method")
        match = _TURN_PATH_RE.fullmatch(path) if isinstance(path, str) else None
        try:
            session_id = UUID(match.group(1)) if match is not None else None
        except ValueError:
            session_id = None
        token = _TRIAL_CONTEXT.set(trial)
        finished = False

        async def observed_send(message: Mapping[str, object]) -> None:
            nonlocal finished
            await send(message)
            if (
                not finished
                and method == "POST"
                and session_id is not None
                and message.get("type") == "http.response.body"
                and not message.get("more_body", False)
            ):
                finished = True
                await self.observer.close_turn_root(session_id)

        try:
            return await self.app(scope, receive, observed_send)
        finally:
            _TRIAL_CONTEXT.reset(token)


def _campaign_settings(
    *,
    profile: str | None,
    recursive: bool,
    data_root: Path,
    database_url: str,
    volume_name: str,
) -> Any:
    import fleet_rlm.config.loader as loader

    load_settings = loader.load_runtime_settings
    try:
        accepts_profile = "profile" in inspect.signature(load_settings).parameters
    except (TypeError, ValueError):
        accepts_profile = False
    # The frozen baseline predates explicit profile selection. Its disposable
    # policy overlay sets the campaign profile as default.
    settings = load_settings(profile=profile) if profile is not None and accepts_profile else load_settings()
    # Only process-local overlays live here: the selected profile owns model,
    # decoding, budget, and lease policy. The recursion flag selects the
    # arm behavior (A/B run non-recursive, C/D recursive). MLflow tracing
    # stays exactly as the profile configures it: campaign trials require
    # live engineering traces on the profile's tracking server.
    update: dict[str, object] = {
        "rlm_recursion_enabled": recursive,
        "data_root": str(data_root),
        "database_url": database_url,
        "volume_name": volume_name,
    }
    return settings.model_copy(update=update)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--volume-name", required=True)
    parser.add_argument("--telemetry", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from fleet_rlm.app import create_app

    settings = _campaign_settings(
        profile=args.profile,
        recursive=args.recursive,
        data_root=args.data_root,
        database_url=args.database_url,
        volume_name=args.volume_name,
    )
    app = create_app(settings=settings)
    observer = LifecycleObserver(app, args.telemetry)

    class LifespanAttach:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped

        async def __call__(self, scope: Any, receive: Any, send: Any) -> Any:
            if scope.get("type") != "lifespan":
                return await self.wrapped(scope, receive, send)

            async def wrapped_send(message: Mapping[str, object]) -> None:
                if message.get("type") == "lifespan.startup.complete":
                    observer.attach()
                await send(message)

            return await self.wrapped(scope, receive, wrapped_send)

    wrapped_app = CampaignMiddleware(LifespanAttach(app), observer)
    uvicorn.run(wrapped_app, host=args.host, port=args.port, reload=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
