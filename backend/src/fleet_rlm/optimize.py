"""Offline GEPA and ReAnchor optimization CLI using isolated Neon branches."""

import argparse
import asyncio
import base64
import copy
import difflib
import importlib.util
import io
import json
import logging
import math
import os
import queue
import random
import re
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, AsyncExitStack, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Literal, Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

import dspy
import httpx
import mlflow
import mlflow.dspy
from dspy.experimental import ReAnchor
from dspy.primitives.code_interpreter import CodeExecutionError
from dspy.primitives.sandbox_guest import CALL_TOOL, LM_TOOL, TAG, WIRE
from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback
from gepa.utils.stop_condition import MaxMetricCallsStopper
from sqlalchemy import select

from . import agent, db, interpreter, skills
from .config import Settings

_LOG = logging.getLogger(__name__)

TargetKind = Literal["rlm", "skills", "memory"]
ProgramKind = Literal["rlm", "flex"]
SplitKind = Literal["train", "val", "holdout"]
RoleName = Literal["root_lm", "sub_lm", "reflection_lm", "judge_lm", "decision_lm"]
ApiMode = Literal["auto", "chat", "responses"]

DEFAULT_OUTPUTS: dict[TargetKind, Path] = {
    "rlm": Path("artifacts/rlm_program.json"),
    "skills": Path("artifacts/skill_selector.json"),
    "memory": Path("artifacts/memory_gate.json"),
}
DEFAULT_MAX_METRIC_CALLS = 80
DEFAULT_HOLDOUT_REPEATS = 3
_VALID_SPLITS = frozenset({"train", "val", "holdout"})
_VALID_VERIFIER_TYPES = frozenset({"exact", "json", "fact_checklist", "artifact", "rubric"})
_AUTO_APPROVE_THRESHOLD = 0.90
_MAX_CASE_DELTAS = 50
_MAX_CANDIDATE_HISTORY = 100
_MISSING = object()

_VALID_ROLE_NAMES: frozenset[str] = frozenset(
    {"root_lm", "sub_lm", "reflection_lm", "judge_lm", "decision_lm"}
)
_VALID_API_MODES: frozenset[str] = frozenset({"auto", "chat", "responses"})
_COLON_DIALECTS: frozenset[str] = frozenset({"openai-chat", "vllm", "sglang", "typesafe"})
_SLASH_DIALECTS: frozenset[str] = frozenset(
    {"openai", "anthropic", "gemini", "openrouter", "xai", "groq", "deepseek", "ollama"}
)
_OPENAI_COMPAT_DIALECTS: frozenset[str] = frozenset(
    {"openai_compatible", "openai-compatible", "openai-chat"}
)
_TERMINAL_STAGES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

_SECRET_SCAN_RE = re.compile(
    r"(?:"
    r"sk-(?:proj-|ant-|or-)?[A-Za-z0-9_-]{16,}"
    r"|dtn_[A-Za-z0-9_-]{12,}"
    r"|tvly-[A-Za-z0-9_-]{12,}"
    r"|napi_[A-Za-z0-9_-]{12,}"
    r"|xai-[A-Za-z0-9_-]{12,}"
    r"|gsk_[A-Za-z0-9_-]{12,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|postgres(?:ql)?(?:\+psycopg)?://[^\s\"']+"
    r"|https?://[^/\s:@\"'<>]+:[^/\s@\"'<>]+@[^\s\"']+"
    r"|Bearer\s+[A-Za-z0-9._-]{16,}"
    r"|(?:(?i:api[_-]?key|token|secret|password))=[^\s&\"']{6,}"
    r")"
)


def _scrub_secrets(text: str, extra_secrets: Sequence[str] = ()) -> str:
    cleaned = _SECRET_SCAN_RE.sub("[REDACTED]", text)
    for secret in extra_secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def _collect_settings_secrets(
    settings: Settings,
    *,
    database_url: str | None = None,
    role_models: Mapping[RoleName, "RoleModelConfig"] | None = None,
) -> list[str]:
    secrets: list[str] = []
    for raw in (settings.database_url, settings.database_direct_url, database_url):
        if raw:
            secrets.append(raw)
            try:
                parsed = urlsplit(raw)
                if parsed.password:
                    secrets.append(parsed.password)
            except ValueError:
                pass
    for attr in (
        "api_bearer_token",
        "daytona_api_key",
        "tavily_api_key",
        "s3_access_key_id",
        "s3_secret_access_key",
        "llm_api_key",
        "optimization_database_url",
        "neon_api_key",
    ):
        val = getattr(settings, attr, None)
        if val is not None and hasattr(val, "get_secret_value"):
            secret_text = str(val.get_secret_value() or "")
            if secret_text:
                secrets.append(secret_text)
    if role_models:
        for spec in role_models.values():
            if spec.api_key:
                secrets.append(spec.api_key)
            if spec.base_url:
                try:
                    parsed = urlsplit(spec.base_url)
                    if parsed.password or parsed.username:
                        secrets.append(spec.base_url)
                        if parsed.password:
                            secrets.append(parsed.password)
                except ValueError:
                    pass
    for env_key in (
        "TYPESAFE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        env_val = os.environ.get(env_key)
        if env_val:
            secrets.append(env_val)
    return list(dict.fromkeys(s for s in secrets if len(s) >= 4))


@dataclass(frozen=True)
class RoleModelConfig:
    """Resolved non-default model & credential binding for a single optimizer role."""

    model: str
    dialect: str | None = None
    api_mode: ApiMode = "auto"
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = field(default=None, repr=False)
    provider_name: str | None = None
    timeout: float | None = None
    reasoning_effort: str | None = None


def _infer_dialect(model: str) -> str | None:
    cleaned = model.strip()
    if not cleaned:
        return None
    colon_idx = cleaned.find(":")
    slash_idx = cleaned.find("/")
    if colon_idx > 0 and (slash_idx == -1 or colon_idx < slash_idx):
        return cleaned[:colon_idx].lower()
    if slash_idx > 0:
        return cleaned[:slash_idx].lower()
    return None


def _format_lm15_model(
    model: str,
    *,
    dialect: str | None,
    api_mode: ApiMode,
) -> tuple[str, ApiMode]:
    """Normalize a (dialect, api_mode, model) triple for DSPy 3.5 lm15 routing."""
    raw_model = model.strip()
    if not raw_model:
        raise ValueError("Model ID must be a non-empty string")
    norm_dialect = (dialect or "").strip().lower() or None

    if norm_dialect in _OPENAI_COMPAT_DIALECTS:
        effective_mode: ApiMode = "chat" if api_mode == "auto" else api_mode
        bare = raw_model
        for prefix in ("openai-chat:", "openai-chat/", "openai:", "openai/"):
            if bare.lower().startswith(prefix):
                bare = bare[len(prefix) :]
                break
        if effective_mode == "responses":
            routed = f"openai:{bare}" if "/" in bare else f"openai/{bare}"
            return routed, "responses"
        return f"openai-chat:{bare}", "chat"

    if norm_dialect == "openai":
        bare = raw_model
        for prefix in ("openai-chat:", "openai-chat/", "openai:", "openai/"):
            if bare.lower().startswith(prefix):
                bare = bare[len(prefix) :]
                break
        if api_mode == "chat":
            return f"openai-chat:{bare}", "chat"
        routed = f"openai:{bare}" if "/" in bare else f"openai/{bare}"
        return routed, api_mode

    if norm_dialect in _COLON_DIALECTS:
        bare = raw_model
        if bare.lower().startswith(f"{norm_dialect}:"):
            bare = bare[len(norm_dialect) + 1 :]
        elif bare.lower().startswith(f"{norm_dialect}/"):
            bare = bare[len(norm_dialect) + 1 :]
        return f"{norm_dialect}:{bare}", api_mode

    if norm_dialect in _SLASH_DIALECTS:
        lower = raw_model.lower()
        if lower.startswith(f"{norm_dialect}/") or lower.startswith(f"{norm_dialect}:"):
            return raw_model, api_mode
        return f"{norm_dialect}/{raw_model}", api_mode

    if norm_dialect is not None:
        lower = raw_model.lower()
        if lower.startswith(f"{norm_dialect}:") or lower.startswith(f"{norm_dialect}/"):
            return raw_model, api_mode
        return f"{norm_dialect}/{raw_model}", api_mode

    return raw_model, api_mode


def _build_role_lm(spec: RoleModelConfig) -> tuple[dspy.LM, str, ApiMode]:
    """Construct a DSPy 3.5 lm15-compatible LM using native client keys only."""
    routed_model, resolved_mode = _format_lm15_model(
        spec.model,
        dialect=spec.dialect,
        api_mode=spec.api_mode,
    )
    # Only pass DSPy 3.5 lm15 NATIVE_CLIENT_KEYS (api_key, api_base, timeout).
    lm_kwargs: dict[str, Any] = {}
    if spec.reasoning_effort:
        lm_kwargs["reasoning_effort"] = spec.reasoning_effort
    if resolved_mode in {"chat", "responses"}:
        lm_kwargs["model_type"] = resolved_mode
    if spec.api_key is not None and spec.api_key.strip():
        lm_kwargs["api_key"] = spec.api_key.strip()
    if spec.base_url is not None and spec.base_url.strip():
        lm_kwargs["api_base"] = spec.base_url.strip()
    if spec.timeout is not None:
        lm_kwargs["timeout"] = spec.timeout
    return dspy.LM(routed_model, **lm_kwargs), routed_model, resolved_mode


def _role_provenance(
    *,
    model: str,
    dialect: str | None = None,
    provider_name: str | None = None,
    extra_secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Return non-secret model/provider provenance safe for report_json."""
    resolved_dialect = (dialect or "").strip().lower() or _infer_dialect(model)
    safe_model = _scrub_secrets(model.strip(), extra_secrets)
    safe_provider = (
        _scrub_secrets(provider_name.strip(), extra_secrets)
        if isinstance(provider_name, str) and provider_name.strip()
        else None
    )
    return {
        "dialect": resolved_dialect,
        "model": safe_model,
        "provider_name": safe_provider,
    }


def load_role_credentials(path: Path) -> dict[RoleName, RoleModelConfig]:
    """Load per-role LLM credentials from a private JSON file."""
    if not path.is_file():
        raise FileNotFoundError(f"Credentials file not found: {path}")
    try:
        mode = path.stat().st_mode & 0o777
        if os.name == "posix" and mode & 0o077:
            _LOG.warning(
                "Credentials file %s has permissive mode %04o; expected 0600",
                path,
                mode,
            )
    except OSError:
        pass

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("Credentials file must contain a JSON object mapping role names to specs")

    configs: dict[RoleName, RoleModelConfig] = {}
    for role_key, entry in raw.items():
        if role_key not in _VALID_ROLE_NAMES:
            raise ValueError(
                f"Unknown role {role_key!r} in credentials file "
                f"(expected one of {sorted(_VALID_ROLE_NAMES)})"
            )
        if entry is None:
            continue
        if not isinstance(entry, Mapping):
            raise ValueError(f"Credentials entry for {role_key!r} must be an object")
        raw_model = entry.get("model")
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise ValueError(f"Credentials entry for {role_key!r} requires a nonempty 'model'")
        raw_dialect = entry.get("dialect")
        if raw_dialect is not None and not isinstance(raw_dialect, str):
            raise ValueError(f"Credentials entry for {role_key!r}: 'dialect' must be a string")
        dialect = raw_dialect.strip().lower() if isinstance(raw_dialect, str) else None
        if dialect == "":
            dialect = None

        raw_mode = entry.get("api_mode")
        if raw_mode is None or raw_mode == "":
            api_mode: ApiMode = "chat" if dialect in _OPENAI_COMPAT_DIALECTS else "auto"
        elif isinstance(raw_mode, str) and raw_mode.strip().lower() in _VALID_API_MODES:
            api_mode = cast(ApiMode, raw_mode.strip().lower())
        else:
            raise ValueError(
                f"Credentials entry for {role_key!r}: invalid api_mode {raw_mode!r} "
                f"(expected 'auto', 'chat', or 'responses')"
            )

        raw_key = entry.get("api_key")
        if raw_key is not None and not isinstance(raw_key, str):
            raise ValueError(f"Credentials entry for {role_key!r}: 'api_key' must be a string")
        api_key = raw_key.strip() if isinstance(raw_key, str) and raw_key.strip() else None

        raw_base = entry.get("base_url", entry.get("api_base"))
        if raw_base is not None and not isinstance(raw_base, str):
            raise ValueError(f"Credentials entry for {role_key!r}: 'base_url' must be a string")
        base_url = raw_base.strip() if isinstance(raw_base, str) and raw_base.strip() else None

        raw_provider = entry.get("provider_name", entry.get("name"))
        provider_name = (
            raw_provider.strip() if isinstance(raw_provider, str) and raw_provider.strip() else None
        )

        raw_timeout = entry.get("timeout")
        timeout: float | None = None
        if raw_timeout is not None:
            if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
                raise ValueError(f"Credentials entry for {role_key!r}: 'timeout' must be numeric")
            if float(raw_timeout) <= 0 or not math.isfinite(float(raw_timeout)):
                raise ValueError(f"Credentials entry for {role_key!r}: 'timeout' must be positive")
            timeout = float(raw_timeout)

        effort = entry.get("reasoning_effort")
        if effort is not None and effort not in ("low", "medium", "high", "xhigh"):
            raise ValueError(f"Credentials entry for {role_key!r}: invalid reasoning effort")
        configs[cast(RoleName, role_key)] = RoleModelConfig(
            model=raw_model.strip(),
            reasoning_effort=effort,
            dialect=dialect,
            api_mode=api_mode,
            api_key=api_key,
            base_url=base_url,
            provider_name=provider_name,
            timeout=timeout,
        )
    return configs


class _MlflowTracking(Protocol):
    def set_tracking_uri(self, uri: str) -> None: ...
    def set_experiment(self, experiment_name: str) -> Any: ...
    def start_run(self, *, run_name: str | None = None) -> AbstractContextManager[Any]: ...
    def log_params(self, params: dict[str, Any]) -> None: ...
    def log_metrics(self, metrics: dict[str, float]) -> None: ...
    def log_artifact(self, local_path: str) -> None: ...


def _mlflow_api() -> _MlflowTracking:
    # MLflow exports tracking helpers conditionally for its tracing-only SDK, so access
    # the module through a typed protocol while preserving monkeypatchable module lookups.
    return cast(_MlflowTracking, mlflow)


_DATABASE_SAFETY_MESSAGE = (
    "Refusing to run optimization against configured database_url/database_direct_url. "
    "Pass an ephemeral Neon branch URL or --allow-direct to override."
)


class DatabaseSafetyError(ValueError):
    """Raised when an optimization run targets the configured primary database."""


@dataclass(frozen=True)
class OptimizationResult:
    target: TargetKind
    output_path: Path
    saved: bool
    baseline_score: float | None
    optimized_score: float | None
    train_size: int
    holdout_size: int
    val_size: int = 0
    reminder: str | None = None
    mlflow_run_id: str | None = None
    report: dict[str, Any] = field(default_factory=dict)


class _ProgressEmitter:
    """Thread-safe JSONL writer for live UI progress tracking with cumulative stage timeline."""

    def __init__(
        self,
        path: Path | None,
        *,
        extra_secrets: Sequence[str] = (),
    ) -> None:
        self._path = path
        self.enabled = path is not None
        self._lock = threading.Lock()
        self._extra_secrets: list[str] = [s for s in extra_secrets if s]
        self._timeline: list[dict[str, Any]] = []
        self._timeline_started_mono: list[float] = []
        self._last_baseline_score: float | None = None
        self._last_best_score: float | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def add_secrets(self, secrets: Sequence[str]) -> None:
        with self._lock:
            existing = set(self._extra_secrets)
            for secret in secrets:
                if secret and secret not in existing:
                    self._extra_secrets.append(secret)
                    existing.add(secret)

    def timeline(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._timeline]

    def _update_timeline_locked(
        self,
        stage: str,
        label: str,
        status: Literal["running", "completed", "failed", "cancelled"],
        now_iso: str,
        now_mono: float,
    ) -> list[dict[str, Any]]:
        is_terminal = status in _TERMINAL_STAGES or stage in _TERMINAL_STAGES
        if is_terminal:
            terminal_status: Literal["completed", "failed", "cancelled"] = (
                "cancelled"
                if status == "cancelled" or stage == "cancelled"
                else "failed"
                if status == "failed" or stage == "failed"
                else "completed"
            )
            if self._timeline and self._timeline[-1]["status"] == "running":
                start_mono = self._timeline_started_mono[-1]
                self._timeline[-1]["status"] = terminal_status
                self._timeline[-1]["finished_at"] = now_iso
                self._timeline[-1]["duration_seconds"] = round(max(0.0, now_mono - start_mono), 3)
            elif not self._timeline:
                self._timeline.append(
                    {
                        "stage": stage,
                        "label": label,
                        "status": terminal_status,
                        "started_at": now_iso,
                        "finished_at": now_iso,
                        "duration_seconds": 0.0,
                    }
                )
                self._timeline_started_mono.append(now_mono)
        else:
            if self._timeline and self._timeline[-1]["stage"] == stage:
                start_mono = self._timeline_started_mono[-1]
                self._timeline[-1]["label"] = label
                self._timeline[-1]["status"] = "running"
                self._timeline[-1]["finished_at"] = None
                self._timeline[-1]["duration_seconds"] = round(max(0.0, now_mono - start_mono), 3)
            else:
                if self._timeline and self._timeline[-1]["status"] == "running":
                    prev_start = self._timeline_started_mono[-1]
                    self._timeline[-1]["status"] = "completed"
                    self._timeline[-1]["finished_at"] = now_iso
                    self._timeline[-1]["duration_seconds"] = round(
                        max(0.0, now_mono - prev_start), 3
                    )
                self._timeline.append(
                    {
                        "stage": stage,
                        "label": label,
                        "status": "running",
                        "started_at": now_iso,
                        "finished_at": None,
                        "duration_seconds": 0.0,
                    }
                )
                self._timeline_started_mono.append(now_mono)
        return [dict(item) for item in self._timeline]

    def emit(
        self,
        stage: str,
        label: str,
        *,
        status: Literal["running", "completed", "failed", "cancelled"] = "running",
        completed: int | None = None,
        total: int | None = None,
        baseline_score: float | None = None,
        best_score: float | None = None,
        message: str | None = None,
    ) -> None:
        if self._path is None or not self.enabled:
            return
        with self._lock:
            now_iso = datetime.now(UTC).isoformat()
            now_mono = time.monotonic()
            if baseline_score is not None:
                self._last_baseline_score = baseline_score
            if best_score is not None:
                self._last_best_score = best_score
            safe_message = (
                _scrub_secrets(message, self._extra_secrets) if message is not None else None
            )
            stage_timeline = self._update_timeline_locked(stage, label, status, now_iso, now_mono)
            payload = {
                "stage": stage,
                "label": label,
                "status": status,
                "completed": completed,
                "total": total,
                "baseline_score": self._last_baseline_score,
                "best_score": self._last_best_score,
                "message": safe_message,
                "stage_timeline": stage_timeline,
                "ts": now_iso,
            }
            line = json.dumps(payload, default=str) + "\n"
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()


@dataclass
class _RolloutTelemetry:
    """TurnTelemetry-compatible execution metrics captured during an offline RLM rollout."""

    root_actions: int = 0
    child_rlms: int = 0
    sub_lm_queries: int = 0
    python_errors: int = 0
    web_calls: int = 0
    repeated_web_opens: int = 0
    web_exhausted: bool = False
    completion: Literal["submit", "iteration_limit", "unknown"] = "unknown"
    error_samples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _CaseSpec:
    case_id: str
    fixture_id: str | None = None
    fixtures_module_path: Path | None = None
    fixtures_fallback_dirs: tuple[Path, ...] = ()
    input_files: dict[str, bytes] = field(default_factory=dict)
    web_fixtures: dict[str, Any] | None = None
    enable_web_tools: bool = False
    enable_spawn_agent: bool = True


@dataclass(frozen=True)
class _RlmCaseOutcome:
    score: float
    correctness: float
    passed: bool
    feedback: str
    blocking: bool
    telemetry: dict[str, Any]


@dataclass
class _SandboxSlot:
    sandbox: Any
    factory: Callable[..., interpreter.DaytonaCodeInterpreter]
    loop: asyncio.AbstractEventLoop


class _PoolInterpreterFactory:
    """Pool-backed interpreter factory matching DaytonaCodeInterpreter.execution_instructions."""

    execution_instructions: str = interpreter.DaytonaCodeInterpreter.execution_instructions

    def __init__(self, pool: "_DaytonaSandboxPool") -> None:
        self._pool = pool

    def __call__(self, **kwargs: Any) -> Any:
        slot = self._pool._queue.get()
        try:
            interp = slot.factory(**kwargs)
        except BaseException:
            self._pool._queue.put(slot)
            raise
        original_shutdown = interp.shutdown
        released = False
        release_lock = threading.Lock()

        def release() -> None:
            nonlocal released
            with release_lock:
                if released:
                    return
                released = True
            self._pool._queue.put(slot)

        def shutdown_and_release() -> None:
            try:
                original_shutdown()
            finally:
                release()

        interp.shutdown = shutdown_and_release
        return interp


class _DaytonaSandboxPool:
    """Thread-safe pool of ephemeral Daytona sandboxes managed on a background event loop."""

    def __init__(self, slots: Sequence[_SandboxSlot], loop: asyncio.AbstractEventLoop) -> None:
        if not slots:
            raise ValueError("Daytona sandbox pool requires at least one sandbox slot")
        self.slots: tuple[_SandboxSlot, ...] = tuple(slots)
        self.loop = loop
        self.pool_size = len(self.slots)
        self._queue: queue.Queue[_SandboxSlot] = queue.Queue()
        for slot in self.slots:
            self._queue.put(slot)
        self.interpreter_factory: Callable[..., Any] = (
            self.slots[0].factory if self.pool_size == 1 else _PoolInterpreterFactory(self)
        )

    @contextmanager
    def checkout(self) -> Generator[_SandboxSlot]:
        slot = self._queue.get()
        try:
            yield slot
        finally:
            self._queue.put(slot)

    def run_coro[T](self, coro: Any) -> T:
        return cast(T, asyncio.run_coroutine_threadsafe(coro, self.loop).result())


def _normalize_database_url(url: str) -> str:
    raw = url.strip()
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    if not raw.startswith("postgresql+psycopg://"):
        raise ValueError("Expected a PostgreSQL URL using psycopg")
    parts = urlsplit(raw)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        netloc = f"{userinfo}@{hostport.lower()}"
    else:
        netloc = netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))


def _endpoint_key(normalized_url: str) -> tuple[str | None, int, str]:
    parts = urlsplit(normalized_url)
    host = parts.hostname.rstrip(".").lower() if parts.hostname else None
    port = parts.port if parts.port is not None else 5432
    path = parts.path or "/"
    return (host, port, path)


def guard_database_url(
    database_url: str,
    settings: Settings,
    *,
    allow_direct: bool = False,
) -> str:
    """Refuse to run against configured primary Neon URLs unless explicitly allowed."""
    normalized = _normalize_database_url(database_url)
    if allow_direct:
        return normalized

    configured = (
        _normalize_database_url(settings.database_url),
        _normalize_database_url(settings.database_direct_url),
    )
    endpoint = _endpoint_key(normalized)
    configured_endpoints = {_endpoint_key(item) for item in configured}
    if normalized in configured or endpoint in configured_endpoints:
        raise DatabaseSafetyError(_DATABASE_SAFETY_MESSAGE)
    return normalized


def _active_mlflow_run_id() -> str | None:
    active_run = getattr(mlflow, "active_run", None)
    if not callable(active_run):
        return None
    run = active_run()
    info = getattr(run, "info", None)
    run_id = getattr(info, "run_id", None)
    return str(run_id) if isinstance(run_id, str) and run_id else None


def _extract_predictor_instructions(module: Any) -> dict[str, str]:
    base = module.rlm if isinstance(module, _OfflineWorkspaceProgram) else module
    instructions: dict[str, str] = {}
    if hasattr(base, "named_predictors"):
        for name, pred in base.named_predictors():
            sig = getattr(pred, "signature", None)
            if sig is not None:
                instructions[str(name)] = agent.strip_dynamic_tool_docs(
                    str(getattr(sig, "instructions", "") or "")
                )
    return instructions


def _extract_reanchor_parameters(predictor: Any) -> dict[str, Any]:
    fields: Any = None
    try:
        state = predictor.dump_state() if hasattr(predictor, "dump_state") else {}
        if isinstance(state, Mapping):
            fields = state.get("fields")
    except Exception:
        fields = None
    if not isinstance(fields, Mapping):
        raw_fields = getattr(predictor, "fields", None)
        fields = raw_fields if isinstance(raw_fields, Mapping) else None
    if not isinstance(fields, Mapping):
        return {}
    extracted: dict[str, Any] = {}
    for field_name, cfg in fields.items():
        if not isinstance(cfg, Mapping):
            continue
        item: dict[str, Any] = {}
        if "threshold" in cfg and isinstance(cfg["threshold"], (int, float)):
            item["threshold"] = float(cfg["threshold"])
        if "weights" in cfg and isinstance(cfg["weights"], Mapping):
            item["weights"] = {
                str(option): float(weight)
                for option, weight in cfg["weights"].items()
                if isinstance(weight, (int, float)) and not isinstance(weight, bool)
            }
        if "cuts" in cfg and isinstance(cfg["cuts"], Sequence) and not isinstance(cfg["cuts"], str):
            item["cuts"] = [
                float(cut)
                for cut in cfg["cuts"]
                if isinstance(cut, (int, float)) and not isinstance(cut, bool)
            ]
        if item:
            extracted[str(field_name)] = item
    return extracted


def _build_reanchor_parameter_diffs(
    baseline_params: Mapping[str, Any],
    fitted_params: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build before->after parameter diff entries against baseline defaults (0.50 / 1.0)."""
    diffs: list[dict[str, Any]] = []
    gates = sorted(set(baseline_params) | set(fitted_params))
    for gate in gates:
        raw_base_cfg = baseline_params.get(gate)
        base_cfg: Mapping[str, Any] = raw_base_cfg if isinstance(raw_base_cfg, Mapping) else {}
        raw_fit_cfg = fitted_params.get(gate)
        fit_cfg: Mapping[str, Any] = raw_fit_cfg if isinstance(raw_fit_cfg, Mapping) else {}

        if "threshold" in base_cfg or "threshold" in fit_cfg:
            base_val = float(base_cfg.get("threshold", 0.50))
            fit_val = float(fit_cfg.get("threshold", base_val))
            diffs.append(
                {
                    "gate": gate,
                    "parameter": "threshold",
                    "baseline": round(base_val, 6),
                    "fitted": round(fit_val, 6),
                    "delta": round(fit_val - base_val, 6),
                }
            )

        raw_base_weights = base_cfg.get("weights")
        base_weights: Mapping[str, Any] = (
            raw_base_weights if isinstance(raw_base_weights, Mapping) else {}
        )
        raw_fit_weights = fit_cfg.get("weights")
        fit_weights: Mapping[str, Any] = (
            raw_fit_weights if isinstance(raw_fit_weights, Mapping) else {}
        )
        for option in sorted(set(base_weights) | set(fit_weights)):
            base_w = float(base_weights.get(option, 1.0))
            fit_w = float(fit_weights.get(option, base_w))
            diffs.append(
                {
                    "gate": gate,
                    "parameter": f"weight:{option}",
                    "baseline": round(base_w, 6),
                    "fitted": round(fit_w, 6),
                    "delta": round(fit_w - base_w, 6),
                }
            )
    return diffs


def _build_instruction_diffs(
    baseline_instructions: Mapping[str, str],
    optimized_instructions: Mapping[str, str],
) -> dict[str, str]:
    """Generate unified diff text for each predictor's instructions."""
    names = list(baseline_instructions.keys()) + [
        name for name in optimized_instructions if name not in baseline_instructions
    ]
    diffs: dict[str, str] = {}
    for name in names:
        before = str(baseline_instructions.get(name, "") or "")
        after = str(optimized_instructions.get(name, "") or "")
        diff_lines = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"baseline/{name}",
                tofile=f"optimized/{name}",
                lineterm="",
            )
        )
        diffs[name] = "\n".join(diff_lines)
    return diffs


def _extract_gepa_candidate_history(
    detailed: Any,
    *,
    extra_secrets: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Safely extract structured GEPA candidate trajectory from compiled.detailed_results."""
    if detailed is None:
        return []

    raw_scores = getattr(detailed, "val_aggregate_scores", None)
    raw_parents = getattr(detailed, "parents", None)
    raw_counts = getattr(detailed, "discovery_eval_counts", None)
    raw_candidates = getattr(detailed, "candidates", None)
    raw_reflections = getattr(detailed, "reflection_summaries", None)
    if raw_reflections is None:
        raw_reflections = getattr(detailed, "reflections", None)
    raw_accepted = getattr(detailed, "accepted", None)

    scores: list[Any] = (
        list(raw_scores)
        if isinstance(raw_scores, Sequence) and not isinstance(raw_scores, (str, bytes))
        else []
    )
    parents: list[Any] = (
        list(raw_parents)
        if isinstance(raw_parents, Sequence) and not isinstance(raw_parents, (str, bytes))
        else []
    )
    counts: list[Any] = (
        list(raw_counts)
        if isinstance(raw_counts, Sequence) and not isinstance(raw_counts, (str, bytes))
        else []
    )
    candidates: list[Any] = (
        list(raw_candidates)
        if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes))
        else []
    )
    reflections: list[Any] = (
        list(raw_reflections)
        if isinstance(raw_reflections, Sequence) and not isinstance(raw_reflections, (str, bytes))
        else []
    )
    accepted_flags: list[Any] = (
        list(raw_accepted)
        if isinstance(raw_accepted, Sequence) and not isinstance(raw_accepted, (str, bytes))
        else []
    )

    total = max(len(scores), len(candidates))
    if total == 0:
        return []

    numeric_scores: list[float | None] = []
    for idx in range(total):
        val = scores[idx] if idx < len(scores) else None
        if (
            isinstance(val, (int, float))
            and not isinstance(val, bool)
            and math.isfinite(float(val))
        ):
            numeric_scores.append(round(float(val), 6))
        else:
            numeric_scores.append(None)

    raw_best_idx = getattr(detailed, "best_idx", None)
    if (
        isinstance(raw_best_idx, int)
        and not isinstance(raw_best_idx, bool)
        and 0 <= raw_best_idx < total
    ):
        best_idx: int | None = raw_best_idx
    else:
        valid_pairs = [(idx, sc) for idx, sc in enumerate(numeric_scores) if sc is not None]
        best_idx = max(valid_pairs, key=lambda pair: pair[1])[0] if valid_pairs else None

    cand_instructions: list[dict[str, str]] = []
    for idx in range(total):
        if idx < len(candidates):
            try:
                cand_instructions.append(_extract_predictor_instructions(candidates[idx]))
            except Exception:
                cand_instructions.append({})
        else:
            cand_instructions.append({})

    history: list[dict[str, Any]] = []
    for idx in range(total):
        val_score = numeric_scores[idx]
        parent_list: list[int] = []
        if idx < len(parents):
            raw_parent = parents[idx]
            if isinstance(raw_parent, int) and not isinstance(raw_parent, bool):
                parent_list = [raw_parent]
            elif isinstance(raw_parent, Sequence) and not isinstance(raw_parent, (str, bytes)):
                parent_list = [
                    p
                    for p in raw_parent
                    if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < total
                ]
        parent_idx = parent_list[0] if parent_list else None

        metric_calls: int | None = None
        if idx < len(counts):
            raw_count = counts[idx]
            if isinstance(raw_count, int) and not isinstance(raw_count, bool):
                metric_calls = raw_count

        if idx < len(accepted_flags) and isinstance(accepted_flags[idx], bool):
            accepted = accepted_flags[idx]
        elif idx == 0 or parent_idx is None:
            accepted = True
        else:
            parent_score = numeric_scores[parent_idx]
            accepted = (
                True
                if (val_score is not None and (parent_score is None or val_score > parent_score))
                or idx == best_idx
                else False
            )

        reflection_summary: str | None = None
        if (
            idx < len(reflections)
            and isinstance(reflections[idx], str)
            and reflections[idx].strip()
        ):
            reflection_summary = _scrub_secrets(reflections[idx].strip(), extra_secrets)[:300]
        elif idx == 0:
            reflection_summary = "Baseline seed candidate"
        elif parent_idx is not None and cand_instructions[idx] and cand_instructions[parent_idx]:
            changed_parts: list[str] = []
            for pred_name, after_text in cand_instructions[idx].items():
                before_text = cand_instructions[parent_idx].get(pred_name, "")
                if before_text != after_text:
                    added = 0
                    removed = 0
                    for line in difflib.unified_diff(
                        before_text.splitlines(), after_text.splitlines(), lineterm=""
                    ):
                        if line.startswith("+") and not line.startswith("+++"):
                            added += 1
                        elif line.startswith("-") and not line.startswith("---"):
                            removed += 1
                    changed_parts.append(f"{pred_name} (+{added}/-{removed} lines)")
            if changed_parts:
                reflection_summary = f"Mutated {', '.join(changed_parts)}"[:300]

        history.append(
            {
                "candidate_idx": idx,
                "val_score": val_score,
                "parent": parent_idx,
                "parents": parent_list,
                "accepted": accepted,
                "is_best": idx == best_idx,
                "metric_calls": metric_calls,
                "reflection_summary": reflection_summary,
            }
        )

    if len(history) <= _MAX_CANDIDATE_HISTORY:
        return history
    pinned_indices = {0, len(history) - 1}
    if best_idx is not None:
        pinned_indices.add(best_idx)
    tail_slots = _MAX_CANDIDATE_HISTORY - len(pinned_indices)
    selected = sorted(pinned_indices | set(range(max(0, len(history) - tail_slots), len(history))))
    return [history[idx] for idx in selected[:_MAX_CANDIDATE_HISTORY]]


def _build_reanchor_gate_checks(
    *,
    fitted_changed: bool,
    fitted_fields: Sequence[str],
    score_improved: bool,
    baseline_score: float | None,
    optimized_score: float | None,
    fp_non_regressing: bool,
    fp_rate_before: float | None,
    fp_rate_after: float | None,
    has_holdout: bool,
) -> list[dict[str, Any]]:
    score_reason: str
    if baseline_score is None or optimized_score is None:
        score_reason = "Missing baseline or candidate holdout score"
    elif score_improved:
        score_reason = f"Holdout score improved from {baseline_score:.4f} to {optimized_score:.4f}"
    else:
        score_reason = (
            f"Holdout score {optimized_score:.4f} did not exceed baseline {baseline_score:.4f}"
        )

    if not has_holdout or fp_rate_before is None or fp_rate_after is None:
        fp_reason = "No holdout FP rate available; check waived"
        fp_threshold = "n/a"
    elif fp_non_regressing:
        fp_reason = f"FP rate {fp_rate_after:.4f} <= baseline {fp_rate_before:.4f}"
        fp_threshold = f"<= {fp_rate_before:.4f}"
    else:
        fp_reason = f"FP rate regressed from {fp_rate_before:.4f} to {fp_rate_after:.4f}"
        fp_threshold = f"<= {fp_rate_before:.4f}"

    return [
        {
            "name": "parameters_changed",
            "label": "Parameters changed from baseline",
            "passed": bool(fitted_changed),
            "actual": len(fitted_fields),
            "threshold": ">= 1 fitted field",
            "reason": (
                f"Calibrated {len(fitted_fields)} field(s): {', '.join(fitted_fields)}"
                if fitted_changed
                else "ReAnchor kept baseline defaults (all candidate fields skipped)"
            ),
        },
        {
            "name": "holdout_score_improved",
            "label": "Holdout score improved over baseline",
            "passed": bool(score_improved),
            "actual": optimized_score,
            "threshold": f"> {baseline_score:.4f}" if baseline_score is not None else "> baseline",
            "reason": score_reason,
        },
        {
            "name": "fp_rate_non_regression",
            "label": "False-positive rate did not regress",
            "passed": bool(fp_non_regressing),
            "actual": fp_rate_after,
            "threshold": fp_threshold,
            "reason": fp_reason,
        },
    ]


def _build_rlm_gate_checks(
    *,
    instructions_changed: bool,
    baseline_score: float,
    optimized_score: float,
    baseline_correctness: float,
    candidate_correctness: float,
    blocking_failure_count: int,
    paired_regression_count: int,
) -> list[dict[str, Any]]:
    score_improved = optimized_score > baseline_score
    correctness_ok = candidate_correctness >= baseline_correctness
    zero_blocking = blocking_failure_count == 0
    zero_regressions = paired_regression_count == 0
    return [
        {
            "name": "instructions_changed",
            "label": "Instructions changed from baseline",
            "passed": bool(instructions_changed),
            "actual": bool(instructions_changed),
            "threshold": True,
            "reason": (
                "Compiled program modified instructions or module source"
                if instructions_changed
                else "Compiled program instructions are identical to baseline"
            ),
        },
        {
            "name": "holdout_score_improved",
            "label": "Holdout score improved over baseline",
            "passed": bool(score_improved),
            "actual": optimized_score,
            "threshold": f"> {baseline_score:.2f}",
            "reason": (
                f"Holdout score improved from {baseline_score:.2f} to {optimized_score:.2f}"
                if score_improved
                else (
                    f"Holdout score {optimized_score:.2f} did not beat "
                    f"baseline {baseline_score:.2f}"
                )
            ),
        },
        {
            "name": "correctness_non_regression",
            "label": "Correctness did not regress",
            "passed": bool(correctness_ok),
            "actual": candidate_correctness,
            "threshold": f">= {baseline_correctness:.4f}",
            "reason": (
                f"Correctness {candidate_correctness:.4f} >= baseline {baseline_correctness:.4f}"
                if correctness_ok
                else (
                    f"Correctness regressed from {baseline_correctness:.4f} "
                    f"to {candidate_correctness:.4f}"
                )
            ),
        },
        {
            "name": "zero_blocking_failures",
            "label": "Zero blocking case failures",
            "passed": bool(zero_blocking),
            "actual": blocking_failure_count,
            "threshold": 0,
            "reason": (
                "All blocking holdout cases passed"
                if zero_blocking
                else f"{blocking_failure_count} blocking holdout failure(s) detected"
            ),
        },
        {
            "name": "zero_paired_regressions",
            "label": "Zero paired case regressions",
            "passed": bool(zero_regressions),
            "actual": paired_regression_count,
            "threshold": 0,
            "reason": (
                "No holdout case pass rate regressed vs baseline"
                if zero_regressions
                else f"{paired_regression_count} holdout case(s) regressed vs baseline"
            ),
        },
    ]


def _build_reanchor_case_deltas(
    before_records: Sequence[Mapping[str, Any]],
    after_records: Sequence[Mapping[str, Any]],
    *,
    limit: int = _MAX_CASE_DELTAS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair baseline vs candidate ReAnchor holdout evaluations into bounded delta lists."""
    regressed: list[dict[str, Any]] = []
    fixed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for before, after in zip(before_records, after_records, strict=False):
        b_score = float(before.get("score", 0.0))
        a_score = float(after.get("score", 0.0))
        b_passed = bool(before.get("passed", False))
        a_passed = bool(after.get("passed", False))
        if a_score > b_score or (not b_passed and a_passed):
            outcome: Literal["fixed", "regressed", "unchanged"] = "fixed"
        elif a_score < b_score or (b_passed and not a_passed):
            outcome = "regressed"
        else:
            outcome = "unchanged"
        row = {
            "case_id": before.get("case_id"),
            "lineage": before.get("lineage"),
            "input_preview": before.get("input_preview", ""),
            "expected": before.get("expected"),
            "baseline_pred": before.get("pred_label"),
            "baseline_prob": before.get("pred_prob"),
            "baseline_score": round(b_score, 6),
            "candidate_pred": after.get("pred_label"),
            "candidate_prob": after.get("pred_prob"),
            "candidate_score": round(a_score, 6),
            "delta": round(a_score - b_score, 6),
            "outcome": outcome,
        }
        if outcome == "regressed":
            regressed.append(row)
        elif outcome == "fixed":
            fixed.append(row)
        else:
            unchanged.append(row)
    combined = (regressed + fixed + unchanged)[:limit]
    return combined, fixed[:limit], regressed[:limit]


def _build_rlm_case_deltas(
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    holdout_examples: Sequence[dspy.Example],
    *,
    blocking_failures: Sequence[Mapping[str, Any]],
    paired_regressions: Sequence[Mapping[str, Any]],
    limit: int = _MAX_CASE_DELTAS,
) -> list[dict[str, Any]]:
    """Join baseline & candidate holdout rows per case_id, pinning blocking/regressed first."""
    if not baseline_rows and not candidate_rows:
        return []

    preview_by_id: dict[str, str] = {}
    lineage_by_id: dict[str, str | None] = {}
    blocking_by_id: dict[str, bool] = {}
    for index, ex in enumerate(holdout_examples, 1):
        cid = str(getattr(ex, "case_id", None) or f"row-{index}")
        preview_by_id[cid] = str(getattr(ex, "task", "") or "")[:180]
        lineage_by_id[cid] = _example_lineage(ex)
        blocking_by_id[cid] = bool(getattr(ex, "blocking", False))

    base_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    cand_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in baseline_rows:
        base_by_id[str(row["case_id"])].append(row)
    for row in candidate_rows:
        cand_by_id[str(row["case_id"])].append(row)

    blocking_failed_ids = {str(row.get("case_id")) for row in blocking_failures}
    paired_reg_ids = {str(row.get("case_id")) for row in paired_regressions}

    deltas: list[dict[str, Any]] = []
    for case_id in sorted(set(base_by_id) | set(cand_by_id)):
        b_rows = base_by_id.get(case_id, [])
        c_rows = cand_by_id.get(case_id, [])
        b_score = sum(float(r["score"]) for r in b_rows) / len(b_rows) if b_rows else 0.0
        c_score = sum(float(r["score"]) for r in c_rows) / len(c_rows) if c_rows else 0.0
        b_corr = sum(float(r["correctness"]) for r in b_rows) / len(b_rows) if b_rows else 0.0
        c_corr = sum(float(r["correctness"]) for r in c_rows) / len(c_rows) if c_rows else 0.0
        b_pass = sum(1 for r in b_rows if r.get("passed")) / len(b_rows) if b_rows else 0.0
        c_pass = sum(1 for r in c_rows if r.get("passed")) / len(c_rows) if c_rows else 0.0

        is_blocking = blocking_by_id.get(
            case_id,
            any(bool(r.get("blocking")) for r in (*b_rows, *c_rows)),
        )
        blocking_failed = case_id in blocking_failed_ids
        paired_regression = case_id in paired_reg_ids

        if paired_regression or blocking_failed or c_score < b_score:
            outcome: Literal["fixed", "regressed", "unchanged"] = "regressed"
        elif c_pass > b_pass or c_score > b_score:
            outcome = "fixed"
        else:
            outcome = "unchanged"

        b_fb = next((str(r.get("feedback") or "") for r in b_rows if r.get("feedback")), "")
        c_fb = next(
            (
                str(r.get("feedback") or "")
                for r in c_rows
                if not r.get("passed") and r.get("feedback")
            ),
            next((str(r.get("feedback") or "") for r in c_rows if r.get("feedback")), ""),
        )
        lineage = lineage_by_id.get(case_id)
        if lineage is None:
            lineage = next(
                (r.get("lineage") for r in (*c_rows, *b_rows) if r.get("lineage") is not None),
                None,
            )

        deltas.append(
            {
                "case_id": case_id,
                "lineage": lineage,
                "input_preview": preview_by_id.get(case_id, ""),
                "blocking": is_blocking,
                "blocking_failed": blocking_failed,
                "paired_regression": paired_regression,
                "baseline_score": round(b_score, 6),
                "candidate_score": round(c_score, 6),
                "baseline_correctness": round(b_corr, 6),
                "candidate_correctness": round(c_corr, 6),
                "baseline_pass_rate": round(b_pass, 6),
                "candidate_pass_rate": round(c_pass, 6),
                "delta": round(c_score - b_score, 6),
                "repeats": len(c_rows) or len(b_rows),
                "outcome": outcome,
                "baseline_feedback": b_fb[:240],
                "candidate_feedback": c_fb[:240],
            }
        )

    def sort_key(item: Mapping[str, Any]) -> tuple[int, float, str]:
        if item["blocking_failed"]:
            rank = 0
        elif item["paired_regression"]:
            rank = 1
        elif item["outcome"] == "regressed":
            rank = 2
        elif item["outcome"] == "fixed":
            rank = 3
        else:
            rank = 4
        return (rank, -abs(float(item["delta"])), str(item["case_id"]))

    deltas.sort(key=sort_key)
    return deltas[:limit]


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, default=str) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(raw)
        tmp.flush()
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


@contextmanager
def _mlflow_tracking(settings: Settings, target: TargetKind) -> Generator[bool]:
    if not settings.mlflow_tracking_uri:
        yield False
        return
    tracking = _mlflow_api()
    tracking.set_tracking_uri(settings.mlflow_tracking_uri)
    tracking.set_experiment(settings.mlflow_experiment)
    mlflow.dspy.autolog(
        log_compiles=True,
        log_evals=True,
        log_traces_from_compile=True,
    )
    try:
        with tracking.start_run(run_name=f"optimize-{target}"):
            yield True
    finally:
        mlflow.dspy.autolog(disable=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Dataset file not found: {path}")
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError(f"{path}:{line_no}: expected a JSON object")
        rows.append(parsed)
    return rows


def _optional_split(row: Mapping[str, Any], *, path: Path, index: int) -> SplitKind | None:
    raw = row.get("split")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or raw.strip() not in _VALID_SPLITS:
        raise ValueError(
            f"{path}:{index}: invalid split {raw!r} (expected 'train', 'val', or 'holdout')"
        )
    return cast(SplitKind, raw.strip())


def _optional_lineage(row: Mapping[str, Any], *, path: Path, index: int) -> str | None:
    raw = row.get("lineage")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{path}:{index}: 'lineage' must be a nonempty string when set")
    return raw.strip()


def _example_split(example: dspy.Example) -> str | None:
    raw = getattr(example, "split", None)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _example_lineage(example: dspy.Example) -> str | None:
    raw = getattr(example, "lineage", None)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _validate_disjoint_lineages(*partitions: Sequence[dspy.Example]) -> None:
    seen: dict[str, int] = {}
    for split_idx, part in enumerate(partitions):
        for example in part:
            lineage = _example_lineage(example)
            if lineage is None:
                continue
            prev = seen.get(lineage)
            if prev is not None and prev != split_idx:
                raise ValueError(f"Lineage {lineage!r} leaks across multiple dataset splits")
            seen[lineage] = split_idx


def _group_by_lineage(examples: Sequence[dspy.Example]) -> list[list[dspy.Example]]:
    grouped: dict[str, list[dspy.Example]] = {}
    order: list[str] = []
    for index, example in enumerate(examples):
        lineage = _example_lineage(example)
        key = f"lineage:{lineage}" if lineage is not None else f"row:{index}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(example)
    return [grouped[key] for key in order]


def split_examples(
    examples: Sequence[dspy.Example],
    *,
    train_ratio: float = 0.7,
    seed: int = 0,
    stratify_key: Callable[[dspy.Example], Any] | None = None,
) -> tuple[list[dspy.Example], list[dspy.Example]]:
    """Deterministically split examples into non-empty, lineage-disjoint train and holdout sets.

    Honors explicit `split` attributes ("train" vs "holdout"/"val") when present on all examples,
    preserves `lineage` grouping when present, and supports optional stratification.
    """
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train_ratio must be strictly between 0 and 1")
    if len(examples) < 2:
        raise ValueError("At least 2 examples are required to split train and holdout sets")

    splits = [_example_split(ex) for ex in examples]
    if any(s is not None for s in splits):
        if not all(s is not None for s in splits):
            raise ValueError("Either all examples or no examples may specify 'split'")
        train = [ex for ex, s in zip(examples, splits, strict=True) if s == "train"]
        holdout = [ex for ex, s in zip(examples, splits, strict=True) if s in {"holdout", "val"}]
        if not train or not holdout:
            raise ValueError(
                "Explicit splits must contain at least one 'train' and one 'holdout' example"
            )
        _validate_disjoint_lineages(train, holdout)
        return train, holdout

    has_lineage = any(_example_lineage(ex) is not None for ex in examples)
    if not has_lineage and stratify_key is None:
        items = list(examples)
        random.Random(seed).shuffle(items)
        split_at = max(1, min(len(items) - 1, round(len(items) * train_ratio)))
        return items[:split_at], items[split_at:]

    groups = _group_by_lineage(examples)
    if len(groups) < 2:
        raise ValueError(
            "At least 2 distinct lineages are required to split train and holdout sets"
        )

    rng = random.Random(seed)
    target_train = max(1, min(len(examples) - 1, round(len(examples) * train_ratio)))

    if stratify_key is not None:
        strata: dict[Any, list[list[dspy.Example]]] = defaultdict(list)
        for group in groups:
            strata[stratify_key(group[0])].append(group)
        train: list[dspy.Example] = []
        holdout: list[dspy.Example] = []
        leftovers: list[list[dspy.Example]] = []
        for stratum_key in sorted(strata, key=repr):
            stratum_groups = list(strata[stratum_key])
            rng.shuffle(stratum_groups)
            if len(stratum_groups) == 1:
                leftovers.extend(stratum_groups)
                continue
            stratum_total = sum(len(g) for g in stratum_groups)
            stratum_target = max(1, min(stratum_total - 1, round(stratum_total * train_ratio)))
            s_train: list[dspy.Example] = []
            s_holdout: list[dspy.Example] = []
            for idx, group in enumerate(stratum_groups):
                remaining_groups = len(stratum_groups) - idx
                if not s_train:
                    s_train.extend(group)
                elif not s_holdout and remaining_groups == 1:
                    s_holdout.extend(group)
                elif len(s_train) + len(group) <= stratum_target and remaining_groups > 1:
                    s_train.extend(group)
                else:
                    s_holdout.extend(group)
            train.extend(s_train)
            holdout.extend(s_holdout)

        rng.shuffle(leftovers)
        for idx, group in enumerate(leftovers):
            remaining = len(leftovers) - idx
            if not train:
                train.extend(group)
            elif not holdout and remaining == 1:
                holdout.extend(group)
            elif len(train) < target_train and (holdout or remaining > 1):
                train.extend(group)
            else:
                holdout.extend(group)
    else:
        rng.shuffle(groups)
        train = []
        holdout = []
        for idx, group in enumerate(groups):
            remaining = len(groups) - idx
            if not train:
                train.extend(group)
            elif not holdout and remaining == 1:
                holdout.extend(group)
            elif len(train) + len(group) <= target_train and remaining > 1:
                train.extend(group)
            else:
                holdout.extend(group)

    if not train or not holdout:
        raise ValueError("Unable to construct non-empty train and holdout splits")
    _validate_disjoint_lineages(train, holdout)
    return train, holdout


def split_train_val_holdout(
    examples: Sequence[dspy.Example],
    *,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    seed: int = 0,
) -> tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Deterministically split examples into non-empty, lineage-disjoint train/val/holdout sets.

    Honors explicit `split` attributes ("train"|"val"|"holdout") when present on all examples,
    and keeps rows with the same `lineage` in a single split.
    """
    if not math.isfinite(train_ratio) or not math.isfinite(val_ratio):
        raise ValueError("train_ratio and val_ratio must be finite numbers")
    if train_ratio <= 0.0 or val_ratio <= 0.0 or train_ratio + val_ratio >= 1.0:
        raise ValueError(
            "train_ratio and val_ratio must be positive and sum to strictly less than 1"
        )
    total = len(examples)
    if total < 3:
        raise ValueError(
            "At least 3 examples are required to split train, validation, and holdout sets"
        )

    splits = [_example_split(ex) for ex in examples]
    if any(s is not None for s in splits):
        if not all(s is not None for s in splits):
            raise ValueError("Either all examples or no examples may specify 'split'")
        train = [ex for ex, s in zip(examples, splits, strict=True) if s == "train"]
        val = [ex for ex, s in zip(examples, splits, strict=True) if s == "val"]
        holdout = [ex for ex, s in zip(examples, splits, strict=True) if s == "holdout"]
        if not train or not val or not holdout:
            raise ValueError(
                "Explicit 3-way splits must include non-empty 'train', 'val', and 'holdout' sets"
            )
        _validate_disjoint_lineages(train, val, holdout)
        return train, val, holdout

    has_lineage = any(_example_lineage(ex) is not None for ex in examples)
    if not has_lineage:
        items = list(examples)
        random.Random(seed).shuffle(items)
        train_count = max(1, min(total - 2, round(total * train_ratio)))
        remaining = total - train_count
        val_count = max(1, min(remaining - 1, round(total * val_ratio)))
        return (
            items[:train_count],
            items[train_count : train_count + val_count],
            items[train_count + val_count :],
        )

    groups = _group_by_lineage(examples)
    if len(groups) < 3:
        raise ValueError(
            "At least 3 distinct lineages are required to split train, validation, and holdout sets"
        )
    random.Random(seed).shuffle(groups)
    train_target = max(1, min(total - 2, round(total * train_ratio)))
    val_target = max(1, min(total - train_target - 1, round(total * val_ratio)))
    holdout_target = max(1, total - train_target - val_target)

    # Guarantee one group per split first, then assign remaining groups to the split
    # farthest below its target fraction.
    buckets: list[list[dspy.Example]] = [list(groups[0]), list(groups[1]), list(groups[2])]
    targets = (train_target, val_target, holdout_target)
    for group in groups[3:]:
        best_bucket = min(
            range(3),
            key=lambda idx: (len(buckets[idx]) / targets[idx], len(buckets[idx]), idx),
        )
        buckets[best_bucket].extend(group)

    train, val, holdout = buckets
    _validate_disjoint_lineages(train, val, holdout)
    return train, val, holdout


_FIXTURE_MODULE_CACHE: dict[Path, Any] = {}
_FIXTURE_CACHE_LOCK = threading.Lock()


def _load_fixtures_module(module_path: Path) -> Any:
    resolved = module_path.resolve()
    with _FIXTURE_CACHE_LOCK:
        cached = _FIXTURE_MODULE_CACHE.get(resolved)
        if cached is not None:
            return cached
        if not resolved.is_file():
            raise FileNotFoundError(f"Fixtures module not found: {resolved}")
        spec = importlib.util.spec_from_file_location(
            f"fleet_eval_fixtures_{abs(hash(str(resolved)))}", resolved
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load fixtures module from {resolved}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "get_case_fixtures"):
            raise ValueError(
                f"Fixtures module {resolved} must define get_case_fixtures(fixture_id)"
            )
        _FIXTURE_MODULE_CACHE[resolved] = module
        return module


def _decode_inline_files(raw: Any, *, path: Path, index: int) -> dict[str, bytes]:
    if not raw:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}:{index}: 'input_files' must be a mapping of path -> content")
    files: dict[str, bytes] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}:{index}: input_files keys must be nonempty strings")
        if isinstance(value, bytes):
            files[name] = value
        elif isinstance(value, str):
            files[name] = value.encode("utf-8")
        elif isinstance(value, Mapping) and value.get("encoding") == "base64":
            data = value.get("data")
            if not isinstance(data, str):
                raise ValueError(f"{path}:{index}: base64 input file {name!r} requires string data")
            files[name] = base64.b64decode(data, validate=True)
        else:
            raise ValueError(f"{path}:{index}: unsupported input_files value for {name!r}")
    return files


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_fixture_search_paths(
    dataset_path: Path,
    settings: Settings | None = None,
) -> tuple[Path | None, tuple[Path, ...]]:
    """Resolve sibling fixtures.py (preferred) and fallback eval_datasets/fixtures search roots."""
    sibling_mod = dataset_path.parent / "fixtures.py"
    fallback_roots: list[Path] = []

    def add_root(candidate: Path) -> None:
        resolved = candidate.resolve()
        if resolved not in fallback_roots and resolved.is_dir():
            fallback_roots.append(resolved)

    add_root(dataset_path.parent / "fixtures")

    eval_dirs: list[Path] = []
    if settings is not None:
        configured = settings.eval_datasets_dir
        if configured.is_absolute():
            eval_dirs.append(configured)
        else:
            eval_dirs.append(configured)
            eval_dirs.append(_backend_root() / configured)
    eval_dirs.append(_backend_root() / "eval_datasets")

    fallback_mod: Path | None = None
    for eval_dir in eval_dirs:
        add_root(eval_dir / "fixtures")
        if fallback_mod is None:
            for candidate_mod in (eval_dir / "fixtures.py", eval_dir / "fixtures" / "__init__.py"):
                if candidate_mod.is_file():
                    fallback_mod = candidate_mod
                    break

    if sibling_mod.is_file():
        fixtures_module_path: Path | None = sibling_mod
    elif (dataset_path.parent / "fixtures" / "__init__.py").is_file():
        fixtures_module_path = dataset_path.parent / "fixtures" / "__init__.py"
    else:
        fixtures_module_path = fallback_mod

    return fixtures_module_path, tuple(fallback_roots)


def _load_fixture_from_dir(
    fixtures_dir: Path,
    fixture_id: str,
) -> tuple[dict[str, bytes], dict[str, Any] | None] | None:
    if not fixture_id or "/" in fixture_id or "\\" in fixture_id or ".." in fixture_id:
        return None
    base = fixtures_dir.resolve()
    if not base.is_dir():
        return None

    # 1. Directory of static fixture files: eval_datasets/fixtures/<fixture_id>/...
    subdir = (base / fixture_id).resolve()
    if subdir.is_relative_to(base) and subdir.is_dir():
        files: dict[str, bytes] = {}
        web: dict[str, Any] | None = None
        for file_path in sorted(subdir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(subdir).as_posix()
            if rel == "web.json":
                parsed_web = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(parsed_web, Mapping):
                    web = dict(parsed_web)
                continue
            files[rel] = file_path.read_bytes()
        return files, web

    # 2. JSON bundle: eval_datasets/fixtures/<fixture_id>.json
    json_path = (base / f"{fixture_id}.json").resolve()
    if json_path.is_relative_to(base) and json_path.is_file():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"Fixture file {json_path} must contain a JSON object")
        files = _decode_inline_files(payload.get("files"), path=json_path, index=1)
        raw_web = payload.get("web")
        web = dict(raw_web) if isinstance(raw_web, Mapping) else None
        return files, web

    # 3. Module per fixture: eval_datasets/fixtures/<fixture_id>.py
    py_path = (base / f"{fixture_id}.py").resolve()
    if py_path.is_relative_to(base) and py_path.is_file():
        module = _load_fixtures_module(py_path)
        payload = module.get_case_fixtures(fixture_id)
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"get_case_fixtures({fixture_id!r}) must return a mapping with 'files'/'web'"
            )
        raw_files = payload.get("files") or {}
        if not isinstance(raw_files, Mapping):
            raise ValueError(f"Fixture {fixture_id!r} 'files' must be a mapping")
        files = {
            str(rel_path): (content if isinstance(content, bytes) else str(content).encode("utf-8"))
            for rel_path, content in raw_files.items()
        }
        raw_web = payload.get("web")
        web = dict(raw_web) if isinstance(raw_web, Mapping) else None
        return files, web

    return None


def _resolve_case_fixtures(spec: _CaseSpec) -> tuple[dict[str, bytes], dict[str, Any] | None]:
    files: dict[str, bytes] = {}
    web: dict[str, Any] | None = None
    if spec.fixture_id:
        resolved = False
        module_error: Exception | None = None
        if spec.fixtures_module_path is not None:
            try:
                module = _load_fixtures_module(spec.fixtures_module_path)
                payload = module.get_case_fixtures(spec.fixture_id)
                if not isinstance(payload, Mapping):
                    raise ValueError(
                        f"get_case_fixtures({spec.fixture_id!r}) must return a mapping "
                        "with 'files'/'web'"
                    )
                raw_files = payload.get("files") or {}
                if not isinstance(raw_files, Mapping):
                    raise ValueError(f"Fixture {spec.fixture_id!r} 'files' must be a mapping")
                for rel_path, content in raw_files.items():
                    files[str(rel_path)] = (
                        content if isinstance(content, bytes) else str(content).encode("utf-8")
                    )
                raw_web = payload.get("web")
                if raw_web is not None:
                    if not isinstance(raw_web, Mapping):
                        raise ValueError(
                            f"Fixture {spec.fixture_id!r} 'web' must be a mapping or None"
                        )
                    web = dict(raw_web)
                resolved = True
            except KeyError as error:
                module_error = error

        if not resolved:
            for fallback_dir in spec.fixtures_fallback_dirs:
                loaded = _load_fixture_from_dir(fallback_dir, spec.fixture_id)
                if loaded is not None:
                    dir_files, dir_web = loaded
                    files.update(dir_files)
                    if dir_web is not None:
                        web = dir_web
                    resolved = True
                    break

        if not resolved:
            if module_error is not None:
                raise module_error
            raise FileNotFoundError(
                f"Case {spec.case_id!r} references fixture_id={spec.fixture_id!r}, "
                "but no fixtures.py module or eval_datasets/fixtures/ entry was found"
            )
    if spec.input_files:
        files.update(spec.input_files)
    if spec.web_fixtures is not None:
        web = dict(spec.web_fixtures)
    return files, web


def _normalize_prior_turns(raw: Any, *, path: Path, index: int) -> list[dict[str, str]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}:{index}: 'prior_turns' must be a list of turn objects")
    turns: list[dict[str, str]] = []
    for turn_idx, turn in enumerate(raw, 1):
        if not isinstance(turn, Mapping):
            raise ValueError(f"{path}:{index}: prior_turns[{turn_idx}] must be an object")
        normalized = {
            "user": str(turn.get("user") or ""),
            "assistant": str(turn.get("assistant") or ""),
        }
        if "runtime" in turn and turn["runtime"] is not None:
            normalized["runtime"] = str(turn["runtime"])
        turns.append(normalized)
    return turns


def load_rlm_dataset(
    path: Path,
    *,
    settings: Settings | None = None,
    registry: skills.SkillRegistry | None = None,
) -> list[dspy.Example]:
    """Load labeled RLM evaluation examples from a JSONL file."""
    fixtures_module_path, fixtures_fallback_dirs = _resolve_fixture_search_paths(
        path, settings=settings
    )
    resolved_registry = registry

    def get_registry() -> skills.SkillRegistry:
        nonlocal resolved_registry
        if resolved_registry is None:
            skills_dir = settings.skills_dir if settings is not None else Path("skills")
            resolved_registry = skills.SkillRegistry.load(
                skills_dir, fallback_to_bundled=True
            ).with_installed(skills_dir)
        return resolved_registry

    examples: list[dspy.Example] = []
    for index, row in enumerate(_read_jsonl(path), 1):
        task = row.get("task")
        verifier = row.get("verifier")
        answer = row.get("answer", "") if verifier is not None else row.get("answer")
        if not isinstance(task, str) or not task.strip() or not isinstance(answer, str):
            raise ValueError(f"{path}:{index}: RLM rows require string 'task' and 'answer'")
        if verifier is not None:
            if not isinstance(verifier, dict) or verifier.get("type") not in _VALID_VERIFIER_TYPES:
                raise ValueError(
                    f"{path}:{index}: invalid verifier {verifier!r} "
                    f"(expected type in {sorted(_VALID_VERIFIER_TYPES)})"
                )

        raw_id = row.get("id")
        case_id = (
            str(raw_id).strip() if isinstance(raw_id, str) and raw_id.strip() else f"row-{index}"
        )
        lineage = _optional_lineage(row, path=path, index=index)
        split = _optional_split(row, path=path, index=index)
        prior_turns = _normalize_prior_turns(row.get("prior_turns"), path=path, index=index)

        raw_skill_names = row.get("skill_names") or []
        if not isinstance(raw_skill_names, list):
            raise ValueError(f"{path}:{index}: 'skill_names' must be a list of strings")
        skill_names = [
            str(name).removeprefix("@").strip() for name in raw_skill_names if str(name).strip()
        ]

        raw_active_skills = row.get("active_skills")
        if isinstance(raw_active_skills, str) and raw_active_skills.strip():
            active_skills: str = raw_active_skills
        elif skill_names:
            reg = get_registry()
            by_name = {skill.name: skill for skill in reg.skills}
            unknown = [name for name in skill_names if name not in by_name]
            if unknown:
                raise ValueError(f"{path}:{index}: unknown skill_names {unknown!r}")
            active_skills = agent._render_active_skills([by_name[name] for name in skill_names])
        else:
            active_skills = str(raw_active_skills or "")

        raw_fixture_id = row.get("fixture_id")
        fixture_id = (
            raw_fixture_id.strip()
            if isinstance(raw_fixture_id, str) and raw_fixture_id.strip()
            else None
        )
        input_files = _decode_inline_files(row.get("input_files"), path=path, index=index)
        raw_web_fixtures = row.get("web_fixtures")
        if raw_web_fixtures is not None and not isinstance(raw_web_fixtures, Mapping):
            raise ValueError(f"{path}:{index}: 'web_fixtures' must be a mapping or null")
        web_fixtures = dict(raw_web_fixtures) if raw_web_fixtures is not None else None

        max_expected_actions = row.get("max_expected_actions")
        if max_expected_actions is not None and (
            not isinstance(max_expected_actions, int)
            or isinstance(max_expected_actions, bool)
            or max_expected_actions < 1
        ):
            raise ValueError(f"{path}:{index}: 'max_expected_actions' must be a positive integer")

        enable_web_tools = (
            bool(row["enable_web_tools"])
            if "enable_web_tools" in row and row["enable_web_tools"] is not None
            else ("web-research" in skill_names or web_fixtures is not None)
        )
        enable_spawn_agent = (
            bool(row["enable_spawn_agent"])
            if "enable_spawn_agent" in row and row["enable_spawn_agent"] is not None
            else True
        )
        blocking = bool(row.get("blocking", False))

        examples.append(
            dspy.Example(
                task=task.strip(),
                files_manifest=list(row.get("files_manifest") or []),
                memories=list(row.get("memories") or []),
                active_skills=active_skills,
                prior_turns=prior_turns,
                answer=answer,
                artifact_paths=list(row.get("artifact_paths") or []),
                case_id=case_id,
                lineage=lineage,
                split=split,
                skill_names=skill_names,
                fixture_id=fixture_id,
                fixtures_module_path=str(fixtures_module_path) if fixtures_module_path else None,
                fixtures_fallback_dirs=[str(p) for p in fixtures_fallback_dirs],
                input_files=input_files,
                web_fixtures=web_fixtures,
                verifier=verifier,
                blocking=blocking,
                max_expected_actions=max_expected_actions,
                enable_web_tools=enable_web_tools,
                enable_spawn_agent=enable_spawn_agent,
            ).with_inputs("task", "files_manifest", "memories", "active_skills", "prior_turns")
        )
    return examples


def _extract_files_manifest(ui_parts: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for part in ui_parts or ():
        if not isinstance(part, dict) or part.get("type") != "data-file-tree":
            continue
        data = part.get("data")
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            manifest = [dict(item) for item in data["files"] if isinstance(item, dict)]
    return manifest


async def load_rlm_examples_from_db(
    database_url: str,
    settings: Settings,
    *,
    limit: int = 200,
) -> list[dspy.Example]:
    """Load completed root runs from an isolated Neon branch database."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    branch_settings = settings.model_copy(
        update={"database_url": database_url, "database_direct_url": database_url}
    )
    engine, sessionmaker = db.create_db(branch_settings)
    try:
        async with sessionmaker() as session:
            runs = (
                await session.scalars(
                    select(db.Run)
                    .where(
                        db.Run.parent_run_id.is_(None),
                        db.Run.status == "completed",
                        db.Run.answer.is_not(None),
                    )
                    .order_by(db.Run.id.desc())
                    .limit(limit)
                )
            ).all()
    finally:
        await engine.dispose()

    examples: list[dspy.Example] = []
    for index, run in enumerate(runs, 1):
        if not run.prompt or run.answer is None:
            continue
        examples.append(
            dspy.Example(
                task=run.prompt,
                files_manifest=_extract_files_manifest(run.ui_parts),
                memories=[],
                active_skills="",
                prior_turns=[],
                answer=run.answer,
                artifact_paths=[],
                case_id=f"db-run-{getattr(run, 'id', index)}",
                lineage=None,
                split=None,
                verifier=None,
                blocking=False,
                max_expected_actions=None,
                enable_web_tools=False,
                enable_spawn_agent=True,
            ).with_inputs("task", "files_manifest", "memories", "active_skills", "prior_turns")
        )
    return examples


def load_skills_dataset(
    path: Path,
    registry: skills.SkillRegistry,
    *,
    settings: Settings | None = None,
) -> list[dspy.Example]:
    """Load labeled skill-selector rows from JSONL, preserving runtime multi-turn formatting."""
    by_name = {skill.name: skill for skill in registry.skills}
    available_names = (
        {skill.name for skill in registry.available_skills(settings)}
        if settings is not None
        else set(by_name)
    )

    def validate_skill_name(name: str, *, index: int) -> None:
        if name not in by_name:
            raise ValueError(f"{path}:{index}: unknown skill {name!r}")
        if name not in available_names:
            reason = (
                by_name[name].unavailable_reason(settings)
                if settings is not None
                else "unavailable under current settings"
            )
            raise ValueError(
                f"{path}:{index}: skill {name!r} is unavailable under current settings: {reason}"
            )

    examples: list[dspy.Example] = []
    for index, row in enumerate(_read_jsonl(path), 1):
        task = row.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"{path}:{index}: Skill rows require a nonempty 'task'")
        prior_turns = _normalize_prior_turns(row.get("prior_turns"), path=path, index=index)
        selector_task, _ = skills.format_selector_task(task.strip(), prior_turns)

        raw_skill = row.get("expected_skill", row.get("primary_skill"))
        expected_skill = (
            raw_skill.removeprefix("@").strip()
            if isinstance(raw_skill, str) and raw_skill.strip()
            else None
        )
        if expected_skill is not None:
            validate_skill_name(expected_skill, index=index)

        raw_secondary = row.get("secondary_skills") or []
        if not isinstance(raw_secondary, list):
            raise ValueError(f"{path}:{index}: 'secondary_skills' must be a list of strings")
        secondary_skills: list[str] = []
        for item in raw_secondary:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"{path}:{index}: secondary_skills entries must be nonempty strings"
                )
            cleaned = item.removeprefix("@").strip()
            validate_skill_name(cleaned, index=index)
            if cleaned != expected_skill and cleaned not in secondary_skills:
                secondary_skills.append(cleaned)

        needs_skill = (
            bool(row["needs_skill"]) if "needs_skill" in row else expected_skill is not None
        )
        if not needs_skill:
            expected_skill = None
            secondary_skills = []
        elif expected_skill is None:
            raise ValueError(f"{path}:{index}: rows with needs_skill=True require 'expected_skill'")

        lineage = _optional_lineage(row, path=path, index=index)
        split = _optional_split(row, path=path, index=index)

        examples.append(
            dspy.Example(
                task=selector_task,
                raw_task=task.strip(),
                prior_turns=prior_turns,
                is_multi_turn=bool(prior_turns),
                needs_skill=needs_skill,
                expected_skill=expected_skill,
                secondary_skills=secondary_skills,
                lineage=lineage,
                split=split,
            ).with_inputs("task")
        )
    return examples


def _normalize_existing_memories(raw: Any, *, path: Path, index: int) -> list[dict[str, str]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}:{index}: 'existing_memories' must be a list")
    normalized: list[dict[str, str]] = []
    for mem_idx, memory in enumerate(raw[:3], 1):
        if not isinstance(memory, Mapping):
            raise ValueError(f"{path}:{index}: existing_memories[{mem_idx}] must be an object")
        mem_id = str(memory.get("id") or f"mem-{mem_idx}")
        content = agent.memory_fact(str(memory.get("content") or ""))[:1000]
        scope = str(memory.get("scope") or "session").strip()
        if scope not in {"global", "session"}:
            raise ValueError(f"{path}:{index}: invalid existing memory scope {scope!r}")
        if "status" in memory and memory["status"] is not None:
            status = str(memory["status"]).strip()
            if status not in {"approved", "pending"}:
                raise ValueError(f"{path}:{index}: invalid existing memory status {status!r}")
        else:
            status = "approved" if memory.get("approved", True) else "pending"
        normalized.append({"id": mem_id, "content": content, "scope": scope, "status": status})
    return normalized


def load_memory_dataset(path: Path) -> list[dspy.Example]:
    """Load labeled (turn_exchange, should_save, action, scope) rows from JSONL."""
    examples: list[dspy.Example] = []
    for index, row in enumerate(_read_jsonl(path), 1):
        exchange = row.get("turn_exchange")
        if "task" in row or "answer" in row:
            task = str(row.get("task") or "").strip()
            answer = str(row.get("answer") or "").strip()
        elif isinstance(exchange, dict):
            task = str(exchange.get("task") or "").strip()
            answer = str(exchange.get("answer") or "").strip()
        elif isinstance(exchange, str):
            task = exchange.strip()
            answer = ""
        else:
            task, answer = "", ""
        if not task:
            raise ValueError(f"{path}:{index}: Memory rows require 'task' or 'turn_exchange'")

        if "worth_saving" in row:
            worth_saving = bool(row["worth_saving"])
        elif "should_save" in row:
            worth_saving = bool(row["should_save"])
        else:
            raise ValueError(f"{path}:{index}: Memory rows require 'worth_saving' or 'should_save'")

        action = str(row.get("action") or ("insert" if worth_saving else "skip")).strip()
        if action not in {"insert", "update", "skip"}:
            raise ValueError(f"{path}:{index}: invalid action {action!r}")
        scope = str(row.get("scope") or "session").strip()
        if scope not in {"global", "session"}:
            raise ValueError(f"{path}:{index}: invalid scope {scope!r}")

        existing_memories = _normalize_existing_memories(
            row.get("existing_memories"), path=path, index=index
        )
        lineage = _optional_lineage(row, path=path, index=index)
        split = _optional_split(row, path=path, index=index)

        examples.append(
            dspy.Example(
                task=task[:2000],
                answer=answer[:4000],
                existing_memories=existing_memories,
                worth_saving=worth_saving,
                action=action,
                scope=scope,
                lineage=lineage,
                split=split,
            ).with_inputs("task", "answer", "existing_memories")
        )
    return examples


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _json_equal(actual: Any, expected: Any, *, abs_tol: float = 0.0, path: str = "$") -> list[str]:
    if isinstance(expected, bool):
        if isinstance(actual, bool) and actual == expected:
            return []
        return [f"{path}: expected bool {expected!r}, got {actual!r}"]
    if isinstance(expected, (int, float)):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return [f"{path}: expected number {expected!r}, got {actual!r}"]
        if not (math.isfinite(float(actual)) and math.isfinite(float(expected))):
            return [f"{path}: non-finite number (expected {expected!r}, got {actual!r})"]
        if math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=abs_tol):
            return []
        return [f"{path}: expected {expected!r} (±{abs_tol}), got {actual!r}"]
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        diffs: list[str] = []
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing:
            diffs.append(f"{path}: missing keys {missing}")
        if extra:
            diffs.append(f"{path}: unexpected keys {extra}")
        for key in sorted(set(expected) & set(actual)):
            diffs.extend(
                _json_equal(actual[key], expected[key], abs_tol=abs_tol, path=f"{path}.{key}")
            )
        return diffs
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected list, got {type(actual).__name__}"]
        if len(actual) != len(expected):
            return [f"{path}: expected list of length {len(expected)}, got {len(actual)}"]
        diffs = []
        for idx, (a_item, e_item) in enumerate(zip(actual, expected, strict=True)):
            diffs.extend(_json_equal(a_item, e_item, abs_tol=abs_tol, path=f"{path}[{idx}]"))
        return diffs
    if actual != expected:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return []


def _count_sentences(text: str) -> int:
    cleaned = text.strip()
    if not cleaned:
        return 0
    segments = [seg.strip() for seg in re.split(r"(?<=[.!?])(?:\s+|$)", cleaned) if seg.strip()]
    return len(segments)


def _normalize_artifact_key(path: str) -> str:
    cleaned = path.strip().removeprefix("/workspace/").lstrip("/")
    return cleaned


def _verify_png_nonblank(payload: bytes, *, min_size: int = 100) -> tuple[bool, str]:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG":
                return False, f"expected PNG image, got {image.format!r}"
            if min(image.size) < min_size:
                return False, f"PNG dimensions {image.size} smaller than {min_size}px"
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            extrema = image.convert("RGB").getextrema()
            nonblank = any(
                isinstance(channel, tuple) and channel[0] != channel[1] for channel in extrema
            )
            if not nonblank:
                return False, "PNG image is blank (uniform pixel values)"
    except Exception as error:
        return False, f"invalid PNG payload ({type(error).__name__}: {error})"
    return True, "valid non-blank PNG"


def _verify_svg_labels(payload: bytes, required_labels: Sequence[str]) -> tuple[bool, str]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        return False, f"invalid SVG XML ({error})"
    labels = {"".join(node.itertext()).strip() for node in root.findall(".//{*}text")}
    missing = [
        label
        for label in required_labels
        if label not in labels and not any(label in text for text in labels)
    ]
    if missing:
        return (
            False,
            f"SVG missing required <text> labels {missing} (found {sorted(labels)[:12]})",
        )
    return True, "all required SVG text labels present"


def _verify_skill_md(payload: bytes, spec: Mapping[str, Any]) -> tuple[bool, str]:
    max_bytes = int(spec.get("max_bytes", skills.MAX_SKILL_BYTES))
    if len(payload) > max_bytes:
        return False, f"SKILL.md size {len(payload)} exceeds max_bytes={max_bytes}"
    try:
        parsed = skills._parse_skill(payload, Path("SKILL.md"), strict=True)
    except Exception as error:
        return False, f"invalid SKILL.md ({error})"
    expected_name = spec.get("name")
    if isinstance(expected_name, str) and parsed.name != expected_name:
        return False, f"SKILL.md name {parsed.name!r} != expected {expected_name!r}"
    decoded = payload.decode("utf-8", errors="replace")
    required_tokens = [str(token) for token in spec.get("required_tokens") or ()]
    missing = [token for token in required_tokens if token not in decoded]
    if missing:
        return False, f"SKILL.md missing required tokens {missing}"
    forbidden_tokens = [str(token) for token in spec.get("forbidden_tokens") or ()]
    present_forbidden = [token for token in forbidden_tokens if token in decoded]
    if present_forbidden:
        return False, f"SKILL.md contains forbidden tokens {present_forbidden}"
    return True, "valid SKILL.md"


class _RubricJudge(dspy.Signature):
    """Evaluate a workspace agent's answer against explicit rubric criteria.

    Score strictly on groundedness, completeness, citation/evidence fidelity, and caveat handling.
    """

    task: str = dspy.InputField()
    reference_answer: str = dspy.InputField()
    criteria: list[str] = dspy.InputField()
    candidate_answer: str = dspy.InputField()
    score: float = dspy.OutputField(desc="Numeric quality score between 0.0 and 1.0.")
    critique: str = dspy.OutputField(desc="Concise diagnostic critique naming strengths and gaps.")


def _legacy_token_f1(expected_raw: str, actual_raw: str) -> tuple[float, bool, str]:
    expected = _normalize_text(expected_raw)
    actual = _normalize_text(actual_raw)
    if expected == actual and expected:
        return 1.0, True, "Exact answer match."
    if not expected or not actual:
        return (
            0.0,
            False,
            f"Expected {expected_raw!r}, got {actual_raw!r} (empty answer).",
        )
    expected_tokens = expected.split()
    actual_tokens = actual.split()
    expected_counts: dict[str, int] = {}
    for token in expected_tokens:
        expected_counts[token] = expected_counts.get(token, 0) + 1
    overlap = 0
    for token in actual_tokens:
        remaining = expected_counts.get(token, 0)
        if remaining > 0:
            overlap += 1
            expected_counts[token] = remaining - 1
    if overlap == 0:
        score = 0.0
    else:
        precision = overlap / len(actual_tokens)
        recall = overlap / len(expected_tokens)
        score = 2 * precision * recall / (precision + recall)
    return (
        score,
        score >= 1.0,
        f"Expected {expected_raw!r}, got {actual_raw!r} (token F1={score:.2f}).",
    )


def _evaluate_verifier(
    gold: dspy.Example,
    pred: Any,
    verifier: Mapping[str, Any] | None,
    *,
    judge_lm: dspy.BaseLM | None = None,
    attachment_paths: Sequence[str] = (),
) -> tuple[float, bool, str]:
    actual_raw = str(getattr(pred, "answer", "") or "").strip()
    expected_raw = str(getattr(gold, "answer", "") or "").strip()

    if not verifier:
        return _legacy_token_f1(expected_raw, actual_raw)

    vtype = str(verifier.get("type", "")).strip()
    if vtype == "exact":
        expected = str(
            verifier["expected"] if verifier.get("expected") is not None else expected_raw
        ).strip()
        if actual_raw == expected:
            return 1.0, True, "Exact match passed."
        return 0.0, False, f"Exact match failed: expected {expected!r}, got {actual_raw!r}."

    if vtype == "json":
        strict_json = bool(verifier.get("strict_json", False))
        abs_tol = float(verifier.get("abs_tol", 0.0))
        expected_obj = verifier.get("expected")
        if expected_obj is None:
            expected_obj = json.loads(expected_raw)
        candidate_text = actual_raw if strict_json else _strip_json_fences(actual_raw)
        try:
            actual_obj = json.loads(candidate_text)
        except json.JSONDecodeError as error:
            mode = "strict JSON" if strict_json else "JSON"
            return (
                0.0,
                False,
                f"Expected valid {mode} answer, failed to parse ({error.msg}).",
            )
        diffs = _json_equal(actual_obj, expected_obj, abs_tol=abs_tol)
        if not diffs:
            return 1.0, True, "JSON verification passed."
        summary = "; ".join(diffs[:6])
        return 0.0, False, f"JSON verification failed: {summary}."

    if vtype == "fact_checklist":
        required = list(verifier.get("required_patterns") or ())
        forbidden = list(verifier.get("forbidden_patterns") or ())
        max_sentences = verifier.get("max_sentences")
        max_words = verifier.get("max_words")
        failures: list[str] = []

        if not actual_raw and not (required and attachment_paths):
            return 0.0, False, "Fact checklist failed: empty answer."

        for pattern in forbidden:
            if re.search(str(pattern), actual_raw, re.IGNORECASE):
                failures.append(f"matched forbidden pattern {pattern!r}")
        if max_sentences is not None:
            sentence_count = _count_sentences(actual_raw)
            if sentence_count > int(max_sentences):
                failures.append(f"sentence count {sentence_count} > max_sentences {max_sentences}")
        if max_words is not None:
            word_count = len(actual_raw.split())
            if word_count > int(max_words):
                failures.append(f"word count {word_count} > max_words {max_words}")

        matched = 0
        missing_patterns: list[str] = []
        for item in required:
            alternatives = item if isinstance(item, list) else [item]
            if any(
                re.search(str(pattern), actual_raw, re.IGNORECASE)
                or any(re.fullmatch(str(pattern), path) for path in attachment_paths)
                for pattern in alternatives
            ):
                matched += 1
            else:
                missing_patterns.append(" | ".join(str(pattern) for pattern in alternatives))

        if failures:
            if missing_patterns:
                failures.append(f"missing required patterns {missing_patterns[:4]}")
            return 0.0, False, "Fact checklist failed: " + "; ".join(failures) + "."
        if not required:
            return 1.0, True, "Fact checklist passed."
        if matched == len(required):
            return 1.0, True, "Fact checklist passed."
        if bool(getattr(gold, "blocking", False)):
            return (
                0.0,
                False,
                f"Blocking fact checklist failed: missing {missing_patterns[:4]}.",
            )
        score = matched / len(required)
        return (
            score,
            False,
            f"Fact checklist partial ({matched}/{len(required)}): missing {missing_patterns[:4]}.",
        )

    if vtype == "artifact":
        answer_verifier = verifier.get("answer_verifier")
        answer_score = 1.0
        answer_passed = True
        feedback_parts: list[str] = []
        raw_artifacts = getattr(pred, "artifacts", None)
        artifact_map: dict[str, bytes] = {}
        if isinstance(raw_artifacts, Mapping):
            for key, value in raw_artifacts.items():
                artifact_map[_normalize_artifact_key(str(key))] = (
                    value if isinstance(value, bytes) else str(value).encode("utf-8")
                )

        specs = verifier.get("artifacts") or {}
        if not isinstance(specs, Mapping):
            raise ValueError("Artifact verifier requires an 'artifacts' mapping")
        if isinstance(answer_verifier, Mapping):
            # WorkspaceTask submits the answer and its attachments in separate fields.
            # Only a declared, collected artifact can supply a checklist path.
            reported = getattr(pred, "artifact_paths", None)
            declared = {_normalize_artifact_key(str(path)) for path in specs}
            attachments = (
                {_normalize_artifact_key(path) for path in reported if isinstance(path, str)}
                if isinstance(reported, (list, tuple))
                else set()
            )
            answer_score, answer_passed, ans_fb = _evaluate_verifier(
                gold,
                pred,
                answer_verifier,
                judge_lm=judge_lm,
                attachment_paths=sorted(attachments.intersection(declared, artifact_map)),
            )
            feedback_parts.append(ans_fb)
        artifact_failures: list[str] = []
        for raw_path, check_spec in specs.items():
            norm_path = _normalize_artifact_key(str(raw_path))
            payload = artifact_map.get(norm_path)
            if payload is None:
                artifact_failures.append(f"missing artifact {norm_path!r}")
                continue
            if not isinstance(check_spec, Mapping):
                continue
            if "svg_labels" in check_spec and check_spec["svg_labels"]:
                ok, msg = _verify_svg_labels(payload, list(check_spec["svg_labels"]))
                if not ok:
                    artifact_failures.append(f"{norm_path}: {msg}")
            if check_spec.get("png_nonblank"):
                ok, msg = _verify_png_nonblank(
                    payload, min_size=int(check_spec.get("min_size", 100))
                )
                if not ok:
                    artifact_failures.append(f"{norm_path}: {msg}")
            if "json_equals" in check_spec and check_spec["json_equals"] is not None:
                try:
                    parsed_json = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    artifact_failures.append(f"{norm_path}: invalid JSON ({error})")
                else:
                    diffs = _json_equal(
                        parsed_json,
                        check_spec["json_equals"],
                        abs_tol=float(check_spec.get("abs_tol", 0.0)),
                    )
                    if diffs:
                        artifact_failures.append(f"{norm_path}: {'; '.join(diffs[:4])}")
            if "text_contains" in check_spec and check_spec["text_contains"]:
                decoded = payload.decode("utf-8", errors="replace")
                for needle in check_spec["text_contains"]:
                    if str(needle) not in decoded:
                        artifact_failures.append(f"{norm_path}: missing text {needle!r}")
            if "text_excludes" in check_spec and check_spec["text_excludes"]:
                decoded = payload.decode("utf-8", errors="replace")
                for needle in check_spec["text_excludes"]:
                    if str(needle) in decoded:
                        artifact_failures.append(f"{norm_path}: contains forbidden text {needle!r}")
            if "skill_md" in check_spec and isinstance(check_spec["skill_md"], Mapping):
                ok, msg = _verify_skill_md(payload, check_spec["skill_md"])
                if not ok:
                    artifact_failures.append(f"{norm_path}: {msg}")

        if artifact_failures:
            feedback_parts.append("Artifact checks failed: " + "; ".join(artifact_failures) + ".")
            return 0.0, False, " ".join(feedback_parts)
        if answer_score <= 0.0:
            return 0.0, False, " ".join(feedback_parts)
        feedback_parts.append("All artifact checks passed.")
        return answer_score, answer_passed, " ".join(feedback_parts)

    if vtype == "rubric":
        if not actual_raw:
            return 0.0, False, "Rubric check failed: empty answer."
        required_facts = [str(p) for p in verifier.get("required_facts") or ()]
        required_urls = [str(u) for u in verifier.get("required_urls") or ()]
        forbidden_claims = [str(p) for p in verifier.get("forbidden_claims") or ()]
        criteria = [str(c) for c in verifier.get("criteria") or ()]

        actual_folded = actual_raw.casefold()
        for claim in forbidden_claims:
            if claim.casefold() in actual_folded:
                return 0.0, False, f"Rubric check failed: matched forbidden claim {claim!r}."

        missing_facts = [fact for fact in required_facts if fact.casefold() not in actual_folded]
        missing_urls = [u for u in required_urls if u not in actual_raw]

        resolved_judge = judge_lm if judge_lm is not None else getattr(dspy.settings, "lm", None)
        if resolved_judge is None:
            raise ValueError(
                "Rubric verifier requires a configured judge_lm or active dspy.settings.lm"
            )

        judge = dspy.Predict(_RubricJudge)
        with dspy.context(lm=resolved_judge):
            judged = judge(
                task=str(getattr(gold, "task", "")),
                reference_answer=expected_raw,
                criteria=criteria
                or ["Groundedness, completeness, citation fidelity, and caveats."],
                candidate_answer=actual_raw,
                lm=resolved_judge,
                config={"cache": False},
            )
        raw_score = getattr(judged, "score", 0.0)
        try:
            judge_score = float(raw_score)
        except (TypeError, ValueError):
            judge_score = 0.0
        if not math.isfinite(judge_score):
            judge_score = 0.0
        judge_score = max(0.0, min(1.0, judge_score))
        critique = str(getattr(judged, "critique", "") or "").strip()

        total_guards = len(required_facts) + len(required_urls)
        if total_guards > 0:
            matched_guards = total_guards - len(missing_facts) - len(missing_urls)
            guard_ratio = matched_guards / total_guards
            combined = round(judge_score * guard_ratio, 6)
        else:
            combined = round(judge_score, 6)

        notes: list[str] = [f"Rubric score={combined:.2f} (judge={judge_score:.2f})."]
        if critique:
            notes.append(critique)
        if missing_facts:
            notes.append(f"Missing required facts: {missing_facts[:4]}.")
        if missing_urls:
            notes.append(f"Missing required URLs: {missing_urls[:4]}.")
        passed = combined >= 0.7 and not missing_facts and not missing_urls
        return combined, passed, " ".join(notes)

    raise ValueError(f"Unsupported verifier type: {vtype!r}")


def _telemetry_dict(pred: Any) -> dict[str, Any]:
    raw = getattr(pred, "telemetry", None)
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if hasattr(raw, "__dataclass_fields__"):
        return asdict(raw)
    return {
        key: getattr(raw, key)
        for key in (
            "root_actions",
            "child_rlms",
            "sub_lm_queries",
            "python_errors",
            "web_calls",
            "repeated_web_opens",
            "web_exhausted",
            "completion",
            "error_samples",
        )
        if hasattr(raw, key)
    }


def _evaluate_rlm_case(
    gold: dspy.Example,
    pred: Any,
    *,
    judge_lm: dspy.BaseLM | None = None,
) -> _RlmCaseOutcome:
    verifier = getattr(gold, "verifier", None)
    blocking = bool(getattr(gold, "blocking", False))
    correctness, passed, correctness_feedback = _evaluate_verifier(
        gold, pred, verifier, judge_lm=judge_lm
    )
    telemetry = _telemetry_dict(pred)

    if correctness <= 0.0:
        return _RlmCaseOutcome(
            score=0.0,
            correctness=0.0,
            passed=False,
            feedback=correctness_feedback,
            blocking=blocking,
            telemetry=telemetry,
        )

    score = correctness
    notes: list[str] = [correctness_feedback]

    completion = telemetry.get("completion")
    if completion == "iteration_limit":
        score *= 0.75
        notes.append(
            "Hit iteration limit instead of calling SUBMIT cleanly; budget actions earlier."
        )

    python_errors = int(telemetry.get("python_errors") or 0)
    if python_errors > 0:
        py_penalty = min(0.15, 0.05 * python_errors)
        score -= py_penalty
        samples = telemetry.get("error_samples") or []
        sample_hint = f" (e.g. {samples[0]})" if samples else ""
        notes.append(
            f"Encountered {python_errors} avoidable Python error(s) "
            f"(-{py_penalty:.2f}){sample_hint}."
        )

    repeated_web_opens = int(telemetry.get("repeated_web_opens") or 0)
    if repeated_web_opens > 0:
        web_penalty = min(0.10, 0.05 * repeated_web_opens)
        score -= web_penalty
        notes.append(
            f"Reopened {repeated_web_opens} previously opened URL(s) (-{web_penalty:.2f}); "
            "reuse extracted content."
        )

    if telemetry.get("web_exhausted") and completion == "iteration_limit":
        notes.append("Web allowance ran out without a clean SUBMIT synthesis.")

    max_expected_actions = getattr(gold, "max_expected_actions", None)
    root_actions = int(telemetry.get("root_actions") or 0)
    if isinstance(max_expected_actions, int) and root_actions > max_expected_actions:
        score -= 0.05
        notes.append(f"Used {root_actions} root actions (target <= {max_expected_actions}, -0.05).")

    final_score = max(0.0, min(1.0, round(score, 6)))
    return _RlmCaseOutcome(
        score=final_score,
        correctness=round(correctness, 6),
        passed=passed,
        feedback=" ".join(notes),
        blocking=blocking,
        telemetry=telemetry,
    )


def rlm_metric(
    gold: dspy.Example,
    pred: Any,
    trace: Any = None,
    pred_name: str | None = None,
    pred_trace: Any = None,
    program_trace: Any = None,
    *,
    judge_lm: dspy.BaseLM | None = None,
) -> ScoreWithFeedback:
    """Verifier-first hybrid GEPA metric combining correctness with execution discipline."""
    outcome = _evaluate_rlm_case(gold, pred, judge_lm=judge_lm)
    return ScoreWithFeedback(score=outcome.score, feedback=outcome.feedback)


def _make_rlm_metric(judge_lm: dspy.BaseLM | None = None) -> Callable[..., ScoreWithFeedback]:
    def metric(
        gold: dspy.Example,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
        program_trace: Any = None,
    ) -> ScoreWithFeedback:
        return rlm_metric(
            gold,
            pred,
            trace,
            pred_name,
            pred_trace,
            program_trace,
            judge_lm=judge_lm,
        )

    return metric


def _decision_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _decision_probability(value: Any) -> float | None:
    prob = getattr(value, "probability", None)
    if isinstance(prob, (int, float)) and not isinstance(prob, bool):
        return float(prob)
    return None


def skill_metric(gold: dspy.Example, pred: Any, trace: Any = None) -> float:
    """Precision-tilted score in [0.0, 1.0] for SkillSelector."""
    pred_needs = bool(_decision_value(getattr(pred, "needs_skill", False)))
    gold_needs = bool(getattr(gold, "needs_skill", True))
    if not gold_needs:
        return 1.0 if not pred_needs else 0.0
    if not pred_needs:
        return 0.25
    raw_primary = getattr(pred, "primary_skill", _MISSING)
    if raw_primary is _MISSING:
        return 1.0
    pred_skill = _decision_value(raw_primary)
    if isinstance(pred_skill, str):
        pred_skill = pred_skill.removeprefix("@").strip()
    return 1.0 if pred_skill == getattr(gold, "expected_skill", None) else 0.0


def memory_metric(gold: dspy.Example, pred: Any, trace: Any = None) -> float:
    """Precision-tilted score in [0.0, 1.0] for MemoryGate (worth_saving, action, scope)."""
    pred_worth = bool(_decision_value(getattr(pred, "worth_saving", False)))
    pred_action = _decision_value(getattr(pred, "action", None))
    pred_scope = _decision_value(getattr(pred, "scope", None))
    gold_worth = bool(getattr(gold, "worth_saving", False))

    if not gold_worth:
        return 1.0 if (not pred_worth or pred_action == "skip") else 0.0
    if not pred_worth or pred_action == "skip":
        return 0.0
    score = 0.4
    if pred_action == getattr(gold, "action", None):
        score += 0.3
    if pred_scope == getattr(gold, "scope", None):
        score += 0.3
    return round(score, 6)


def _clear_predictor_lms(module: Any) -> None:
    if hasattr(module, "lm"):
        module.lm = None
    if hasattr(module, "predictors"):
        for predictor in module.predictors():
            if hasattr(predictor, "lm"):
                predictor.lm = None


def _save_program(module: Any, output_path: Path) -> None:
    target = module.rlm if isinstance(module, _OfflineWorkspaceProgram) else module
    to_save = target.deepcopy() if hasattr(target, "deepcopy") else target
    if hasattr(to_save, "generate_action"):
        action_sig = getattr(to_save.generate_action, "signature", None)
        if action_sig is not None:
            cleaned = agent.strip_dynamic_tool_docs(action_sig.instructions or "")
            to_save.generate_action.signature = action_sig.with_instructions(cleaned)
    _clear_predictor_lms(to_save)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    to_save.save(output_path)


def _eval_score(result: Any) -> float:
    return float(result.score if hasattr(result, "score") else result)


def _pin_reminder(env_var: str, output_path: Path, decision_model: str) -> str:
    return (
        f"IMPORTANT: Before deploying {output_path} via {env_var}, pin the exact "
        f"TypeSafe Jev model snapshot in FLEET_DECISION_MODEL (currently {decision_model!r}; "
        "do not keep floating 'typesafe:jev-latest') so decision probabilities match calibrated "
        "thresholds."
    )


def _public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None:
            return False
        try:
            return ip_address(host).is_global
        except ValueError:
            return "." in host and not host.endswith((".localhost", ".local", ".internal"))
    except ValueError:
        return False


class _OfflineWebResearch:
    """Deterministic recorded web_search/web_open matching skills/web-research host_tools.py."""

    def __init__(self, fixture: Mapping[str, Any], telemetry: _RolloutTelemetry) -> None:
        self._limit = max(0, int(fixture.get("limit", 8)))
        self._remaining = self._limit
        self._searches: dict[str, list[dict[str, Any]]] = {
            str(k): list(v)
            for k, v in (fixture.get("searches") or {}).items()
            if isinstance(v, list)
        }
        self._pages: dict[str, dict[str, Any]] = {
            str(k): dict(v)
            for k, v in (fixture.get("pages") or {}).items()
            if isinstance(v, Mapping)
        }
        self._telemetry = telemetry
        self._opened_targets: set[str] = set()
        self._lock = threading.Lock()

    def _budget(self, *, requested: bool) -> dict[str, Any]:
        return {
            "used": self._limit - self._remaining,
            "limit": self._limit,
            "requested": requested,
        }

    def _invalid(self, message: str) -> dict[str, Any]:
        with self._lock:
            return {
                "error": message,
                "error_code": "invalid_input",
                "budget": self._budget(requested=False),
            }

    def _consume(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        with self._lock:
            if self._remaining == 0:
                self._telemetry.web_exhausted = True
                limit_label = "eight-call" if self._limit == 8 else f"{self._limit}-call"
                return (
                    {
                        "error": f"Web research reached its {limit_label} limit for this turn.",
                        "error_code": "allowance_exhausted",
                        "budget": self._budget(requested=False),
                    },
                    self._budget(requested=False),
                )
            self._remaining -= 1
            self._telemetry.web_calls += 1
            return None, self._budget(requested=True)

    def _match_search(self, query: str) -> list[dict[str, Any]]:
        cleaned = query.strip()
        if cleaned in self._searches:
            return self._searches[cleaned]
        folded = cleaned.casefold()
        for key, items in self._searches.items():
            if key != "*" and key.casefold() == folded:
                return items
        query_tokens = set(re.findall(r"\w+", folded))
        best_score = (0, 0)
        best_items: list[dict[str, Any]] | None = None
        for key, items in self._searches.items():
            if key == "*":
                continue
            key_folded = key.casefold()
            substr = int(key_folded in folded or folded in key_folded)
            key_tokens = set(re.findall(r"\w+", key_folded))
            overlap = len(query_tokens & key_tokens)
            score = (substr, overlap)
            if score > best_score and (substr or overlap > 0):
                best_score = score
                best_items = items
        if best_items is not None:
            return best_items
        return self._searches.get("*", [])

    def _match_page(self, url: str) -> dict[str, Any] | None:
        if url in self._pages:
            return self._pages[url]
        trimmed = url.rstrip("/")
        for candidate in (trimmed, f"{trimmed}/", agent._web_target("web_open", url)):
            if candidate and candidate in self._pages:
                return self._pages[candidate]
        for key, value in self._pages.items():
            if key != "*" and key.rstrip("/") == trimmed:
                return value
        return self._pages.get("*")

    def web_search(self, query: str) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            return self._invalid("Provide a search query.")
        exhausted, budget = self._consume()
        if exhausted is not None:
            return exhausted
        raw_results = self._match_search(query)
        results = [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("snippet", item.get("content", "")))[:800],
            }
            for item in raw_results[:5]
            if isinstance(item, Mapping)
        ]
        return {"results": results, "budget": budget}

    def web_open(self, url: str) -> dict[str, Any]:
        if not isinstance(url, str) or not _public_url(url):
            return self._invalid("Provide a public HTTP or HTTPS page URL.")
        exhausted, budget = self._consume()
        if exhausted is not None:
            return exhausted
        target_key = agent._web_target("web_open", url) or url.strip()
        page = self._match_page(url)
        if page is None:
            return {"url": url, "error": "Tavily could not extract this page.", "budget": budget}
        if "error" in page:
            out: dict[str, Any] = {"url": url, "error": str(page["error"]), "budget": budget}
            if "error_code" in page:
                out["error_code"] = page["error_code"]
            return out
        content = str(page.get("markdown", page.get("raw_content", "")))
        if not content.strip():
            return {"url": url, "error": "The page returned no readable text.", "budget": budget}
        with self._lock:
            if target_key in self._opened_targets:
                self._telemetry.repeated_web_opens += 1
            else:
                self._opened_targets.add(target_key)
        truncated = bool(page.get("truncated", len(content) > 16000))
        return {
            "url": str(page.get("url") or url),
            "markdown": content[:16000],
            "truncated": truncated,
            "budget": budget,
        }


class _SharedCallBudget:
    """Thread-safe shared sub-LM / child-agent call budget across a rollout tree."""

    def __init__(self, remaining: int) -> None:
        self._remaining = remaining
        self._lock = threading.Lock()

    def reserve(self, count: int) -> None:
        with self._lock:
            if count > self._remaining:
                raise CodeExecutionError("Shared sub-LM call limit reached")
            self._remaining -= count


@dataclass
class _RolloutState:
    rlm: dspy.RLM
    settings: Settings
    sub_lm: dspy.BaseLM
    telemetry: _RolloutTelemetry
    budget: _SharedCallBudget
    depth: int
    max_depth: int
    files_manifest: list[dict[str, Any]]
    memories: list[str]
    active_skills: str
    slot: _SandboxSlot | None
    web_session: _OfflineWebResearch | None
    enable_web_tools: bool
    enable_spawn_agent: bool

    def reserve(self, count: int) -> None:
        self.budget.reserve(count)


_ROLLOUT_STATE: ContextVar[_RolloutState | None] = ContextVar(
    "fleet_optimize_rollout_state", default=None
)


def _build_scoped_factory(state: _RolloutState) -> Callable[..., Any] | None:
    if state.slot is None:
        return None
    slot = state.slot

    async def observe_execution(_code: str, execute: Callable[[], Any]) -> Any:
        try:
            return await execute()
        except CodeExecutionError as error:
            if state.depth == 0:
                state.telemetry.python_errors += 1
                if len(state.telemetry.error_samples) < 5:
                    state.telemetry.error_samples.append(str(error)[:160])
            raise

    async def observe_tool(name: str, args: list, kwargs: dict, invoke: Callable[[], Any]) -> Any:
        token = _ROLLOUT_STATE.set(state)
        try:
            if name == CALL_TOOL and kwargs.get("tool") == "spawn_agent":
                arguments = kwargs.get("arguments") or {}
                return await asyncio.to_thread(_offline_spawn_agent, **arguments)
            if name == LM_TOOL:
                operation = args[0] if args else kwargs.get("op")
                if operation != "request":
                    options = args[2] if len(args) > 2 else kwargs.get("options")
                    calls = max(1, int((options or {}).get("n") or 1))
                    state.reserve(calls)
                    state.telemetry.sub_lm_queries += calls
            elif name in {"llm_query", "llm_query_batched"}:
                batched = name == "llm_query_batched"
                argument = args[0] if args else kwargs.get("prompts" if batched else "prompt")
                if isinstance(argument, dict) and argument.get(TAG) == WIRE:
                    argument = argument.get("value")
                query_count = len(argument) if batched and isinstance(argument, list) else 1
                state.reserve(query_count)
                state.telemetry.sub_lm_queries += query_count
            return await invoke()
        finally:
            _ROLLOUT_STATE.reset(token)

    class _ScopedOfflineFactory:
        execution_instructions: str = getattr(
            slot.factory,
            "execution_instructions",
            interpreter.DaytonaCodeInterpreter.execution_instructions,
        )

        def __call__(self) -> Any:
            return slot.factory(on_execution=observe_execution, on_tool=observe_tool)

    return _ScopedOfflineFactory()


def _offline_spawn_agent(task: str) -> dict[str, Any]:
    """Delegate a workspace subtask to an isolated RLM and return its result."""
    state = _ROLLOUT_STATE.get()
    if state is None:
        raise CodeExecutionError("spawn_agent is unavailable outside an active workspace rollout")
    if not state.enable_spawn_agent:
        raise CodeExecutionError("spawn_agent is disabled for this task")
    if state.depth >= state.max_depth:
        raise CodeExecutionError("Maximum spawn_agent depth reached")
    state.reserve(1)
    state.telemetry.child_rlms += 1

    child_state = _RolloutState(
        rlm=state.rlm,
        settings=state.settings,
        sub_lm=state.sub_lm,
        telemetry=state.telemetry,
        budget=state.budget,
        depth=state.depth + 1,
        max_depth=state.max_depth,
        files_manifest=state.files_manifest,
        memories=state.memories,
        active_skills=state.active_skills,
        slot=state.slot,
        web_session=state.web_session,
        enable_web_tools=state.enable_web_tools,
        enable_spawn_agent=state.enable_spawn_agent,
    )
    child_factory = _build_scoped_factory(child_state)
    token = _ROLLOUT_STATE.set(child_state)
    try:
        ctx = (
            dspy.context(lm=state.sub_lm, interpreter_factory=child_factory)
            if child_factory is not None
            else dspy.context(lm=state.sub_lm)
        )
        with ctx:
            child_result = state.rlm(
                task=task,
                files_manifest=state.files_manifest,
                memories=state.memories,
                active_skills=state.active_skills,
                prior_turns=[],
                **({"interpreter_factory": child_factory} if child_factory is not None else {}),
            )
    finally:
        _ROLLOUT_STATE.reset(token)
    return {
        "answer": str(getattr(child_result, "answer", "")),
        "artifact_paths": list(getattr(child_result, "artifact_paths", []) or []),
    }


def _offline_web_search(query: str) -> dict[str, Any]:
    """Find public sources. Returns titles, URLs and excerpts for discovery."""
    state = _ROLLOUT_STATE.get()
    if state is None or not state.enable_web_tools or state.web_session is None:
        if state is not None:
            state.telemetry.web_calls += 1
        return {
            "error": "Web research tools are not enabled for this task.",
            "error_code": "tool_unavailable",
            "budget": {"used": 0, "limit": 0, "requested": False},
        }
    return state.web_session.web_search(query)


def _offline_web_open(url: str) -> dict[str, Any]:
    """Read a public page. Returns source URL and Markdown or an extraction error."""
    state = _ROLLOUT_STATE.get()
    if state is None or not state.enable_web_tools or state.web_session is None:
        if state is not None:
            state.telemetry.web_calls += 1
        return {
            "error": "Web research tools are not enabled for this task.",
            "error_code": "tool_unavailable",
            "budget": {"used": 0, "limit": 0, "requested": False},
        }
    return state.web_session.web_open(url)


def _build_offline_tools(*, include_web_tools: bool) -> list[Callable | dspy.Tool]:
    tools: list[Callable | dspy.Tool] = [dspy.Tool(_offline_spawn_agent, name="spawn_agent")]
    if include_web_tools:
        tools.extend(
            [
                dspy.Tool(_offline_web_search, name="web_search"),
                dspy.Tool(_offline_web_open, name="web_open"),
            ]
        )
    return tools


def _case_lookup_key(
    task: str,
    active_skills: str,
    memories: Sequence[str],
    prior_turns: Sequence[Mapping[str, Any]],
) -> tuple[str, str, str, str]:
    return (
        task,
        str(active_skills),
        json.dumps(list(memories), sort_keys=True),
        json.dumps(list(prior_turns), sort_keys=True),
    )


class _OfflineWorkspaceProgram(dspy.Module):
    """Full-fidelity offline RLM wrapper preserving GEPA signature.equals invariants."""

    def __init__(
        self,
        settings: Settings,
        *,
        sub_lm: dspy.BaseLM | None = None,
        pool: _DaytonaSandboxPool | None = None,
        include_web_tools: bool = False,
        case_specs: Mapping[str, _CaseSpec] | None = None,
        case_specs_by_key: Mapping[tuple[str, str, str, str], _CaseSpec] | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._sub_lm = (
            sub_lm if sub_lm is not None else agent._model(settings, settings.sub_llm_model)
        )
        self._pool = pool
        self._include_web_tools = include_web_tools
        self._case_specs: dict[str, _CaseSpec] = dict(case_specs or {})
        self._case_specs_by_key: dict[tuple[str, str, str, str], _CaseSpec] = dict(
            case_specs_by_key or {}
        )
        offline_tools = _build_offline_tools(include_web_tools=include_web_tools)
        self.rlm = agent.build_program(
            settings.model_copy(update={"optimized_program_path": None}),
            offline_tools,
            sub_lm=self._sub_lm,
        )
        agent.apply_submission_guidance(self.rlm)

    @property
    def generate_action(self) -> dspy.Predict:
        return self.rlm.generate_action

    @property
    def extract(self) -> dspy.Predict:
        return self.rlm.extract

    def __deepcopy__(self, memo: dict[int, Any]) -> "_OfflineWorkspaceProgram":
        copied = self.__class__.__new__(self.__class__)
        memo[id(self)] = copied
        copied._compiled = getattr(self, "_compiled", False)
        copied.callbacks = copy.deepcopy(getattr(self, "callbacks", []), memo)
        copied.history = copy.deepcopy(getattr(self, "history", []), memo)
        copied._settings = self._settings
        copied._sub_lm = self._sub_lm
        copied._pool = self._pool
        copied._include_web_tools = self._include_web_tools
        copied._case_specs = self._case_specs
        copied._case_specs_by_key = self._case_specs_by_key
        copied.rlm = self.rlm.deepcopy()
        return copied

    def _resolve_spec(
        self,
        *,
        task: str,
        active_skills: str,
        memories: Sequence[str],
        prior_turns: Sequence[Mapping[str, Any]],
        case_id: str | None,
    ) -> _CaseSpec | None:
        if case_id is not None and case_id in self._case_specs:
            return self._case_specs[case_id]
        return self._case_specs_by_key.get(
            _case_lookup_key(task, active_skills, memories, prior_turns)
        )

    def forward(
        self,
        task: str,
        files_manifest: list[dict[str, Any]] | None = None,
        memories: list[str] | None = None,
        active_skills: str = "",
        prior_turns: list[dict[str, str]] | None = None,
        *,
        case_id: str | None = None,
        input_files: Mapping[str, bytes] | None = None,
        web_fixtures: Mapping[str, Any] | None = None,
        enable_web_tools: bool | None = None,
        enable_spawn_agent: bool | None = None,
    ) -> dspy.Prediction:
        resolved_manifest = list(files_manifest or [])
        resolved_memories = list(memories or [])
        resolved_prior_turns = list(prior_turns or [])

        spec = self._resolve_spec(
            task=task,
            active_skills=active_skills,
            memories=resolved_memories,
            prior_turns=resolved_prior_turns,
            case_id=case_id,
        )
        staged_files: dict[str, bytes] = {}
        resolved_web: dict[str, Any] | None = None
        if spec is not None:
            staged_files, resolved_web = _resolve_case_fixtures(spec)
        if input_files:
            staged_files.update(input_files)
        if web_fixtures is not None:
            resolved_web = dict(web_fixtures)

        web_enabled = (
            enable_web_tools
            if enable_web_tools is not None
            else (spec.enable_web_tools if spec is not None else resolved_web is not None)
        )
        spawn_enabled = (
            enable_spawn_agent
            if enable_spawn_agent is not None
            else (spec.enable_spawn_agent if spec is not None else True)
        )

        telemetry = _RolloutTelemetry()
        web_session = _OfflineWebResearch(resolved_web or {}, telemetry) if web_enabled else None
        collected_artifacts: dict[str, bytes] = {}

        def execute_in_slot(slot: _SandboxSlot | None) -> dspy.Prediction:
            nonlocal resolved_manifest, collected_artifacts
            if slot is not None and self._pool is not None:
                staged_manifest = self._pool.run_coro(
                    interpreter.stage_workspace_bytes(
                        slot.sandbox,
                        staged_files,
                        timeout=self._settings.daytona_exec_timeout_seconds,
                    )
                )
                if staged_files or not resolved_manifest:
                    resolved_manifest = staged_manifest

            state = _RolloutState(
                rlm=self.rlm,
                settings=self._settings,
                sub_lm=self._sub_lm,
                telemetry=telemetry,
                budget=_SharedCallBudget(self._settings.rlm_max_llm_calls),
                depth=0,
                max_depth=self._settings.rlm_max_depth if spawn_enabled else 0,
                files_manifest=resolved_manifest,
                memories=resolved_memories,
                active_skills=active_skills,
                slot=slot,
                web_session=web_session,
                enable_web_tools=web_enabled,
                enable_spawn_agent=spawn_enabled,
            )
            scoped_factory = _build_scoped_factory(state)
            token = _ROLLOUT_STATE.set(state)
            try:
                rlm_kwargs: dict[str, Any] = {
                    "task": task,
                    "files_manifest": resolved_manifest,
                    "memories": resolved_memories,
                    "active_skills": active_skills,
                    "prior_turns": resolved_prior_turns,
                }
                if scoped_factory is not None:
                    rlm_kwargs["interpreter_factory"] = scoped_factory
                result = self.rlm(**rlm_kwargs)
            finally:
                _ROLLOUT_STATE.reset(token)

            trajectory = list(getattr(result, "trajectory", []) or [])
            telemetry.root_actions = len(trajectory)
            if slot is None:
                for step in trajectory:
                    if isinstance(step, Mapping) and str(step.get("output", "")).startswith(
                        "[Error]"
                    ):
                        telemetry.python_errors += 1
                        if len(telemetry.error_samples) < 5:
                            telemetry.error_samples.append(str(step.get("output", ""))[:160])
            else:
                for step in trajectory:
                    out = str(step.get("output", "")) if isinstance(step, Mapping) else ""
                    if out.startswith("[Error] SyntaxError"):
                        telemetry.python_errors += 1
                        if len(telemetry.error_samples) < 5:
                            telemetry.error_samples.append(out[:160])

            turn_tel = agent.TurnTelemetry()
            if hasattr(result, "history") and getattr(result.history, "messages", None):
                turn_tel.record_completion(result)
            telemetry.completion = turn_tel.completion

            if slot is not None and self._pool is not None:
                collected_artifacts = self._pool.run_coro(
                    interpreter.collect_workspace_artifacts(
                        slot.sandbox,
                        {},
                        timeout=self._settings.daytona_exec_timeout_seconds,
                    )
                )

            return dspy.Prediction(
                answer=str(getattr(result, "answer", "")),
                artifact_paths=list(getattr(result, "artifact_paths", []) or []),
                artifacts=collected_artifacts,
                telemetry=telemetry,
                history=getattr(result, "history", None),
                trajectory=trajectory,
                final_reasoning=getattr(result, "final_reasoning", None),
            )

        if self._pool is not None:
            with self._pool.checkout() as slot:
                return execute_in_slot(slot)
        return execute_in_slot(None)


@contextmanager
def _ephemeral_daytona_pool(
    settings: Settings,
    *,
    pool_size: int = 1,
) -> Generator[_DaytonaSandboxPool]:
    """Provision a pool of ephemeral Daytona sandboxes and always delete all of them."""
    if pool_size < 1:
        raise ValueError("pool_size must be at least 1")
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    release_event: asyncio.Event | None = None
    pool: _DaytonaSandboxPool | None = None
    startup_error: BaseException | None = None
    cleanup_error: BaseException | None = None

    async def lifecycle() -> None:
        nonlocal release_event, pool, startup_error, cleanup_error
        release_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                daytona = await stack.enter_async_context(interpreter.make_daytona_client(settings))
                slots: list[_SandboxSlot] = []
                for _ in range(pool_size):
                    sandbox = await stack.enter_async_context(
                        interpreter.ephemeral_sandbox(daytona, settings)
                    )
                    factory = interpreter.make_interpreter_factory(sandbox, settings, loop)
                    slots.append(_SandboxSlot(sandbox=sandbox, factory=factory, loop=loop))
                pool = _DaytonaSandboxPool(slots, loop)
                ready.set()
                await release_event.wait()
        except BaseException as error:
            if not ready.is_set():
                startup_error = error
                ready.set()
            else:
                cleanup_error = error

    def run_loop() -> None:
        nonlocal startup_error, cleanup_error
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(lifecycle())
        except BaseException as error:
            if not ready.is_set():
                startup_error = error
            else:
                cleanup_error = error
        finally:
            ready.set()
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                with suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with suppress(Exception):
                    loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    thread = threading.Thread(target=run_loop, name="fleet-optimize-daytona", daemon=True)
    thread.start()
    ready.wait()
    if startup_error is not None:
        thread.join()
        raise startup_error
    if pool is None:
        thread.join()
        raise RuntimeError("Failed to initialize ephemeral Daytona sandbox pool")

    body_failed = False
    try:
        yield pool
    except BaseException:
        body_failed = True
        raise
    finally:
        if release_event is not None and not loop.is_closed():
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(release_event.set)
        thread.join()
        if cleanup_error is not None:
            if body_failed:
                _LOG.warning(
                    "Failed to tear down ephemeral Daytona sandbox pool after optimization error",
                    exc_info=cleanup_error,
                )
            else:
                raise cleanup_error


@contextmanager
def _ephemeral_daytona_interpreter(
    settings: Settings,
) -> Generator[Callable[..., interpreter.DaytonaCodeInterpreter]]:
    """Run an ephemeral Daytona sandbox on a background event loop and always delete it."""
    with _ephemeral_daytona_pool(settings, pool_size=1) as pool:
        yield pool.interpreter_factory


@contextmanager
def _rlm_execution_context(
    settings: Settings,
    root_lm: dspy.BaseLM,
    *,
    program_kind: ProgramKind,
    local_interpreter: bool,
    num_threads: int | None = None,
) -> Generator[_DaytonaSandboxPool | None]:
    if program_kind == "rlm" and not local_interpreter:
        pool_size = max(1, min(num_threads or 2, 4))
        with (
            _ephemeral_daytona_pool(settings, pool_size=pool_size) as pool,
            dspy.context(lm=root_lm, interpreter_factory=pool.interpreter_factory),
        ):
            yield pool
        return
    with dspy.context(lm=root_lm):
        yield None


def _build_case_spec_tables(
    examples: Sequence[dspy.Example],
) -> tuple[dict[str, _CaseSpec], dict[tuple[str, str, str, str], _CaseSpec], bool]:
    by_id: dict[str, _CaseSpec] = {}
    by_key: dict[tuple[str, str, str, str], _CaseSpec] = {}
    any_web = False
    for index, example in enumerate(examples, 1):
        case_id = str(getattr(example, "case_id", None) or f"row-{index}")
        raw_mod_path = getattr(example, "fixtures_module_path", None)
        raw_fallback_dirs = getattr(example, "fixtures_fallback_dirs", None) or ()
        enable_web = bool(getattr(example, "enable_web_tools", False))
        any_web = any_web or enable_web
        spec = _CaseSpec(
            case_id=case_id,
            fixture_id=getattr(example, "fixture_id", None),
            fixtures_module_path=Path(raw_mod_path) if raw_mod_path else None,
            fixtures_fallback_dirs=tuple(Path(p) for p in raw_fallback_dirs if p),
            input_files=dict(getattr(example, "input_files", None) or {}),
            web_fixtures=getattr(example, "web_fixtures", None),
            enable_web_tools=enable_web,
            enable_spawn_agent=bool(getattr(example, "enable_spawn_agent", True)),
        )
        by_id[case_id] = spec
        key = _case_lookup_key(
            str(getattr(example, "task", "")),
            str(getattr(example, "active_skills", "")),
            list(getattr(example, "memories", []) or []),
            list(getattr(example, "prior_turns", []) or []),
        )
        by_key[key] = spec
    return by_id, by_key, any_web


def _with_case_id_inputs(examples: Sequence[dspy.Example]) -> list[dspy.Example]:
    wrapped: list[dspy.Example] = []
    for index, ex in enumerate(examples, 1):
        copied = ex.copy()
        if not getattr(copied, "case_id", None):
            copied.case_id = f"row-{index}"
        wrapped.append(
            copied.with_inputs(
                "task", "files_manifest", "memories", "active_skills", "prior_turns", "case_id"
            )
        )
    return wrapped


def _repeat_holdout_examples(
    holdout: Sequence[dspy.Example],
    *,
    repeats: int,
    include_case_id: bool,
) -> list[dspy.Example]:
    repeated: list[dspy.Example] = []
    input_keys = (
        ("task", "files_manifest", "memories", "active_skills", "prior_turns", "case_id")
        if include_case_id
        else ("task", "files_manifest", "memories", "active_skills", "prior_turns")
    )
    for index, ex in enumerate(holdout, 1):
        case_id = str(getattr(ex, "case_id", None) or f"row-{index}")
        for repeat in range(1, repeats + 1):
            copied = ex.copy()
            copied.case_id = case_id
            copied.eval_repeat = repeat
            repeated.append(copied.with_inputs(*input_keys))
    return repeated


def _program_changed(student: dspy.Module, compiled: dspy.Module) -> bool:
    base = student.rlm if isinstance(student, _OfflineWorkspaceProgram) else student
    cand = compiled.rlm if isinstance(compiled, _OfflineWorkspaceProgram) else compiled
    if getattr(base, "module_src", None) != getattr(cand, "module_src", None):
        return True
    base_preds = list(base.predictors())
    cand_preds = list(cand.predictors())
    if len(base_preds) != len(cand_preds):
        return True
    for base_pred, cand_pred in zip(base_preds, cand_preds, strict=True):
        base_instr = agent.strip_dynamic_tool_docs(
            str(getattr(getattr(base_pred, "signature", None), "instructions", "") or "")
        )
        cand_instr = agent.strip_dynamic_tool_docs(
            str(getattr(getattr(cand_pred, "signature", None), "instructions", "") or "")
        )
        if base_instr != cand_instr:
            return True
        if list(getattr(base_pred, "demos", []) or []) != list(
            getattr(cand_pred, "demos", []) or []
        ):
            return True
    return False


class _HoldoutMetricCollector:
    """Thread-safe holdout metric wrapper capturing per-(case, repeat) outcomes once."""

    def __init__(self, *, judge_lm: dspy.BaseLM | None) -> None:
        self._judge_lm = judge_lm
        self._lock = threading.Lock()
        self.outcomes: dict[tuple[str, int], _RlmCaseOutcome] = {}

    def reset(self) -> None:
        with self._lock:
            self.outcomes.clear()

    def metric(self, gold: dspy.Example, pred: Any, trace: Any = None) -> ScoreWithFeedback:
        outcome = _evaluate_rlm_case(gold, pred, judge_lm=self._judge_lm)
        case_id = str(getattr(gold, "case_id", None) or getattr(gold, "task", ""))
        repeat = int(getattr(gold, "eval_repeat", 1))
        with self._lock:
            self.outcomes[(case_id, repeat)] = outcome
        return ScoreWithFeedback(score=outcome.score, feedback=outcome.feedback)


def _extract_holdout_rows(
    eval_result: Any,
    collector_outcomes: Mapping[tuple[str, int], _RlmCaseOutcome],
    *,
    judge_lm: dspy.BaseLM | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    results = getattr(eval_result, "results", None)
    if results:
        for index, item in enumerate(results, 1):
            example, prediction, raw_score = item
            case_id = str(getattr(example, "case_id", None) or f"row-{index}")
            repeat = int(getattr(example, "eval_repeat", 1))
            outcome = collector_outcomes.get((case_id, repeat))
            if outcome is None:
                if (
                    hasattr(prediction, "answer")
                    and getattr(prediction, "answer", None) is not None
                ):
                    outcome = _evaluate_rlm_case(example, prediction, judge_lm=judge_lm)
                else:
                    numeric = float(raw_score.score if hasattr(raw_score, "score") else raw_score)
                    outcome = _RlmCaseOutcome(
                        score=numeric,
                        correctness=numeric,
                        passed=numeric >= 1.0,
                        feedback=str(getattr(raw_score, "feedback", "")),
                        blocking=bool(getattr(example, "blocking", False)),
                        telemetry=_telemetry_dict(prediction),
                    )
            rows.append(
                {
                    "case_id": case_id,
                    "lineage": _example_lineage(example),
                    "repeat": repeat,
                    "score": outcome.score,
                    "correctness": outcome.correctness,
                    "passed": outcome.passed,
                    "blocking": outcome.blocking,
                    "feedback": outcome.feedback,
                    "telemetry": outcome.telemetry,
                }
            )
    elif collector_outcomes:
        for (case_id, repeat), outcome in sorted(collector_outcomes.items()):
            rows.append(
                {
                    "case_id": case_id,
                    "lineage": None,
                    "repeat": repeat,
                    "score": outcome.score,
                    "correctness": outcome.correctness,
                    "passed": outcome.passed,
                    "blocking": outcome.blocking,
                    "feedback": outcome.feedback,
                    "telemetry": outcome.telemetry,
                }
            )
    return rows


def _summarize_holdout_rows(
    rows: Sequence[Mapping[str, Any]], fallback_score: float
) -> dict[str, float]:
    if not rows:
        return {
            "correctness": round(fallback_score / 100.0, 6),
            "submit_rate": 0.0,
            "mean_actions": 0.0,
            "py_errors": 0.0,
        }
    total = len(rows)
    correctness = sum(float(row["correctness"]) for row in rows) / total
    submit_count = sum(
        1 for row in rows if (row.get("telemetry") or {}).get("completion") == "submit"
    )
    mean_actions = (
        sum(int((row.get("telemetry") or {}).get("root_actions") or 0) for row in rows) / total
    )
    py_errors = float(
        sum(int((row.get("telemetry") or {}).get("python_errors") or 0) for row in rows)
    )
    return {
        "correctness": round(correctness, 6),
        "submit_rate": round(submit_count / total, 6),
        "mean_actions": round(mean_actions, 6),
        "py_errors": py_errors,
    }


def _paired_case_regressions(
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not baseline_rows or not candidate_rows:
        return []
    base_by_case: dict[str, list[bool]] = defaultdict(list)
    cand_by_case: dict[str, list[bool]] = defaultdict(list)
    for row in baseline_rows:
        base_by_case[str(row["case_id"])].append(bool(row["passed"]))
    for row in candidate_rows:
        cand_by_case[str(row["case_id"])].append(bool(row["passed"]))

    regressions: list[dict[str, Any]] = []
    for case_id in sorted(set(base_by_case) & set(cand_by_case)):
        base_rate = sum(base_by_case[case_id]) / len(base_by_case[case_id])
        cand_rate = sum(cand_by_case[case_id]) / len(cand_by_case[case_id])
        if cand_rate < base_rate:
            regressions.append(
                {
                    "case_id": case_id,
                    "baseline_pass_rate": round(base_rate, 6),
                    "candidate_pass_rate": round(cand_rate, 6),
                }
            )
    return regressions


def _write_report_artifact(
    report: Mapping[str, Any],
    *,
    log_dir: Path | None,
    mlflow_active: bool,
) -> None:
    payload = json.dumps(report, indent=2, default=str) + "\n"
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        report_path = log_dir / "report.json"
        report_path.write_text(payload, encoding="utf-8")
        if mlflow_active:
            _mlflow_api().log_artifact(str(report_path))
    elif mlflow_active:
        with TemporaryDirectory(prefix="fleet-opt-report-") as temporary:
            report_path = Path(temporary) / "report.json"
            report_path.write_text(payload, encoding="utf-8")
            _mlflow_api().log_artifact(str(report_path))


def optimize_rlm(
    settings: Settings,
    *,
    database_url: str,
    dataset: Path | None = None,
    output_path: Path | None = None,
    program_kind: ProgramKind = "rlm",
    train_ratio: float = 0.6,
    val_ratio: float | None = None,
    seed: int = 0,
    auto: Literal["light", "medium", "heavy"] | None = None,
    max_metric_calls: int | None = None,
    reflection_lm: str | None = None,
    judge_lm: str | None = None,
    holdout_repeats: int = DEFAULT_HOLDOUT_REPEATS,
    disable_cache: bool = True,
    num_threads: int | None = None,
    limit: int = 200,
    force: bool = False,
    allow_direct: bool = False,
    local_interpreter: bool = False,
    log_dir: Path | None = None,
    progress: _ProgressEmitter | None = None,
    role_models: Mapping[RoleName, RoleModelConfig] | None = None,
) -> OptimizationResult:
    """Compile dspy.RLM or dspy.Flex with GEPA and promote only on verified holdout improvement."""
    if auto is not None and max_metric_calls is not None:
        raise ValueError("Specify at most one of 'auto' and 'max_metric_calls'")
    if max_metric_calls is not None and max_metric_calls < 1:
        raise ValueError("max_metric_calls must be positive")
    if holdout_repeats < 1:
        raise ValueError("holdout_repeats must be positive")

    extra_secrets = _collect_settings_secrets(
        settings, database_url=database_url, role_models=role_models
    )
    if progress is not None:
        progress.add_secrets(extra_secrets)
        progress.emit("loading_dataset", "Loading evaluation dataset")
    safe_url = guard_database_url(database_url, settings, allow_direct=allow_direct)
    if dataset is None:
        _LOG.warning(
            "Running --target rlm without --dataset falls back to unverified past DB runs "
            "(missing ground-truth verifiers, memories, skills, and prior_turns). "
            "Pass --dataset <path.jsonl> for reliable optimization."
        )
        examples = asyncio.run(load_rlm_examples_from_db(safe_url, settings, limit=limit))
    else:
        examples = load_rlm_dataset(dataset, settings=settings)

    if not examples:
        raise ValueError("No labeled RLM examples found")

    resolved_val_ratio = val_ratio if val_ratio is not None else (1.0 - train_ratio) / 2.0
    trainset, valset, holdout = split_train_val_holdout(
        examples,
        train_ratio=train_ratio,
        val_ratio=resolved_val_ratio,
        seed=seed,
    )

    target_path = output_path or DEFAULT_OUTPUTS["rlm"]
    roles = role_models or {}

    def resolve_role(
        role: RoleName, fallback_model: str
    ) -> tuple[dspy.BaseLM, str, dict[str, Any]]:
        spec = roles.get(role)
        if spec is not None:
            lm_obj, routed_model, _mode = _build_role_lm(spec)
            prov = _role_provenance(
                model=routed_model,
                dialect=spec.dialect,
                provider_name=spec.provider_name,
                extra_secrets=extra_secrets,
            )
            if spec.reasoning_effort:
                prov["reasoning_effort"] = spec.reasoning_effort
            return lm_obj, routed_model, prov
        lm_obj = agent._model(settings, fallback_model)
        prov = _role_provenance(
            model=fallback_model,
            dialect=_infer_dialect(fallback_model),
            provider_name=None,
            extra_secrets=extra_secrets,
        )
        return lm_obj, fallback_model, prov

    root_lm, _root_model_name, root_prov = resolve_role("root_lm", settings.llm_model)
    sub_lm, _sub_model_name, sub_prov = resolve_role("sub_lm", settings.sub_llm_model)
    resolved_reflection_lm, reflection_model_name, reflection_prov = resolve_role(
        "reflection_lm", reflection_lm or settings.llm_model
    )
    resolved_judge_lm, judge_model_name, judge_prov = resolve_role(
        "judge_lm", judge_lm or settings.llm_model
    )

    if disable_cache:
        root_lm.cache = False
        sub_lm.cache = False
        resolved_reflection_lm.cache = False
    # Keep rubric judge evaluations uncached so repeated holdout measurements stay independent.
    resolved_judge_lm.cache = False

    resolved_max_metric_calls = max_metric_calls if auto is None else None
    if auto is None and resolved_max_metric_calls is None:
        resolved_max_metric_calls = DEFAULT_MAX_METRIC_CALLS

    case_specs_by_id, case_specs_by_key, any_web_tools = _build_case_spec_tables(examples)
    raw_gepa_metric = _make_rlm_metric(judge_lm=resolved_judge_lm)
    holdout_collector = _HoldoutMetricCollector(judge_lm=resolved_judge_lm)

    gepa_trainset = _with_case_id_inputs(trainset) if program_kind == "rlm" else list(trainset)
    gepa_valset = _with_case_id_inputs(valset) if program_kind == "rlm" else list(valset)
    repeated_holdout = _repeat_holdout_examples(
        holdout, repeats=holdout_repeats, include_case_id=(program_kind == "rlm")
    )
    holdout_total = len(repeated_holdout)
    eval_phase = "baseline"
    baseline_score_live: float | None = None
    holdout_lock = threading.Lock()
    holdout_done = 0

    def tracked_holdout_metric(
        gold: dspy.Example, pred: Any, trace: Any = None
    ) -> ScoreWithFeedback:
        nonlocal holdout_done
        result = holdout_collector.metric(gold, pred, trace)
        if progress is not None:
            with holdout_lock:
                holdout_done += 1
                done = holdout_done
            stage = "baseline_eval" if eval_phase == "baseline" else "holdout_eval"
            label = (
                "Evaluating baseline on holdout"
                if eval_phase == "baseline"
                else "Evaluating compiled candidate on holdout"
            )
            progress.emit(
                stage,
                label,
                completed=done,
                total=holdout_total,
                baseline_score=baseline_score_live,
            )
        return result

    gepa_lock = threading.Lock()
    gepa_calls = 0
    gepa_window: deque[float] = deque(maxlen=max(1, len(gepa_valset)))
    gepa_best_score: float | None = None

    def tracked_gepa_metric(
        gold: dspy.Example,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
        program_trace: Any = None,
    ) -> ScoreWithFeedback:
        nonlocal gepa_calls, gepa_best_score
        result = raw_gepa_metric(gold, pred, trace, pred_name, pred_trace, program_trace)
        if progress is not None:
            score_val = float(result.score) if math.isfinite(float(result.score)) else 0.0
            with gepa_lock:
                gepa_calls += 1
                calls = gepa_calls
                gepa_window.append(score_val)
                window_pct = round(100.0 * (sum(gepa_window) / len(gepa_window)), 2)
                if gepa_best_score is None or window_pct > gepa_best_score:
                    gepa_best_score = window_pct
                live_best = gepa_best_score
            if calls == 1 or calls % 3 == 0 or calls == resolved_max_metric_calls:
                progress.emit(
                    "gepa_compile",
                    "Running GEPA prompt evolution",
                    completed=calls,
                    total=resolved_max_metric_calls,
                    baseline_score=baseline_score_live,
                    best_score=live_best,
                )
        return result

    mlflow_run_id: str | None = None
    with _mlflow_tracking(settings, "rlm") as mlflow_active:
        mlflow_run_id = _active_mlflow_run_id()
        if progress is not None and program_kind == "rlm" and not local_interpreter:
            progress.emit(
                "provisioning_sandboxes",
                "Provisioning ephemeral Daytona sandboxes",
                message=f"train={len(trainset)} val={len(valset)} holdout={len(holdout)}",
            )
        with _rlm_execution_context(
            settings,
            root_lm,
            program_kind=program_kind,
            local_interpreter=local_interpreter,
            num_threads=num_threads,
        ) as pool:
            student: dspy.Module = (
                dspy.Flex(agent.WorkspaceTask)
                if program_kind == "flex"
                else _OfflineWorkspaceProgram(
                    settings,
                    sub_lm=sub_lm,
                    pool=pool,
                    include_web_tools=any_web_tools,
                    case_specs=case_specs_by_id,
                    case_specs_by_key=case_specs_by_key,
                )
            )
            progress_active = progress is not None and progress.enabled
            evaluator = dspy.Evaluate(
                devset=repeated_holdout,
                metric=tracked_holdout_metric if progress_active else holdout_collector.metric,
                num_threads=num_threads,
                display_progress=False,
            )
            if progress is not None:
                progress.emit(
                    "baseline_eval",
                    "Evaluating baseline on holdout",
                    completed=0,
                    total=holdout_total,
                )
            holdout_collector.reset()
            baseline_eval = evaluator(student)
            baseline_score = _eval_score(baseline_eval)
            baseline_score_live = baseline_score
            baseline_rows = _extract_holdout_rows(
                baseline_eval, holdout_collector.outcomes, judge_lm=resolved_judge_lm
            )

            gepa_kwargs: dict[str, Any] = {
                "metric": tracked_gepa_metric if progress_active else raw_gepa_metric,
                "reflection_lm": resolved_reflection_lm,
                "num_threads": num_threads,
                "log_dir": str(log_dir) if log_dir else None,
                "seed": seed,
                "track_stats": True,
            }
            if auto is not None:
                gepa_kwargs["auto"] = auto
            else:
                assert resolved_max_metric_calls is not None
                minibatch = min(3, len(gepa_trainset))
                headroom = 2 * minibatch + len(gepa_valset)
                stop_limit = (
                    max(1, resolved_max_metric_calls - headroom) + 1
                    if resolved_max_metric_calls > headroom
                    else resolved_max_metric_calls
                )
                gepa_kwargs["max_metric_calls"] = resolved_max_metric_calls
                gepa_kwargs["reflection_minibatch_size"] = minibatch
                gepa_kwargs["gepa_kwargs"] = {"stop_callbacks": MaxMetricCallsStopper(stop_limit)}

            if progress is not None:
                progress.emit(
                    "gepa_compile",
                    "Running GEPA prompt evolution",
                    completed=0,
                    total=resolved_max_metric_calls,
                    baseline_score=baseline_score,
                )
            gepa = dspy.GEPA(**gepa_kwargs)
            compiled = gepa.compile(student, trainset=gepa_trainset, valset=gepa_valset)

            eval_phase = "holdout"
            holdout_done = 0
            holdout_collector.reset()
            if progress is not None:
                progress.emit(
                    "holdout_eval",
                    "Evaluating compiled candidate on holdout",
                    completed=0,
                    total=holdout_total,
                    baseline_score=baseline_score,
                    best_score=gepa_best_score,
                )
            optimized_eval = evaluator(compiled)
            optimized_score = _eval_score(optimized_eval)
            candidate_rows = _extract_holdout_rows(
                optimized_eval, holdout_collector.outcomes, judge_lm=resolved_judge_lm
            )

        instructions_changed = _program_changed(student, compiled)
        baseline_instructions = _extract_predictor_instructions(student)
        optimized_instructions = _extract_predictor_instructions(compiled)
        instruction_diffs = _build_instruction_diffs(baseline_instructions, optimized_instructions)
        baseline_summary = _summarize_holdout_rows(baseline_rows, baseline_score)
        candidate_summary = _summarize_holdout_rows(candidate_rows, optimized_score)
        blocking_failures = [
            row for row in candidate_rows if row.get("blocking") and not row.get("passed")
        ]
        paired_regressions = _paired_case_regressions(baseline_rows, candidate_rows)
        case_deltas = _build_rlm_case_deltas(
            baseline_rows,
            candidate_rows,
            holdout,
            blocking_failures=blocking_failures,
            paired_regressions=paired_regressions,
        )

        gate_checks = _build_rlm_gate_checks(
            instructions_changed=instructions_changed,
            baseline_score=baseline_score,
            optimized_score=optimized_score,
            baseline_correctness=baseline_summary["correctness"],
            candidate_correctness=candidate_summary["correctness"],
            blocking_failure_count=len(blocking_failures),
            paired_regression_count=len(paired_regressions),
        )
        eligible = all(check["passed"] for check in gate_checks)
        saved = eligible or force
        if progress is not None:
            progress.emit(
                "saving_artifact",
                "Verifying holdout promotion gate",
                baseline_score=baseline_score,
                best_score=optimized_score,
                message="Saved" if saved else "Discarded by holdout gate",
            )
        if saved:
            _save_program(compiled, target_path)

        detailed = getattr(compiled, "detailed_results", None)
        search_metric_calls = getattr(detailed, "total_metric_calls", None)
        if not isinstance(search_metric_calls, int) or isinstance(search_metric_calls, bool):
            search_metric_calls = None
        candidate_history = _extract_gepa_candidate_history(detailed, extra_secrets=extra_secrets)
        report: dict[str, Any] = {
            "target": "rlm",
            "program_kind": program_kind,
            "instructions_changed": instructions_changed,
            "baseline_instructions": baseline_instructions,
            "optimized_instructions": optimized_instructions,
            "instruction_diffs": instruction_diffs,
            "eligible": eligible,
            "gate_checks": gate_checks,
            "saved": saved,
            "force": force,
            "mlflow_run_id": mlflow_run_id,
            "mlflow_experiment": settings.mlflow_experiment if mlflow_active else None,
            "holdout_repeats": holdout_repeats,
            "disable_cache": disable_cache,
            "budget": {
                "auto": auto,
                "max_metric_calls": resolved_max_metric_calls,
                "search_metric_calls": search_metric_calls,
            },
            "models": {
                "root_lm": root_prov,
                "sub_lm": sub_prov,
                "reflection_lm": reflection_prov,
                "judge_lm": judge_prov,
            },
            "candidate_history": candidate_history,
            "lineages": {
                "train": sorted({name for ex in trainset if (name := _example_lineage(ex))}),
                "val": sorted({name for ex in valset if (name := _example_lineage(ex))}),
                "holdout": sorted({name for ex in holdout if (name := _example_lineage(ex))}),
            },
            "metrics": {
                "baseline_holdout_score": baseline_score,
                "optimized_holdout_score": optimized_score,
                "baseline_correctness": baseline_summary["correctness"],
                "optimized_correctness": candidate_summary["correctness"],
                "baseline_submit_rate": baseline_summary["submit_rate"],
                "optimized_submit_rate": candidate_summary["submit_rate"],
                "baseline_mean_actions": baseline_summary["mean_actions"],
                "optimized_mean_actions": candidate_summary["mean_actions"],
                "baseline_py_errors": baseline_summary["py_errors"],
                "optimized_py_errors": candidate_summary["py_errors"],
                "paired_regressions": len(paired_regressions),
                "blocking_failures": len(blocking_failures),
            },
            "blocking_failures": blocking_failures,
            "paired_regressions": paired_regressions,
            "case_deltas": case_deltas,
            "baseline_cases": baseline_rows,
            "candidate_cases": candidate_rows,
        }
        _write_report_artifact(report, log_dir=log_dir, mlflow_active=mlflow_active)

        if mlflow_active:
            tracking = _mlflow_api()
            tracking.log_params(
                {
                    "target": "rlm",
                    "program_kind": program_kind,
                    "auto": auto if auto is not None else "budget",
                    "max_metric_calls": resolved_max_metric_calls,
                    "holdout_repeats": holdout_repeats,
                    "disable_cache": disable_cache,
                    "reflection_lm": reflection_model_name,
                    "judge_lm": judge_model_name,
                    "train_size": len(trainset),
                    "val_size": len(valset),
                    "holdout_size": len(holdout),
                    "local_interpreter": local_interpreter,
                    "force": force,
                }
            )
            tracking.log_metrics(
                {
                    "baseline_holdout_score": baseline_score,
                    "optimized_holdout_score": optimized_score,
                    "baseline_correctness": baseline_summary["correctness"],
                    "optimized_correctness": candidate_summary["correctness"],
                    "baseline_submit_rate": baseline_summary["submit_rate"],
                    "optimized_submit_rate": candidate_summary["submit_rate"],
                    "baseline_mean_actions": baseline_summary["mean_actions"],
                    "optimized_mean_actions": candidate_summary["mean_actions"],
                    "baseline_py_errors": baseline_summary["py_errors"],
                    "optimized_py_errors": candidate_summary["py_errors"],
                    "paired_regressions": float(len(paired_regressions)),
                    "blocking_failures": float(len(blocking_failures)),
                    "instructions_changed": 1.0 if instructions_changed else 0.0,
                    "saved": 1.0 if saved else 0.0,
                }
            )
            if saved:
                tracking.log_artifact(str(target_path))

    if progress is not None:
        progress.emit(
            "completed",
            "Optimization complete",
            status="completed",
            baseline_score=baseline_score,
            best_score=optimized_score,
            message="Artifact saved" if saved else "Discarded by holdout gate",
        )

    return OptimizationResult(
        target="rlm",
        output_path=target_path,
        saved=saved,
        baseline_score=baseline_score,
        optimized_score=optimized_score,
        train_size=len(trainset),
        holdout_size=len(holdout),
        val_size=len(valset),
        mlflow_run_id=mlflow_run_id,
        report=report,
    )


def _reanchor_fitted_changed(report: Mapping[str, Any]) -> tuple[bool, list[str]]:
    fitted = report.get("fitted")
    if isinstance(fitted, list):
        accepted = [
            row
            for row in fitted
            if isinstance(row, Mapping) and "skipped" not in row and "value" in row
        ]
        fields = [str(row.get("field", "")) for row in accepted if row.get("field")]
        return bool(accepted), fields
    if isinstance(fitted, Mapping):
        fields = [
            str(key)
            for key, value in fitted.items()
            if value and not (isinstance(value, Mapping) and value.get("skipped"))
        ]
        return bool(fields), fields
    return False, []


def _fetch_jev_metadata(
    decision_model: str,
    decision_lm: Any,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"configured_model": decision_model}
    history = getattr(decision_lm, "history", None)
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, Mapping):
            response = last.get("response")
            resolved = (
                response.get("model")
                if isinstance(response, Mapping)
                else getattr(response, "model", None)
            )
            if isinstance(resolved, str) and resolved.strip():
                metadata["resolved_model"] = resolved.strip()

    resolved_key = api_key if api_key else os.environ.get("TYPESAFE_API_KEY")
    if (
        not decision_model.startswith("typesafe:")
        or not resolved_key
        or not isinstance(decision_lm, dspy.BaseLM)
    ):
        return metadata

    requested_id = decision_model.removeprefix("typesafe:").strip()
    models_url = "https://api.typesafe.ai/v1/models"
    if base_url and base_url.strip():
        trimmed = base_url.strip().rstrip("/")
        models_url = trimmed if trimmed.endswith("/models") else f"{trimmed}/models"
    try:
        response = httpx.get(
            models_url,
            headers={"Authorization": f"Bearer {resolved_key}"},
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        models = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, Mapping) and entry.get("id") == requested_id:
                    if "release_date" in entry:
                        metadata["release_date"] = str(entry["release_date"])
                    if "id" in entry:
                        metadata["catalog_id"] = str(entry["id"])
                    break
    except Exception as error:
        _LOG.debug("Skipping non-fatal TypeSafe model metadata lookup: %s", error)
    return metadata


def _evaluate_skill_diagnostics(
    predictor: dspy.Module,
    examples: Sequence[dspy.Example],
    decision_lm: dspy.BaseLM,
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    neg_total = 0
    neg_fp = 0
    pos_total = 0
    pos_fn = 0
    primary_correct = 0
    multi_total = 0
    multi_correct = 0
    secondary_total = 0
    secondary_hits = 0

    weights = (
        getattr(predictor, "fields", {}).get("primary_skill", {}).get("weights", {})
        if isinstance(getattr(predictor, "fields", None), Mapping)
        else {}
    )

    with dspy.context(lm=decision_lm):
        for index, ex in enumerate(examples, 1):
            pred = predictor(**ex.inputs())
            gold_needs = bool(getattr(ex, "needs_skill", True))
            needs_obj = getattr(pred, "needs_skill", False)
            pred_needs = bool(_decision_value(needs_obj))
            needs_prob = _decision_probability(needs_obj)
            raw_primary = getattr(pred, "primary_skill", _MISSING)
            pred_skill = _decision_value(raw_primary) if raw_primary is not _MISSING else None
            if isinstance(pred_skill, str):
                pred_skill = pred_skill.removeprefix("@").strip()

            exact = False
            if not gold_needs:
                neg_total += 1
                if pred_needs:
                    neg_fp += 1
                else:
                    exact = True
            else:
                pos_total += 1
                if not pred_needs:
                    pos_fn += 1
                elif raw_primary is _MISSING or pred_skill == getattr(ex, "expected_skill", None):
                    primary_correct += 1
                    exact = True

                secondaries = list(getattr(ex, "secondary_skills", []) or [])
                if secondaries and raw_primary is not _MISSING:
                    secondary_total += 1
                    probs = getattr(raw_primary, "probabilities", None) or {}
                    if isinstance(probs, Mapping) and pred_needs:
                        ranked = sorted(
                            (
                                name
                                for name, prob in probs.items()
                                if float(prob) >= skills.DEFAULT_SELECTION_THRESHOLD
                            ),
                            key=lambda name: (
                                -float(probs[name]) * float(weights.get(name, 1.0)),
                                -float(probs[name]),
                                name,
                            ),
                        )
                        if any(sec in ranked[:2] for sec in secondaries):
                            secondary_hits += 1

            if getattr(ex, "is_multi_turn", False):
                multi_total += 1
                if exact:
                    multi_correct += 1

            if records is not None:
                expected_label = (
                    str(getattr(ex, "expected_skill", None) or "skill") if gold_needs else "none"
                )
                if not pred_needs:
                    pred_label = "none"
                elif raw_primary is _MISSING:
                    pred_label = str(getattr(ex, "expected_skill", None) or "skill")
                else:
                    pred_label = str(pred_skill or "none")
                records.append(
                    {
                        "case_id": str(getattr(ex, "case_id", None) or f"holdout-{index}"),
                        "lineage": _example_lineage(ex),
                        "input_preview": str(
                            getattr(ex, "raw_task", None) or getattr(ex, "task", "")
                        )[:180],
                        "expected": expected_label,
                        "pred_label": pred_label,
                        "pred_prob": round(needs_prob, 6) if needs_prob is not None else None,
                        "score": float(skill_metric(ex, pred)),
                        "passed": exact,
                    }
                )

    metrics: dict[str, float] = {
        "fp_rate": round(neg_fp / neg_total, 6) if neg_total else 0.0,
        "fn_rate": round(pos_fn / pos_total, 6) if pos_total else 0.0,
        "primary_accuracy": round(primary_correct / pos_total, 6) if pos_total else 1.0,
    }
    if multi_total:
        metrics["multi_turn_accuracy"] = round(multi_correct / multi_total, 6)
    if secondary_total:
        metrics["secondary_hit_rate"] = round(secondary_hits / secondary_total, 6)
    return metrics


def _evaluate_memory_diagnostics(
    predictor: dspy.Module,
    examples: Sequence[dspy.Example],
    decision_lm: dspy.BaseLM,
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    neg_total = 0
    neg_fp = 0
    auto_approve_fp = 0
    pos_total = 0
    pos_fn = 0
    action_correct = 0
    scope_correct = 0

    with dspy.context(lm=decision_lm):
        for index, ex in enumerate(examples, 1):
            pred = predictor(**ex.inputs())
            gold_worth = bool(getattr(ex, "worth_saving", False))
            worth_obj = getattr(pred, "worth_saving", False)
            pred_worth = bool(_decision_value(worth_obj))
            pred_prob = _decision_probability(worth_obj)
            pred_action = _decision_value(getattr(pred, "action", None))
            pred_scope = _decision_value(getattr(pred, "scope", None))

            saved_pred = pred_worth and pred_action != "skip"
            if not gold_worth:
                neg_total += 1
                if saved_pred:
                    neg_fp += 1
                    if pred_prob is not None and pred_prob >= _AUTO_APPROVE_THRESHOLD:
                        auto_approve_fp += 1
            else:
                pos_total += 1
                if not saved_pred:
                    pos_fn += 1
                else:
                    if pred_action == getattr(ex, "action", None):
                        action_correct += 1
                    if pred_scope == getattr(ex, "scope", None):
                        scope_correct += 1

            if records is not None:
                score = float(memory_metric(ex, pred))
                gold_action = str(getattr(ex, "action", "insert") or "insert")
                gold_scope = str(getattr(ex, "scope", "session") or "session")
                expected_label = f"{gold_action}:{gold_scope}" if gold_worth else "skip"
                pred_label = f"{pred_action}:{pred_scope}" if saved_pred else "skip"
                records.append(
                    {
                        "case_id": str(getattr(ex, "case_id", None) or f"holdout-{index}"),
                        "lineage": _example_lineage(ex),
                        "input_preview": str(getattr(ex, "task", "") or "")[:180],
                        "expected": expected_label,
                        "pred_label": pred_label,
                        "pred_prob": round(pred_prob, 6) if pred_prob is not None else None,
                        "score": score,
                        "passed": score >= 1.0,
                    }
                )

    return {
        "fp_rate": round(neg_fp / neg_total, 6) if neg_total else 0.0,
        "fn_rate": round(pos_fn / pos_total, 6) if pos_total else 0.0,
        "auto_approve_fp_count": float(auto_approve_fp),
        "action_accuracy": round(action_correct / pos_total, 6) if pos_total else 1.0,
        "scope_accuracy": round(scope_correct / pos_total, 6) if pos_total else 1.0,
    }


def _run_reanchor(
    settings: Settings,
    *,
    target: Literal["skills", "memory"],
    database_url: str,
    examples: Sequence[dspy.Example],
    predictor: dspy.Module,
    metric: Any,
    output_path: Path,
    env_var: str,
    train_ratio: float,
    seed: int,
    num_threads: int | None,
    allow_direct: bool,
    log_dir: Path | None,
    require_cache: bool,
    force: bool = False,
    progress: _ProgressEmitter | None = None,
    role_models: Mapping[RoleName, RoleModelConfig] | None = None,
) -> OptimizationResult:
    extra_secrets = _collect_settings_secrets(
        settings, database_url=database_url, role_models=role_models
    )
    if progress is not None:
        progress.add_secrets(extra_secrets)
    guard_database_url(database_url, settings, allow_direct=allow_direct)

    def stratify(ex: dspy.Example) -> Any:
        if target == "skills":
            return (bool(getattr(ex, "needs_skill", True)), getattr(ex, "expected_skill", None))
        return (
            bool(getattr(ex, "worth_saving", False)),
            getattr(ex, "action", None),
            getattr(ex, "scope", None),
        )

    if len(examples) >= 2:
        trainset, holdout = split_examples(
            examples, train_ratio=train_ratio, seed=seed, stratify_key=stratify
        )
    else:
        trainset, holdout = list(examples), None

    progress_active = progress is not None and progress.enabled
    decision_spec = (role_models or {}).get("decision_lm")
    if decision_spec is not None:
        decision_lm: Any
        decision_lm, effective_decision_model, _mode = _build_role_lm(decision_spec)
        decision_prov = _role_provenance(
            model=effective_decision_model,
            dialect=decision_spec.dialect,
            provider_name=decision_spec.provider_name,
            extra_secrets=extra_secrets,
        )
    else:
        effective_decision_model = settings.decision_model
        decision_lm = dspy.LM(settings.decision_model)
        decision_prov = _role_provenance(
            model=settings.decision_model,
            dialect=_infer_dialect(settings.decision_model),
            provider_name=None,
            extra_secrets=extra_secrets,
        )

    reanchor_lock = threading.Lock()
    reanchor_calls = 0
    reanchor_window: deque[float] = deque(maxlen=max(1, len(trainset)))
    reanchor_best_score: float | None = None

    def tracked_metric(gold: dspy.Example, pred: Any, trace: Any = None) -> float:
        nonlocal reanchor_calls, reanchor_best_score
        score = float(metric(gold, pred, trace))
        if progress is not None and progress.enabled:
            with reanchor_lock:
                reanchor_calls += 1
                calls = reanchor_calls
                if math.isfinite(score):
                    reanchor_window.append(score)
                    mean_score = round(sum(reanchor_window) / len(reanchor_window), 6)
                    if reanchor_best_score is None or mean_score > reanchor_best_score:
                        reanchor_best_score = mean_score
                live_best = reanchor_best_score
            if calls == 1 or calls % 10 == 0:
                progress.emit(
                    "reanchor_cv",
                    "Calibrating thresholds & Choice weights via ReAnchor CV",
                    completed=calls,
                    total=None,
                    best_score=live_best,
                )
        return score

    mlflow_run_id: str | None = None
    with (
        _mlflow_tracking(settings, target) as mlflow_active,
        dspy.context(lm=decision_lm),
    ):
        mlflow_run_id = _active_mlflow_run_id()
        baseline_predictor = predictor.deepcopy()
        baseline_parameters = _extract_reanchor_parameters(baseline_predictor)
        if progress is not None and progress.enabled:
            progress.emit(
                "reanchor_cv",
                "Calibrating thresholds & Choice weights via ReAnchor CV",
                message=f"train={len(trainset)} holdout={len(holdout) if holdout else 0}",
            )
        reanchor = ReAnchor(
            tracked_metric if progress_active else metric,
            num_threads=num_threads,
            log_dir=log_dir,
            require_cache=require_cache,
        )
        compiled = reanchor.compile(predictor, trainset=trainset, valset=holdout)
        report = dict(reanchor.report)

        if holdout:
            baseline_score = report.get("val_score_before")
            optimized_score = report.get("val_score")
        else:
            baseline_score = report.get("train_score_before")
            optimized_score = report.get("train_score")

        baseline_score_num = (
            float(baseline_score) if isinstance(baseline_score, (int, float)) else None
        )
        optimized_score_num = (
            float(optimized_score) if isinstance(optimized_score, (int, float)) else None
        )

        fitted_changed, fitted_fields = _reanchor_fitted_changed(report)
        fitted_parameters = _extract_reanchor_parameters(compiled)
        parameter_diffs = _build_reanchor_parameter_diffs(baseline_parameters, fitted_parameters)

        diag_before: dict[str, float] = {}
        diag_after: dict[str, float] = {}
        before_records: list[dict[str, Any]] = []
        after_records: list[dict[str, Any]] = []
        if holdout and isinstance(decision_lm, dspy.BaseLM):
            if progress is not None:
                progress.emit(
                    "holdout_eval",
                    "Evaluating FP/FN diagnostics on holdout",
                    baseline_score=baseline_score_num,
                    best_score=optimized_score_num,
                )
            eval_fn = (
                _evaluate_skill_diagnostics if target == "skills" else _evaluate_memory_diagnostics
            )
            diag_before = eval_fn(baseline_predictor, holdout, decision_lm, records=before_records)
            diag_after = eval_fn(compiled, holdout, decision_lm, records=after_records)

        case_deltas, fixed_cases, regressed_cases = _build_reanchor_case_deltas(
            before_records, after_records
        )

        fp_rate_before: float | None = diag_before.get(
            "fp_rate",
            float(report["fp_rate_before"]) if "fp_rate_before" in report else None,
        )
        fp_rate_after: float | None = diag_after.get(
            "fp_rate",
            float(report["fp_rate_after"]) if "fp_rate_after" in report else None,
        )
        score_improved = (
            baseline_score_num is not None
            and optimized_score_num is not None
            and optimized_score_num > baseline_score_num
        )
        fp_non_regressing = (
            True
            if not holdout or fp_rate_before is None or fp_rate_after is None
            else fp_rate_after <= fp_rate_before
        )
        gate_checks = _build_reanchor_gate_checks(
            fitted_changed=fitted_changed,
            fitted_fields=fitted_fields,
            score_improved=score_improved,
            baseline_score=baseline_score_num,
            optimized_score=optimized_score_num,
            fp_non_regressing=fp_non_regressing,
            fp_rate_before=fp_rate_before,
            fp_rate_after=fp_rate_after,
            has_holdout=bool(holdout),
        )
        eligible = all(check["passed"] for check in gate_checks)
        saved = bool(eligible or force)
        if progress is not None:
            progress.emit(
                "saving_artifact",
                "Verifying ReAnchor save gate",
                baseline_score=baseline_score_num,
                best_score=optimized_score_num,
                message="Saved" if saved else "Discarded by save gate",
            )
        if saved:
            _save_program(compiled, output_path)

        jev_metadata = _fetch_jev_metadata(
            effective_decision_model,
            decision_lm,
            api_key=decision_spec.api_key if decision_spec is not None else None,
            base_url=decision_spec.base_url if decision_spec is not None else None,
        )
        resolved_model = str(jev_metadata.get("resolved_model") or "").strip()
        if (
            resolved_model
            and effective_decision_model.startswith("typesafe:")
            and ":" not in resolved_model
        ):
            resolved_decision_model = f"typesafe:{resolved_model}"
        else:
            resolved_decision_model = resolved_model or effective_decision_model
        report.update(
            {
                "target": target,
                "eligible": eligible,
                "gate_checks": gate_checks,
                "fitted_changed": fitted_changed,
                "fitted_fields": fitted_fields,
                "fitted_parameters": fitted_parameters,
                "parameter_diffs": parameter_diffs,
                "fp_rate_before": fp_rate_before,
                "fp_rate_after": fp_rate_after,
                "diagnostics_before": diag_before,
                "diagnostics_after": diag_after,
                "case_deltas": case_deltas,
                "fixed_cases": fixed_cases,
                "regressed_cases": regressed_cases,
                "models": {"decision_lm": decision_prov},
                "decision_model_metadata": jev_metadata,
                "resolved_decision_model": resolved_decision_model,
                "mlflow_run_id": mlflow_run_id,
                "mlflow_experiment": settings.mlflow_experiment if mlflow_active else None,
                "saved": saved,
                "force": force,
            }
        )
        if "auto_approve_fp_count" in diag_after:
            report["auto_approve_fp_count_before"] = diag_before.get("auto_approve_fp_count", 0.0)
            report["auto_approve_fp_count_after"] = diag_after["auto_approve_fp_count"]

        _write_report_artifact(report, log_dir=log_dir, mlflow_active=mlflow_active)

        if mlflow_active:
            tracking = _mlflow_api()
            params: dict[str, Any] = {
                "target": target,
                "decision_model": effective_decision_model,
                "train_size": len(trainset),
                "holdout_size": len(holdout) if holdout else 0,
                "fitted_fields": ",".join(fitted_fields),
                "force": force,
            }
            if "resolved_model" in jev_metadata:
                params["decision_model_resolved"] = jev_metadata["resolved_model"]
            if "release_date" in jev_metadata:
                params["decision_model_release_date"] = jev_metadata["release_date"]
            tracking.log_params(params)

            metrics: dict[str, float] = {
                "saved": 1.0 if saved else 0.0,
                "eligible": 1.0 if eligible else 0.0,
            }
            if baseline_score_num is not None:
                metrics["baseline_score"] = baseline_score_num
            if optimized_score_num is not None:
                metrics["optimized_score"] = optimized_score_num
            if fp_rate_before is not None:
                metrics["fp_rate_before"] = fp_rate_before
            if fp_rate_after is not None:
                metrics["fp_rate_after"] = fp_rate_after
            for key, value in diag_before.items():
                if key != "fp_rate":
                    metrics[f"{key}_before"] = float(value)
            for key, value in diag_after.items():
                if key != "fp_rate":
                    metrics[f"{key}_after"] = float(value)
            tracking.log_metrics(metrics)
            if saved:
                tracking.log_artifact(str(output_path))

    if progress is not None:
        progress.emit(
            "completed",
            "Optimization complete",
            status="completed",
            baseline_score=baseline_score_num,
            best_score=optimized_score_num,
            message="Artifact saved" if saved else "Discarded by save gate",
        )

    return OptimizationResult(
        target=target,
        output_path=output_path,
        saved=saved,
        baseline_score=baseline_score_num,
        optimized_score=optimized_score_num,
        train_size=len(trainset),
        holdout_size=len(holdout) if holdout else 0,
        reminder=_pin_reminder(env_var, output_path, resolved_decision_model),
        mlflow_run_id=mlflow_run_id,
        report=report,
    )


def optimize_skills(
    settings: Settings,
    *,
    database_url: str,
    dataset: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.7,
    seed: int = 0,
    num_threads: int | None = None,
    allow_direct: bool = False,
    log_dir: Path | None = None,
    require_cache: bool = True,
    force: bool = False,
    progress: _ProgressEmitter | None = None,
    role_models: Mapping[RoleName, RoleModelConfig] | None = None,
) -> OptimizationResult:
    """Calibrate SkillSelector thresholds and Choice weights via 5-fold ReAnchor CV."""
    if progress is not None:
        progress.emit("loading_dataset", "Loading labeled Skill Selector dataset")
    registry = skills.SkillRegistry.load(
        settings.skills_dir, fallback_to_bundled=True
    ).with_installed(settings.skills_dir)
    if not registry.skills:
        raise ValueError(f"No skills found in {settings.skills_dir}")
    available = registry.available_skills(settings)
    if not available:
        raise ValueError(f"No available skills in {settings.skills_dir} under current settings")
    examples = load_skills_dataset(dataset, registry, settings=settings)
    if not examples:
        raise ValueError(f"No skill examples found in {dataset}")
    return _run_reanchor(
        settings,
        target="skills",
        database_url=database_url,
        examples=examples,
        predictor=skills.build_skill_selector(available),
        metric=skill_metric,
        output_path=output_path or DEFAULT_OUTPUTS["skills"],
        env_var="FLEET_SKILL_SELECTOR_PATH",
        train_ratio=train_ratio,
        seed=seed,
        num_threads=num_threads,
        allow_direct=allow_direct,
        log_dir=log_dir,
        require_cache=require_cache,
        force=force,
        progress=progress,
        role_models=role_models,
    )


def optimize_memory(
    settings: Settings,
    *,
    database_url: str,
    dataset: Path,
    output_path: Path | None = None,
    train_ratio: float = 0.7,
    seed: int = 0,
    num_threads: int | None = None,
    allow_direct: bool = False,
    log_dir: Path | None = None,
    require_cache: bool = True,
    force: bool = False,
    progress: _ProgressEmitter | None = None,
    role_models: Mapping[RoleName, RoleModelConfig] | None = None,
) -> OptimizationResult:
    """Calibrate MemoryGate thresholds and choice weights via 5-fold ReAnchor CV."""
    if progress is not None:
        progress.emit("loading_dataset", "Loading labeled Memory Gate dataset")
    examples = load_memory_dataset(dataset)
    if not examples:
        raise ValueError(f"No memory examples found in {dataset}")
    return _run_reanchor(
        settings,
        target="memory",
        database_url=database_url,
        examples=examples,
        predictor=dspy.Predict(agent.MemoryGate),
        metric=memory_metric,
        output_path=output_path or DEFAULT_OUTPUTS["memory"],
        env_var="FLEET_MEMORY_GATE_PATH",
        train_ratio=train_ratio,
        seed=seed,
        num_threads=num_threads,
        allow_direct=allow_direct,
        log_dir=log_dir,
        require_cache=require_cache,
        force=force,
        progress=progress,
        role_models=role_models,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet_rlm.optimize",
        description="Offline GEPA and ReAnchor optimizer for Fleet-RLM artifacts.",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=("rlm", "skills", "memory"),
        help="Optimization target to run.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Ephemeral Neon branch Postgres URL (refuses primary URLs unless --allow-direct).",
    )
    parser.add_argument(
        "--database-url-file",
        type=Path,
        default=None,
        help=(
            "Path to a private (0600) file containing the ephemeral Neon branch Postgres URL "
            "(keeps database credentials out of process argv)."
        ),
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=None,
        help=(
            "Path to a private (0600) JSON file mapping optional optimizer roles "
            "(root_lm, sub_lm, reflection_lm, judge_lm, decision_lm) to provider credentials."
        ),
    )
    parser.add_argument(
        "--allow-direct",
        action="store_true",
        help="Allow running against the configured database_url / database_direct_url.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Path to JSONL evaluation dataset (required for skills and memory).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output artifact path (defaults to artifacts/<target>.json).",
    )
    parser.add_argument(
        "--program",
        choices=("rlm", "flex"),
        default="rlm",
        help="Program module to compile for --target rlm.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=None,
        help="Train split ratio (default: 0.6 for rlm, 0.7 for skills/memory).",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=None,
        help=(
            "Validation split ratio for --target rlm "
            "(default: half of remaining examples after --train-ratio)."
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="Random seed for split/GEPA.")
    parser.add_argument(
        "--auto",
        choices=("light", "medium", "heavy"),
        default=None,
        help=(
            "Explicit GEPA auto budget override for --target rlm "
            "(mutually exclusive with --max-metric-calls)."
        ),
    )
    parser.add_argument(
        "--max-metric-calls",
        type=int,
        default=None,
        help=(
            "Strict GEPA metric-call budget for --target rlm "
            f"(default: {DEFAULT_MAX_METRIC_CALLS} when --auto is unset)."
        ),
    )
    parser.add_argument(
        "--reflection-lm",
        type=str,
        default=None,
        help="Model ID for GEPA reflection LM (default: settings.llm_model).",
    )
    parser.add_argument(
        "--judge-lm",
        type=str,
        default=None,
        help="Model ID for open-ended rubric judging (default: settings.llm_model).",
    )
    parser.add_argument(
        "--holdout-repeats",
        type=int,
        default=DEFAULT_HOLDOUT_REPEATS,
        help=(
            "Number of uncached holdout repeats for --target rlm "
            f"(default: {DEFAULT_HOLDOUT_REPEATS})."
        ),
    )
    parser.add_argument(
        "--disable-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Disable DSPy LM cache on RLM rollouts and holdout evaluations (default: enabled).",
    )
    parser.add_argument("--num-threads", type=int, default=None, help="Evaluation thread count.")
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum completed root runs to load from Neon when --dataset is omitted.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Save artifact even if holdout promotion / ReAnchor save gates do not pass.",
    )
    parser.add_argument(
        "--local-interpreter",
        action="store_true",
        help=(
            "Skip ephemeral Daytona sandbox provisioning for --target rlm --program rlm "
            "and use DSPy's local interpreter."
        ),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Optional directory for GEPA / ReAnchor logs and reports.",
    )
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=None,
        help="Optional JSONL path for structured live progress updates.",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="Optional JSON path for structured OptimizationResult output.",
    )
    return parser


def _read_database_url_file(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Database URL file not found: {path}")
    try:
        mode = path.stat().st_mode & 0o777
        if os.name == "posix" and mode & 0o077:
            _LOG.warning(
                "Database URL file %s has permissive mode %04o; expected 0600",
                path,
                mode,
            )
    except OSError:
        pass
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Database URL file is empty: {path}")
    return value


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.database_url) == bool(args.database_url_file):
        parser.error("Specify exactly one of --database-url and --database-url-file")
    if args.target in {"skills", "memory"} and args.dataset is None:
        parser.error(f"--dataset is required for --target {args.target}")
    if args.target == "rlm" and args.auto is not None and args.max_metric_calls is not None:
        parser.error("Specify at most one of --auto and --max-metric-calls")
    if args.target == "rlm" and args.dataset is None:
        print(
            "Warning: running --target rlm without --dataset uses unverified past DB runs "
            "(missing ground-truth verifiers, memories, skills, and prior_turns). "
            "Pass --dataset <path.jsonl> for reliable optimization.",
            file=sys.stderr,
        )

    progress = _ProgressEmitter(args.progress_file)
    resolved_settings = settings or Settings.model_validate({})
    extra_secrets = _collect_settings_secrets(resolved_settings)
    progress.add_secrets(extra_secrets)

    prev_sigterm = None
    sigterm_received = False
    if threading.current_thread() is threading.main_thread():
        prev_sigterm = signal.getsignal(signal.SIGTERM)

        def _handle_sigterm(_signum: int, _frame: Any) -> None:
            nonlocal sigterm_received
            if sigterm_received:
                return
            sigterm_received = True
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        database_url = (
            _read_database_url_file(args.database_url_file)
            if args.database_url_file is not None
            else str(args.database_url)
        )
        role_models: dict[RoleName, RoleModelConfig] | None = (
            load_role_credentials(args.credentials_file)
            if args.credentials_file is not None
            else None
        )
        extra_secrets = _collect_settings_secrets(
            resolved_settings, database_url=database_url, role_models=role_models
        )
        progress.add_secrets(extra_secrets)

        guard_database_url(database_url, resolved_settings, allow_direct=args.allow_direct)
        if args.target == "rlm":
            train_ratio = 0.6 if args.train_ratio is None else args.train_ratio
            result = optimize_rlm(
                resolved_settings,
                database_url=database_url,
                dataset=args.dataset,
                output_path=args.output,
                program_kind=args.program,
                train_ratio=train_ratio,
                val_ratio=args.val_ratio,
                seed=args.seed,
                auto=args.auto,
                max_metric_calls=args.max_metric_calls,
                reflection_lm=args.reflection_lm,
                judge_lm=args.judge_lm,
                holdout_repeats=args.holdout_repeats,
                disable_cache=args.disable_cache,
                num_threads=args.num_threads,
                limit=args.limit,
                force=args.force,
                allow_direct=args.allow_direct,
                local_interpreter=args.local_interpreter,
                log_dir=args.log_dir,
                progress=progress,
                role_models=role_models,
            )
        elif args.target == "skills":
            train_ratio = 0.7 if args.train_ratio is None else args.train_ratio
            result = optimize_skills(
                resolved_settings,
                database_url=database_url,
                dataset=args.dataset,
                output_path=args.output,
                train_ratio=train_ratio,
                seed=args.seed,
                num_threads=args.num_threads,
                allow_direct=args.allow_direct,
                log_dir=args.log_dir,
                force=args.force,
                progress=progress,
                role_models=role_models,
            )
        else:
            train_ratio = 0.7 if args.train_ratio is None else args.train_ratio
            result = optimize_memory(
                resolved_settings,
                database_url=database_url,
                dataset=args.dataset,
                output_path=args.output,
                train_ratio=train_ratio,
                seed=args.seed,
                num_threads=args.num_threads,
                allow_direct=args.allow_direct,
                log_dir=args.log_dir,
                force=args.force,
                progress=progress,
                role_models=role_models,
            )
    except DatabaseSafetyError:
        safe_err = _DATABASE_SAFETY_MESSAGE
        progress.emit("failed", "Database safety check failed", status="failed", message=safe_err)
        print(f"Error: {safe_err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        progress.emit("cancelled", "Optimization cancelled", status="cancelled")
        print("Optimization cancelled", file=sys.stderr)
        return 130
    except Exception as error:
        safe_err = _scrub_secrets(str(error) or type(error).__name__, extra_secrets)
        progress.emit("failed", "Optimization failed", status="failed", message=safe_err)
        raise RuntimeError(safe_err) from None
    finally:
        if prev_sigterm is not None and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, prev_sigterm)

    if args.report_file is not None:
        payload = asdict(result)
        payload["output_path"] = str(result.output_path)
        _write_json_atomic(args.report_file, payload)

    status = f"saved to {result.output_path}" if result.saved else "discarded (gate rejected)"
    val_segment = f" val={result.val_size}" if result.val_size else ""
    print(
        f"[{result.target}] train={result.train_size}{val_segment} holdout={result.holdout_size} "
        f"baseline={result.baseline_score} optimized={result.optimized_score} -> {status}"
    )
    if result.reminder:
        print(result.reminder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
