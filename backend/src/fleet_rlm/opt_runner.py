"""Optimization runner, artifact/dataset caching, storage dispatch, and Neon branch lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import weakref
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from uuid import UUID

import dspy
import httpx
from sqlalchemy import or_, select, tuple_

from . import agent, interpreter, llm_registry, opt_datasets
from . import db as store
from .config import Settings
from .skills import SkillRegistry, build_skill_selector
from .storage import ObjectStorage, StorageIntegrityError

_LOG = logging.getLogger(__name__)

type OptimizationTarget = opt_datasets.OptimizationTarget
type OptimizationBranchMode = Literal[
    "auto_neon", "configured_branch", "custom_branch", "direct_readonly"
]
type DatasetStorageBackend = Literal["builtin", "neon", "daytona_volume"]
type RoleName = Literal["root_lm", "sub_lm", "reflection_lm", "judge_lm", "decision_lm"]

OPT_LOG_MAX_BYTES = 32 * 1024
NEON_API_BASE = "https://console.neon.tech/api/v2"
CANCEL_SIGKILL_GRACE_SECONDS = 60.0
_MONITOR_POLL_INTERVAL_SECONDS = 0.5

_SUBPROCESS_ENV_PASSTHROUGH: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTPX_CA_BUNDLE",
    "NO_PROXY",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "UV_CACHE_DIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "DSPY_CACHEDIR",
    "LITELLM_LOCAL_MODEL_COST_MAP",
)


class OptimizationRuntimeLike(Protocol):
    @property
    def settings(self) -> Settings: ...
    @property
    def sessionmaker(self) -> store.SessionFactory: ...
    @property
    def daytona(self) -> Any: ...
    @property
    def volume_daytona(self) -> Any: ...
    @property
    def storage(self) -> ObjectStorage: ...
    @property
    def skills(self) -> SkillRegistry: ...
    @property
    def sub_lm(self) -> dspy.BaseLM: ...
    @property
    def decision_lm(self) -> dspy.BaseLM: ...
    @property
    def optimized_program_state(self) -> dict[str, Any] | None: ...
    @property
    def mlflow_experiment_id(self) -> str | None: ...
    @property
    def mlflow_optimization_experiment_id(self) -> str | None: ...
    @property
    def artifact_cache_dir(self) -> Path: ...
    @property
    def rlm_state_cache(self) -> dict[str, dict[str, Any]]: ...
    @property
    def optimization_tasks(self) -> dict[UUID, asyncio.Task[None]]: ...
    @property
    def optimization_procs(self) -> dict[UUID, Any]: ...
    @property
    def optimization_cancel_events(self) -> dict[UUID, asyncio.Event]: ...
    @property
    def shutdown_event(self) -> asyncio.Event: ...


@dataclass(frozen=True)
class ResolvedRoleCredential:
    """Resolved per-role LM credential for an optimization run (never persisted)."""

    role: RoleName
    model: str
    dialect: llm_registry.ProviderDialect | None = None
    api_mode: llm_registry.ProviderApiMode = "auto"
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = field(default=None, repr=False)
    provider_id: UUID | None = None
    provider_name: str | None = None
    timeout: float | None = None
    reasoning_effort: str | None = None

    def to_credentials_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "api_mode": self.api_mode,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.dialect is not None:
            payload["dialect"] = self.dialect
        if self.api_key is not None:
            payload["api_key"] = self.api_key
        if self.base_url is not None:
            payload["base_url"] = self.base_url
        if self.provider_name is not None:
            payload["provider_name"] = self.provider_name
        if self.timeout is not None:
            payload["timeout"] = self.timeout
        return payload

    def to_provenance(self) -> dict[str, Any]:
        """Non-secret metadata safe to persist in config_json."""
        return {
            **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}),
            "provider_id": str(self.provider_id) if self.provider_id is not None else None,
            "provider_name": self.provider_name,
            "dialect": self.dialect,
            "api_mode": self.api_mode,
            "model": self.model,
        }


@dataclass(frozen=True)
class JobRunSpec:
    """Normalized launch parameters passed from api.py to the subprocess supervisor."""

    target: OptimizationTarget
    program_kind: Literal["rlm", "flex"] = "rlm"
    train_ratio: float | None = None
    val_ratio: float | None = None
    seed: int = 0
    auto: Literal["light", "medium", "heavy"] | None = None
    max_metric_calls: int | None = None
    holdout_repeats: int = 3
    disable_cache: bool = True
    local_interpreter: bool = False
    num_threads: int | None = None
    limit: int = 200
    force: bool = False
    reflection_lm: str | None = None
    judge_lm: str | None = None
    role_credentials: Mapping[RoleName, ResolvedRoleCredential] = field(default_factory=dict)
    decision_provider_id: UUID | None = None


@dataclass(frozen=True)
class ActiveArtifacts:
    """Resolved per-turn settings, RLM state, and per-target ReAnchor decision LMs."""

    settings: Settings
    rlm_state: dict[str, Any] | None
    skills_decision_lm: dspy.BaseLM | None = None
    memory_decision_lm: dspy.BaseLM | None = None


@dataclass(frozen=True)
class BuiltinDatasetSummaryData:
    id: str
    name: str
    target: OptimizationTarget
    row_count: int
    size_bytes: int
    sha256: str
    summary_json: dict[str, Any]


@dataclass(frozen=True)
class OverviewTargetData:
    target: OptimizationTarget
    active_promotion: store.OptimizationPromotion | None
    previous_promotion: store.OptimizationPromotion | None
    latest_scored_job: store.OptimizationJob | None
    latest_job: store.OptimizationJob | None
    active_job: store.OptimizationJob | None


@dataclass(frozen=True)
class OverviewData:
    workspace_overrides: dict[str, Any]
    targets: dict[OptimizationTarget, OverviewTargetData]
    jobs_by_id: dict[UUID, store.OptimizationJob]
    provider_names: dict[UUID, str]


_LOOP_SHA_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, weakref.WeakValueDictionary[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()
_BUILTIN_SUMMARY_CACHE: dict[
    tuple[str, str, int, int, tuple[str, ...]], BuiltinDatasetSummaryData
] = {}


def _sha_lock(key: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _LOOP_SHA_LOCKS.get(loop)
    if locks is None:
        locks = weakref.WeakValueDictionary()
        _LOOP_SHA_LOCKS[loop] = locks
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_eval_datasets_dir(settings: Settings) -> Path:
    configured = settings.eval_datasets_dir
    if configured.is_absolute() or configured.is_dir():
        return configured.resolve()
    candidate = backend_root() / configured
    if candidate.is_dir():
        return candidate.resolve()
    packaged = Path(__file__).with_name("eval_datasets")
    if configured == Path("eval_datasets") and packaged.is_dir():
        return packaged
    return configured


def resolve_skills_dir(settings: Settings) -> Path:
    configured = settings.skills_dir
    if configured.is_absolute() or configured.is_dir():
        return configured.resolve()
    candidate = backend_root() / configured
    return candidate.resolve() if candidate.is_dir() else configured


def coerce_target(value: str) -> OptimizationTarget:
    if value == "rlm":
        return "rlm"
    if value == "skills":
        return "skills"
    if value == "memory":
        return "memory"
    raise ValueError(f"Unsupported optimization target: {value!r}")


def env_path_for_target(settings: Settings, target: OptimizationTarget) -> str | None:
    if target == "rlm":
        return str(settings.optimized_program_path) if settings.optimized_program_path else None
    if target == "skills":
        return str(settings.skill_selector_path) if settings.skill_selector_path else None
    return str(settings.memory_gate_path) if settings.memory_gate_path else None


def daytona_volume_enabled(settings: Settings, *, daytona: Any = True) -> bool:
    return bool(
        daytona is not None
        and (settings.daytona_service_key or settings.daytona_api_key).get_secret_value().strip()
        and settings.daytona_dataset_volume.strip()
    )


def optimization_experiment(settings: Settings) -> str:
    return f"{settings.mlflow_experiment}-optimization"


def mlflow_run_url(
    runtime: OptimizationRuntimeLike,
    experiment_name: str | None,
    run_id: str | None,
) -> str | None:
    """Return an MLflow UI run URL only when tracking URI and matching experiment ID are known."""
    tracking_uri = runtime.settings.mlflow_tracking_uri
    experiments = {
        runtime.settings.mlflow_experiment: runtime.mlflow_experiment_id,
        optimization_experiment(runtime.settings): runtime.mlflow_optimization_experiment_id,
    }
    exp_id = experiments.get(experiment_name or runtime.settings.mlflow_experiment)
    if not tracking_uri or not run_id or not exp_id:
        return None
    try:
        uri = urlsplit(tracking_uri)
        if uri.scheme not in {"http", "https"} or not uri.hostname or uri.username is not None:
            return None
    except ValueError:
        return None
    return urlunsplit(
        (
            uri.scheme,
            uri.netloc,
            uri.path.rstrip("/") + "/",
            "",
            f"/experiments/{quote(exp_id, safe='')}/runs/{quote(run_id, safe='')}",
        )
    )


# ---------------------------------------------------------------------------
# Private 0700 host SHA-256 cache (artifacts & datasets)
# ---------------------------------------------------------------------------


def ensure_private_cache_dir(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        with suppress(OSError):
            os.chmod(cache_dir, 0o700)
    return cache_dir


def _verify_cached_file(path: Path, expected_sha256: str, expected_size: int | None = None) -> bool:
    if not path.is_file():
        return False
    try:
        if expected_size is not None and path.stat().st_size != expected_size:
            return False
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest() == expected_sha256
    except OSError:
        return False


def _create_temp_path_in_cache(cache_dir: Path, prefix: str) -> Path:
    ensure_private_cache_dir(cache_dir)
    with NamedTemporaryFile(dir=cache_dir, prefix=prefix, suffix=".tmp", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    if os.name == "posix":
        with suppress(OSError):
            os.chmod(tmp_path, 0o600)
    return tmp_path


def _write_private_file_atomic(directory: Path, target: Path, data: bytes) -> Path:
    ensure_private_cache_dir(directory)
    tmp_path = _create_temp_path_in_cache(directory, f"{target.stem[:12]}-")
    try:
        tmp_path.write_bytes(data)
        if os.name == "posix":
            with suppress(OSError):
                os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    except BaseException:
        with suppress(OSError):
            tmp_path.unlink()
        raise
    return target


def _write_private_secret_file(path: Path, text_content: str) -> Path:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text_content)
    except BaseException:
        with suppress(OSError):
            path.unlink()
        raise
    if os.name == "posix":
        with suppress(OSError):
            os.chmod(path, 0o600)
    return path


async def materialize_artifact(
    runtime: OptimizationRuntimeLike,
    *,
    object_key: str,
    sha256: str,
    size_bytes: int,
) -> Path:
    """Download and SHA-256 verify an optimization artifact into the 0700 host cache."""
    async with _sha_lock(f"artifact:{sha256}"):
        cache_dir = await asyncio.to_thread(ensure_private_cache_dir, runtime.artifact_cache_dir)
        cached = cache_dir / f"{sha256}.json"
        if await asyncio.to_thread(_verify_cached_file, cached, sha256, size_bytes):
            return cached
        tmp_path = await asyncio.to_thread(_create_temp_path_in_cache, cache_dir, f"{sha256[:12]}-")
        try:
            await runtime.storage.download(object_key, tmp_path, sha256=sha256, size=size_bytes)
            if os.name == "posix":
                await asyncio.to_thread(os.chmod, tmp_path, 0o600)
            await asyncio.to_thread(os.replace, tmp_path, cached)
        except BaseException:
            with suppress(OSError):
                await asyncio.to_thread(tmp_path.unlink, True)
            raise
        return cached


async def cache_dataset_bytes(cache_dir: Path, sha256: str, raw_bytes: bytes) -> Path:
    """Store verified dataset bytes in the private 0700 host SHA-256 cache."""
    async with _sha_lock(f"dataset:{sha256}"):
        cached = cache_dir / f"{sha256}.jsonl"
        if await asyncio.to_thread(_verify_cached_file, cached, sha256, len(raw_bytes)):
            return cached
        return await asyncio.to_thread(_write_private_file_atomic, cache_dir, cached, raw_bytes)


async def evict_cached_dataset(cache_dir: Path, sha256: str) -> None:
    async with _sha_lock(f"dataset:{sha256}"):
        await asyncio.to_thread((cache_dir / f"{sha256}.jsonl").unlink, missing_ok=True)


async def materialize_dataset_bytes(
    runtime: OptimizationRuntimeLike,
    record: store.OptimizationDataset,
) -> tuple[bytes, Path]:
    """Read dataset bytes from host SHA cache, falling back to Neon S3 or Daytona Volume."""
    async with _sha_lock(f"dataset:{record.sha256}"):
        cache_dir = await asyncio.to_thread(ensure_private_cache_dir, runtime.artifact_cache_dir)
        cached = cache_dir / f"{record.sha256}.jsonl"
        if await asyncio.to_thread(_verify_cached_file, cached, record.sha256, record.size_bytes):
            raw = await asyncio.to_thread(cached.read_bytes)
            return raw, cached

        if record.storage_backend == "daytona_volume":
            raw = await interpreter.read_volume_dataset(
                runtime.settings.model_copy(
                    update={"daytona_dataset_volume": record.storage_volume}
                )
                if record.storage_volume
                else runtime.settings,
                record.object_key,
                daytona=runtime.volume_daytona or runtime.daytona,
            )
            digest = await asyncio.to_thread(lambda: hashlib.sha256(raw).hexdigest())
            if len(raw) != record.size_bytes or digest != record.sha256:
                raise StorageIntegrityError("Daytona volume dataset integrity check failed")
            await asyncio.to_thread(_write_private_file_atomic, cache_dir, cached, raw)
            return raw, cached

        tmp_path = await asyncio.to_thread(
            _create_temp_path_in_cache, cache_dir, f"ds-{record.sha256[:12]}-"
        )
        try:
            await runtime.storage.download(
                record.object_key,
                tmp_path,
                sha256=record.sha256,
                size=record.size_bytes,
            )
            if os.name == "posix":
                await asyncio.to_thread(os.chmod, tmp_path, 0o600)
            await asyncio.to_thread(os.replace, tmp_path, cached)
            raw = await asyncio.to_thread(cached.read_bytes)
        except BaseException:
            with suppress(OSError):
                await asyncio.to_thread(tmp_path.unlink, True)
            raise
        return raw, cached


# ---------------------------------------------------------------------------
# Dataset storage dispatch & built-in summary cache
# ---------------------------------------------------------------------------


def builtin_dataset_path(
    settings: Settings, dataset_id: str
) -> tuple[OptimizationTarget, Path] | None:
    eval_dir = resolve_eval_datasets_dir(settings)
    for target, (builtin_id, filename) in opt_datasets.BUILTIN_DATASETS.items():
        if dataset_id == builtin_id:
            return target, eval_dir / filename
    return None


def summarize_builtin_dataset(
    target: OptimizationTarget,
    dataset_id: str,
    path: Path,
    available_skills: Collection[str],
) -> BuiltinDatasetSummaryData | None:
    """Summarize a built-in dataset file, caching by (path, target, mtime_ns, size, skills)."""
    if not path.is_file():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    skills_key = tuple(sorted(available_skills))
    cache_key = (str(path.resolve()), target, stat.st_mtime_ns, stat.st_size, skills_key)
    cached = _BUILTIN_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows, summary = opt_datasets.validate_and_summarize_jsonl(
        raw,
        target,
        available_skills,
        strict_secrets=False,
        enforce_min_rows=False,
    )
    result = BuiltinDatasetSummaryData(
        id=dataset_id,
        name=path.name,
        target=target,
        row_count=len(rows),
        size_bytes=len(raw),
        sha256=digest,
        summary_json=summary,
    )
    if len(_BUILTIN_SUMMARY_CACHE) >= 32:
        _BUILTIN_SUMMARY_CACHE.clear()
    _BUILTIN_SUMMARY_CACHE[cache_key] = result
    return result


async def store_dataset_bytes(
    runtime: OptimizationRuntimeLike,
    *,
    dataset_id: UUID,
    raw_bytes: bytes,
    storage_backend: store.OptimizationStorageBackend,
) -> tuple[str, str | None, str, int]:
    """Write validated dataset bytes to Neon S3 or Daytona Volume and warm the host SHA cache.

    Returns ``(object_key, storage_volume, sha256, size_bytes)``.
    """
    if storage_backend == "daytona_volume":
        rel_path = f"datasets/{dataset_id}.jsonl"
        storage_volume, object_key = await interpreter.write_volume_dataset(
            runtime.settings,
            rel_path,
            raw_bytes,
            daytona=runtime.volume_daytona or runtime.daytona,
        )
        digest = await asyncio.to_thread(lambda: hashlib.sha256(raw_bytes).hexdigest())
        size_bytes = len(raw_bytes)
    else:
        object_key = runtime.storage.optimization_dataset_key(dataset_id)
        storage_volume = None

        def _stage_file(temp_dir: str) -> Path:
            local = Path(temp_dir) / "dataset.jsonl"
            local.write_bytes(raw_bytes)
            return local

        with TemporaryDirectory(prefix="fleet-opt-ds-") as temporary:
            local_path = await asyncio.to_thread(_stage_file, temporary)
            digest, size_bytes = await runtime.storage.put_object(object_key, local_path)

    with suppress(OSError):
        await cache_dataset_bytes(runtime.artifact_cache_dir, digest, raw_bytes)
    return object_key, storage_volume, digest, size_bytes


async def delete_dataset_from_backend(
    runtime: OptimizationRuntimeLike,
    *,
    storage_backend: str,
    object_key: str,
    storage_volume: str | None = None,
) -> None:
    if storage_backend == "daytona_volume":
        await interpreter.delete_volume_dataset(
            runtime.settings.model_copy(update={"daytona_dataset_volume": storage_volume})
            if storage_volume
            else runtime.settings,
            object_key,
            daytona=runtime.volume_daytona or runtime.daytona,
        )
    else:
        await runtime.storage.delete_object(object_key)


# ---------------------------------------------------------------------------
# Per-turn active artifact & per-target ReAnchor decision LM resolution
# ---------------------------------------------------------------------------


def _load_and_validate_rlm_state(
    settings: Settings,
    sub_lm: dspy.BaseLM,
    path: Path,
) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("Promoted RLM artifact must be a JSON object")
    agent.build_program(settings, [], sub_lm=sub_lm, optimized_state=loaded)
    return loaded


def _validate_artifact_sync(
    settings: Settings,
    sub_lm: dspy.BaseLM,
    available_skills: tuple[Any, ...],
    target: OptimizationTarget,
    path: Path,
) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Artifact file must contain a JSON object")
    if target == "rlm":
        agent.build_program(settings, [], sub_lm=sub_lm, optimized_state=raw)
    elif target == "skills":
        if available_skills:
            build_skill_selector(available_skills, state_path=path)
    else:
        predictor = dspy.Predict(agent.MemoryGate)
        predictor.load(path)


async def validate_artifact_before_promote(
    runtime: OptimizationRuntimeLike,
    *,
    target: OptimizationTarget,
    object_key: str,
    sha256: str,
    size_bytes: int,
    available_skills: tuple[Any, ...],
) -> Path:
    """Materialize and validate an artifact off the event loop before live promotion."""
    path = await materialize_artifact(
        runtime,
        object_key=object_key,
        sha256=sha256,
        size_bytes=size_bytes,
    )
    await asyncio.to_thread(
        _validate_artifact_sync,
        runtime.settings,
        runtime.sub_lm,
        available_skills,
        target,
        path,
    )
    return path


async def _resolve_promotion_decision_lm(
    runtime: OptimizationRuntimeLike,
    promo: store.OptimizationPromotion,
    effective: Settings,
) -> dspy.BaseLM | None:
    """Resolve a ReAnchor promotion's pinned (decision_provider_id, resolved_decision_model)."""
    if promo.decision_provider_id is not None:
        provider = await store.get_llm_provider(runtime.sessionmaker, promo.decision_provider_id)
        if provider is not None:
            api_key: str | None = None
            if provider.api_key_ciphertext:
                api_key = llm_registry.decrypt_secret(provider.api_key_ciphertext, runtime.settings)
            model_id = (
                (promo.resolved_decision_model or "").strip()
                or (provider.default_model or "").strip()
                or effective.decision_model
            )
            return llm_registry.build_dspy_lm(
                cast(llm_registry.ProviderDialect, provider.dialect),
                cast(llm_registry.ProviderApiMode, provider.api_mode),
                model_id,
                api_key=api_key,
                base_url=provider.base_url,
            )
        _LOG.warning(
            "Active %s promotion %s references missing provider %s; falling back to model ID",
            promo.target,
            promo.id,
            promo.decision_provider_id,
        )

    pinned_model = (promo.resolved_decision_model or "").strip()
    if not pinned_model or pinned_model == effective.decision_model:
        return None
    if pinned_model == runtime.settings.decision_model:
        return runtime.decision_lm
    return dspy.LM(pinned_model)


async def resolve_active_artifacts(
    runtime: OptimizationRuntimeLike,
    effective: Settings,
) -> ActiveArtifacts:
    """Overlay live promoted artifacts & per-target ReAnchor LMs without blocking or crashing."""
    try:
        promotions = await store.list_active_optimization_promotions(runtime.sessionmaker)
    except Exception as error:
        _LOG.warning(
            "Could not load active optimization promotions (%s); using env/default artifacts",
            type(error).__name__,
        )
        return ActiveArtifacts(settings=effective, rlm_state=runtime.optimized_program_state)

    updated = effective
    rlm_state = runtime.optimized_program_state
    skills_decision_lm: dspy.BaseLM | None = None
    memory_decision_lm: dspy.BaseLM | None = None

    if (skills_promo := promotions.get("skills")) is not None:
        try:
            path = await materialize_artifact(
                runtime,
                object_key=skills_promo.artifact_object_key,
                sha256=skills_promo.artifact_sha256,
                size_bytes=skills_promo.artifact_size_bytes,
            )
            skills_decision_lm = await _resolve_promotion_decision_lm(
                runtime, skills_promo, updated
            )
            updated = updated.model_copy(update={"skill_selector_path": path})
        except Exception as error:
            _LOG.warning(
                "Could not materialize promoted skills artifact %s (%s); "
                "falling back to env/default",
                skills_promo.artifact_sha256[:12],
                type(error).__name__,
            )

    if (memory_promo := promotions.get("memory")) is not None:
        try:
            path = await materialize_artifact(
                runtime,
                object_key=memory_promo.artifact_object_key,
                sha256=memory_promo.artifact_sha256,
                size_bytes=memory_promo.artifact_size_bytes,
            )
            memory_decision_lm = await _resolve_promotion_decision_lm(
                runtime, memory_promo, updated
            )
            updated = updated.model_copy(update={"memory_gate_path": path})
        except Exception as error:
            _LOG.warning(
                "Could not materialize promoted memory artifact %s (%s); "
                "falling back to env/default",
                memory_promo.artifact_sha256[:12],
                type(error).__name__,
            )

    if (rlm_promo := promotions.get("rlm")) is not None:
        if rlm_promo.program_kind == "flex":
            _LOG.warning(
                "Skipping active RLM promotion %s with unsupported program_kind='flex'",
                rlm_promo.id,
            )
        else:
            try:
                cached_state = runtime.rlm_state_cache.get(rlm_promo.artifact_sha256)
                if cached_state is None:
                    path = await materialize_artifact(
                        runtime,
                        object_key=rlm_promo.artifact_object_key,
                        sha256=rlm_promo.artifact_sha256,
                        size_bytes=rlm_promo.artifact_size_bytes,
                    )
                    loaded = await asyncio.to_thread(
                        _load_and_validate_rlm_state, updated, runtime.sub_lm, path
                    )
                    if len(runtime.rlm_state_cache) >= 8:
                        runtime.rlm_state_cache.pop(next(iter(runtime.rlm_state_cache)))
                    runtime.rlm_state_cache[rlm_promo.artifact_sha256] = loaded
                    cached_state = loaded
                rlm_state = cached_state
            except Exception as error:
                _LOG.warning(
                    "Could not materialize promoted RLM artifact %s (%s); "
                    "falling back to env/default",
                    rlm_promo.artifact_sha256[:12],
                    type(error).__name__,
                )

    return ActiveArtifacts(
        settings=updated,
        rlm_state=rlm_state,
        skills_decision_lm=skills_decision_lm,
        memory_decision_lm=memory_decision_lm,
    )


# ---------------------------------------------------------------------------
# Single-session overview query (no N+1, no 100-row cutoff)
# ---------------------------------------------------------------------------


async def load_overview_data(sessionmaker: store.SessionFactory) -> OverviewData:
    """Fetch workspace overrides, active/previous promotions, latest & active jobs in 1 session."""
    async with sessionmaker() as db:
        settings_row = await db.get(store.WorkspaceSettings, 1)
        overrides = (
            dict(settings_row.overrides_json or {})
            if settings_row is not None and settings_row.overrides_json
            else {}
        )

        active_promos = list(
            await db.scalars(
                select(store.OptimizationPromotion).where(
                    store.OptimizationPromotion.active.is_(True)
                )
            )
        )
        active_by_target: dict[str, store.OptimizationPromotion] = {
            row.target: row for row in active_promos
        }

        previous_by_target: dict[str, store.OptimizationPromotion] = {}
        if active_by_target:
            prev_conditions = [
                (store.OptimizationPromotion.target == target)
                & store.OptimizationPromotion.active.is_(False)
                & (
                    tuple_(
                        store.OptimizationPromotion.promoted_at,
                        store.OptimizationPromotion.id,
                    )
                    < tuple_(promo.promoted_at, promo.id)
                )
                for target, promo in active_by_target.items()
            ]
            prev_rows = list(
                await db.scalars(
                    select(store.OptimizationPromotion)
                    .distinct(store.OptimizationPromotion.target)
                    .where(or_(*prev_conditions))
                    .order_by(
                        store.OptimizationPromotion.target,
                        store.OptimizationPromotion.promoted_at.desc(),
                        store.OptimizationPromotion.id.desc(),
                    )
                )
            )
            previous_by_target = {row.target: row for row in prev_rows}

        job = store.OptimizationJob
        latest_query = (
            select(job)
            .distinct(job.target)
            .order_by(job.target, job.created_at.desc(), job.id.desc())
        )
        latest_rows = list(await db.scalars(latest_query))
        latest_by_target = {row.target: row for row in latest_rows}
        scored_rows = await db.scalars(
            latest_query.where(job.status == "completed", job.optimized_score.is_not(None))
        )
        scored_by_target = {row.target: row for row in scored_rows}

        active_job_rows = list(
            await db.scalars(
                select(store.OptimizationJob).where(
                    store.OptimizationJob.status.in_(("queued", "running"))
                )
            )
        )
        active_job_by_target: dict[str, store.OptimizationJob] = {
            row.target: row for row in active_job_rows
        }

        jobs_by_id: dict[UUID, store.OptimizationJob] = {
            job.id: job for job in (*latest_rows, *active_job_rows)
        }
        promo_job_ids = {
            promo.job_id
            for promo in (*active_by_target.values(), *previous_by_target.values())
            if promo.job_id not in jobs_by_id
        }
        if promo_job_ids:
            extra_jobs = list(
                await db.scalars(
                    select(store.OptimizationJob).where(store.OptimizationJob.id.in_(promo_job_ids))
                )
            )
            for job in extra_jobs:
                jobs_by_id[job.id] = job

        provider_ids = {
            promo.decision_provider_id
            for promo in (*active_by_target.values(), *previous_by_target.values())
            if promo.decision_provider_id is not None
        }
        provider_names: dict[UUID, str] = {}
        if provider_ids:
            rows = (
                await db.execute(
                    select(store.LLMProvider.id, store.LLMProvider.name).where(
                        store.LLMProvider.id.in_(provider_ids)
                    )
                )
            ).all()
            provider_names = {pid: name for pid, name in rows}

    targets: dict[OptimizationTarget, OverviewTargetData] = {}
    for target in opt_datasets.VALID_OPT_TARGETS:
        targets[target] = OverviewTargetData(
            target=target,
            active_promotion=active_by_target.get(target),
            previous_promotion=previous_by_target.get(target),
            latest_job=latest_by_target.get(target),
            latest_scored_job=scored_by_target.get(target),
            active_job=active_job_by_target.get(target),
        )
    return OverviewData(
        workspace_overrides=overrides,
        targets=targets,
        jobs_by_id=jobs_by_id,
        provider_names=provider_names,
    )


# ---------------------------------------------------------------------------
# Ephemeral Neon branch lifecycle
# ---------------------------------------------------------------------------


def _neon_db_and_role(settings: Settings) -> tuple[str, str]:
    for raw_url in (settings.database_url, settings.database_direct_url):
        if not raw_url:
            continue
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            continue
        db_name = unquote(parsed.path.lstrip("/")) if parsed.path else ""
        role_name = unquote(parsed.username) if parsed.username else ""
        if db_name or role_name:
            return db_name or "neondb", role_name or "neondb_owner"
    return "neondb", "neondb_owner"


async def create_ephemeral_neon_branch(
    settings: Settings,
    target: OptimizationTarget,
    job_id: UUID,
) -> tuple[str, str]:
    """Create a temporary Neon branch and return (branch_id, connection_uri)."""
    api_key = settings.neon_api_key.get_secret_value() if settings.neon_api_key else ""
    project_id = (settings.neon_project_id or "").strip()
    if not api_key or not project_id:
        raise RuntimeError("Auto ephemeral Neon branch is not configured")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    branch_payload: dict[str, Any] = {
        "branch": {"name": f"opt-ui-{target}-{job_id.hex[:8]}"},
        "endpoints": [{"type": "read_write"}],
    }
    if settings.neon_parent_branch_id:
        branch_payload["branch"]["parent_id"] = settings.neon_parent_branch_id

    db_name, role_name = _neon_db_and_role(settings)
    proj_quoted = quote(project_id, safe="")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{NEON_API_BASE}/projects/{proj_quoted}/branches",
            headers=headers,
            json=branch_payload,
        )
        resp.raise_for_status()
        created = resp.json()
        branch = created.get("branch") or {}
        branch_id = str(branch.get("id") or "")
        if not branch_id:
            raise RuntimeError("Neon REST API did not return a branch ID")
        try:
            uri_resp = await client.get(
                f"{NEON_API_BASE}/projects/{proj_quoted}/connection_uri",
                headers=headers,
                params={
                    "branch_id": branch_id,
                    "database_name": db_name,
                    "role_name": role_name,
                },
            )
            uri_resp.raise_for_status()
            uri = str((uri_resp.json() or {}).get("uri") or "")
            if not uri:
                raise RuntimeError("Neon REST API did not return a connection URI")
            return branch_id, uri
        except BaseException:
            await delete_ephemeral_neon_branch(settings, branch_id)
            raise


async def delete_ephemeral_neon_branch(settings: Settings, branch_id: str) -> None:
    """Delete an ephemeral Neon branch, verifying HTTP status and logging loudly on failure."""
    api_key = settings.neon_api_key.get_secret_value() if settings.neon_api_key else ""
    project_id = (settings.neon_project_id or "").strip()
    if not api_key or not project_id or not branch_id:
        return
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            proj = quote(project_id, safe="")
            branch = quote(branch_id, safe="")
            resp = await client.delete(
                f"{NEON_API_BASE}/projects/{proj}/branches/{branch}",
                headers=headers,
            )
            if resp.status_code != 404:
                resp.raise_for_status()
    except httpx.HTTPStatusError as error:
        _LOG.error(
            "Failed to delete ephemeral Neon branch %s (HTTP %s)",
            branch_id,
            error.response.status_code,
        )
    except Exception as error:
        _LOG.error(
            "Failed to delete ephemeral Neon branch %s (%s)",
            branch_id,
            type(error).__name__,
        )


# ---------------------------------------------------------------------------
# Subprocess environment, secret collection, logs, progress, supervision
# ---------------------------------------------------------------------------


def _credential_free_pg_url(raw_url: str) -> str:
    """Return a psycopg URL retaining host/port/db for guard_database_url without credentials."""
    try:
        parsed = urlsplit(opt_datasets.normalize_pg_url(raw_url))
    except ValueError:
        return "postgresql+psycopg://redacted@blocked.invalid/blocked"
    host = parsed.hostname or "blocked.invalid"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = parsed.path or "/neondb"
    return f"postgresql+psycopg://redacted@{host}{port}{path}"


def build_subprocess_env(
    effective: Settings,
    *,
    target: OptimizationTarget,
    program_kind: Literal["rlm", "flex"],
    local_interpreter: bool,
    decision_credential: ResolvedRoleCredential | None = None,
) -> dict[str, str]:
    """Build an explicit allowlisted environment for the optimizer subprocess."""
    env: dict[str, str] = {}
    for key in _SUBPROCESS_ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value

    src_dir = str(backend_root() / "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_parts = [part for part in existing_pythonpath.split(os.pathsep) if part]
    if src_dir not in pythonpath_parts:
        pythonpath_parts.insert(0, src_dir)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # Credential-free primary endpoints so Settings loads and guard_database_url remains active
    # without leaking primary Postgres credentials to the subprocess.
    env["FLEET_DATABASE_URL"] = _credential_free_pg_url(effective.database_url)
    env["FLEET_DATABASE_DIRECT_URL"] = _credential_free_pg_url(effective.database_direct_url)

    # Workspace LLM / Decision model config
    env["FLEET_LLM_MODEL"] = effective.llm_model
    env["FLEET_SUB_LLM_MODEL"] = effective.sub_llm_model
    env["FLEET_LLM_API_KEY"] = (
        effective.llm_api_key.get_secret_value() if target == "rlm" else "disabled"
    )
    if effective.llm_base_url:
        env["FLEET_LLM_BASE_URL"] = effective.llm_base_url
    env["FLEET_DECISION_MODEL"] = effective.decision_model
    if typesafe_key := os.environ.get("TYPESAFE_API_KEY"):
        if (
            target == "rlm"
            or decision_credential is None
            or decision_credential.provider_id is None
        ):
            env["TYPESAFE_API_KEY"] = typesafe_key
    if target in {"rlm", "skills"} and effective.tavily_api_key:
        # Offline web tools replay fixtures. Only skill availability depends on this setting.
        if effective.tavily_api_key.get_secret_value():
            env["TAVILY_API_KEY"] = "available"

    # Workspace RLM / history / timeout tuning overrides & local paths
    env["FLEET_RLM_MAX_ITERS"] = str(effective.rlm_max_iters)
    env["FLEET_RLM_MAX_LLM_CALLS"] = str(effective.rlm_max_llm_calls)
    env["FLEET_RLM_MAX_OUTPUT_CHARS"] = str(effective.rlm_max_output_chars)
    env["FLEET_RLM_MAX_DEPTH"] = str(effective.rlm_max_depth)
    env["FLEET_HISTORY_MAX_TURNS"] = str(effective.history_max_turns)
    env["FLEET_HISTORY_MAX_CHARS"] = str(effective.history_max_chars)
    env["FLEET_DAYTONA_EXEC_TIMEOUT_SECONDS"] = str(effective.daytona_exec_timeout_seconds)
    env["FLEET_SKILLS_DIR"] = str(resolve_skills_dir(effective))
    env["FLEET_EVAL_DATASETS_DIR"] = str(resolve_eval_datasets_dir(effective))

    # MLflow tracking configuration
    if effective.mlflow_tracking_uri:
        env["FLEET_MLFLOW_TRACKING_URI"] = effective.mlflow_tracking_uri
    env["FLEET_MLFLOW_EXPERIMENT"] = optimization_experiment(effective)

    # Daytona credentials ONLY for non-local dspy.RLM runs
    needs_daytona = target == "rlm" and program_kind == "rlm" and not local_interpreter
    if needs_daytona:
        env["FLEET_DAYTONA_API_KEY"] = effective.daytona_api_key.get_secret_value()
        env["FLEET_DAYTONA_IMAGE"] = effective.daytona_image
        if effective.daytona_region:
            env["FLEET_DAYTONA_REGION"] = effective.daytona_region
        env["FLEET_DAYTONA_IDLE_TIMEOUT_MINUTES"] = str(effective.daytona_idle_timeout_minutes)
        env["FLEET_DAYTONA_MAX_LIFETIME_MINUTES"] = str(effective.daytona_max_lifetime_minutes)
        env["FLEET_DAYTONA_NETWORK_ALLOW_DOMAINS"] = json.dumps(
            effective.daytona_network_allow_domains
        )
        env["FLEET_DAYTONA_OPTIMIZE_SPOT"] = "true" if effective.daytona_optimize_spot else "false"
        env["FLEET_DAYTONA_OTEL_ENABLED"] = "true" if effective.daytona_otel_enabled else "false"
    else:
        # Satisfy required Settings field without exposing the host Daytona key.
        env["FLEET_DAYTONA_API_KEY"] = "disabled"

    return env


def collect_extra_secrets(
    settings: Settings,
    *,
    db_urls: Sequence[str | None] = (),
    role_credentials: Mapping[RoleName, ResolvedRoleCredential] | None = None,
) -> list[str]:
    """Collect host, DB, and per-role secrets so logs/reports/errors never leak them."""
    secrets = opt_datasets.database_url_secrets(settings, *db_urls)
    for attr in (
        "api_bearer_token",
        "daytona_api_key",
        "daytona_service_key",
        "tavily_api_key",
        "s3_access_key_id",
        "s3_secret_access_key",
        "llm_api_key",
        "neon_api_key",
        "credentials_encryption_key",
    ):
        val = getattr(settings, attr, None)
        if val is not None and hasattr(val, "get_secret_value"):
            secret_text = str(val.get_secret_value() or "")
            if secret_text:
                secrets.append(secret_text)
    if typesafe_key := os.environ.get("TYPESAFE_API_KEY"):
        secrets.append(typesafe_key)
    for cred in (role_credentials or {}).values():
        if cred.api_key:
            secrets.append(cred.api_key)
        if cred.base_url:
            with suppress(ValueError):
                parsed = urlsplit(cred.base_url)
                if parsed.password:
                    secrets.append(parsed.password)
    return list(dict.fromkeys(secret for secret in secrets if len(secret) >= 4))


def append_log_tail(current: str, chunk: str) -> str:
    merged = current + chunk
    encoded = merged.encode("utf-8", errors="replace")
    if len(encoded) <= OPT_LOG_MAX_BYTES:
        return merged
    return encoded[-OPT_LOG_MAX_BYTES:].decode("utf-8", errors="ignore")


async def _read_log_line(stream: asyncio.StreamReader) -> bytes:
    # Keep draining oversized lines to avoid blocking the child on a full pipe.
    # Drop the whole line so a partial credential cannot reach the log.
    oversized = False
    while True:
        try:
            line = await stream.readuntil(b"\n")
        except asyncio.LimitOverrunError as error:
            await stream.readexactly(error.consumed)
            oversized = True
            continue
        except asyncio.IncompleteReadError as error:
            line = error.partial
        return b"[Oversized log line omitted]\n" if oversized else line


def read_latest_progress(progress_path: Path, offset: int) -> tuple[int, dict[str, Any] | None]:
    """Read only complete newline-terminated JSONL records so partial writes are never lost."""
    try:
        with progress_path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read()
    except OSError:
        return offset, None
    last_newline = raw.rfind(b"\n")
    if last_newline < 0:
        return offset, None
    complete_bytes = raw[: last_newline + 1]
    new_offset = offset + last_newline + 1
    latest: dict[str, Any] | None = None
    for line in complete_bytes.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        with suppress(json.JSONDecodeError):
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                latest = parsed
    return new_offset, latest


async def _terminate_and_reap_proc(proc: Any, *, grace_seconds: float) -> None:
    if proc is None or proc.returncode is not None:
        return
    with suppress(ProcessLookupError, OSError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except TimeoutError:
        with suppress(ProcessLookupError, OSError):
            proc.kill()
        with suppress(Exception):
            await proc.wait()


def _optimizer_arguments(
    spec: JobRunSpec, dataset_arg: Path | None, branch_mode: OptimizationBranchMode
) -> list[str]:
    cmd: list[str] = []
    if dataset_arg is not None:
        cmd.extend(["--dataset", str(dataset_arg)])
    if branch_mode == "direct_readonly":
        cmd.append("--allow-direct")
    if spec.train_ratio is not None:
        cmd.extend(["--train-ratio", str(spec.train_ratio)])
    if spec.force:
        cmd.append("--force")
    if spec.num_threads is not None:
        cmd.extend(["--num-threads", str(spec.num_threads)])

    if spec.target == "rlm":
        cmd.extend(_rlm_arguments(spec, dataset_arg))
    return cmd


def _rlm_arguments(spec: JobRunSpec, dataset_arg: Path | None) -> list[str]:
    cmd: list[str] = []
    cmd.extend(["--program", spec.program_kind])
    if spec.val_ratio is not None:
        cmd.extend(["--val-ratio", str(spec.val_ratio)])
    if spec.auto is not None:
        cmd.extend(["--auto", spec.auto])
    elif spec.max_metric_calls is not None:
        cmd.extend(["--max-metric-calls", str(spec.max_metric_calls)])
    cmd.extend(["--holdout-repeats", str(spec.holdout_repeats)])
    if not spec.disable_cache:
        cmd.append("--no-disable-cache")
    if spec.local_interpreter:
        cmd.append("--local-interpreter")
    if spec.reflection_lm and "reflection_lm" not in spec.role_credentials:
        cmd.extend(["--reflection-lm", spec.reflection_lm])
    if spec.judge_lm and "judge_lm" not in spec.role_credentials:
        cmd.extend(["--judge-lm", spec.judge_lm])
    if dataset_arg is None:
        cmd.extend(["--limit", str(spec.limit)])
    return cmd


async def _poll_job_cancellation(
    sessionmaker: store.SessionFactory, job_id: UUID, event: asyncio.Event
) -> bool:
    if event.is_set():
        return True
    try:
        if await store.is_optimization_job_cancel_requested(sessionmaker, job_id):
            event.set()
    except Exception as error:
        _LOG.warning(
            "Transient DB error polling cancel flag for job %s (%s)", job_id, type(error).__name__
        )
    return event.is_set()


def _unsuccessful_exit(
    cancelled: bool, returncode: int | None, log_tail: str
) -> tuple[Literal["cancelled", "failed"], str] | None:
    if cancelled or returncode == 130:
        return "cancelled", "Cancelled by user"
    if returncode == 0:
        return None
    message = next(
        (line.strip() for line in reversed(log_tail.splitlines()) if line.strip()),
        f"Optimizer exited with code {returncode}",
    )
    return "failed", message[:500]


def _remove_secret_files(*paths: Path | None) -> None:
    for path in paths:
        if path is not None:
            with suppress(OSError):
                path.unlink(missing_ok=True)


async def _stop_monitor_task(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


async def supervise_optimization_job(
    runtime: OptimizationRuntimeLike,
    job_id: UUID,
    *,
    spec: JobRunSpec,
    branch_mode: OptimizationBranchMode,
    effective: Settings,
    dataset_source: store.OptimizationDatasetSource,
    builtin_dataset_path: Path | None,
    custom_dataset: store.OptimizationDataset | None,
    custom_database_url: str | None,
) -> None:
    """Supervise an isolated optimization subprocess with hardened env, files, and cleanup."""
    cancel_event = runtime.optimization_cancel_events.setdefault(job_id, asyncio.Event())
    ephemeral_branch_id: str | None = None
    log_tail = ""
    extra_secrets = collect_extra_secrets(
        runtime.settings,
        db_urls=(custom_database_url,),
        role_credentials=spec.role_credentials,
    )
    progress_state: dict[str, Any] = {
        "stage": "queued",
        "label": "Preparing optimization runner",
        "completed": None,
        "total": None,
    }
    cancelled = False
    proc: Any = None
    db_url_file: Path | None = None
    credentials_file: Path | None = None

    async def _check_cancel_requested() -> bool:
        if cancel_event.is_set():
            return True
        if await store.is_optimization_job_cancel_requested(runtime.sessionmaker, job_id):
            cancel_event.set()
            return True
        return False

    try:
        # Check cancel BEFORE Neon branch provisioning so queued cancels exit cleanly.
        if await _check_cancel_requested():
            await store.finish_optimization_job(
                runtime.sessionmaker,
                job_id,
                status="cancelled",
                stage="cancelled",
                progress_json={**progress_state, "stage": "cancelled", "label": "Cancelled"},
                log_tail=log_tail,
                error="Cancelled by user",
            )
            return

        if branch_mode == "auto_neon":
            progress_state = {
                "stage": "provisioning_branch",
                "label": "Provisioning ephemeral Neon branch",
            }
            await store.update_optimization_job_progress(
                runtime.sessionmaker,
                job_id,
                status="running",
                stage="provisioning_branch",
                progress_json=progress_state,
                started_at=datetime.now(UTC),
            )
            ephemeral_branch_id, db_url = await create_ephemeral_neon_branch(
                runtime.settings, spec.target, job_id
            )
            extra_secrets = collect_extra_secrets(
                runtime.settings,
                db_urls=(custom_database_url, db_url),
                role_credentials=spec.role_credentials,
            )
        elif branch_mode == "configured_branch":
            opt_db = runtime.settings.optimization_database_url
            assert opt_db is not None
            db_url = opt_db.get_secret_value()
            extra_secrets = collect_extra_secrets(
                runtime.settings,
                db_urls=(custom_database_url, db_url),
                role_credentials=spec.role_credentials,
            )
        elif branch_mode == "custom_branch":
            assert custom_database_url is not None
            db_url = custom_database_url
        else:
            db_url = runtime.settings.database_direct_url

        # Re-check cancel BEFORE subprocess spawn (also tears down any ephemeral branch in finally).
        if await _check_cancel_requested():
            await store.finish_optimization_job(
                runtime.sessionmaker,
                job_id,
                status="cancelled",
                stage="cancelled",
                progress_json={**progress_state, "stage": "cancelled", "label": "Cancelled"},
                log_tail=log_tail,
                error="Cancelled by user",
            )
            return

        await store.update_optimization_job_progress(
            runtime.sessionmaker,
            job_id,
            status="running",
            stage="starting",
            progress_json={"stage": "starting", "label": "Launching isolated optimizer"},
            started_at=datetime.now(UTC),
        )

        with TemporaryDirectory(prefix=f"fleet-opt-job-{job_id.hex[:8]}-") as temporary:
            work_dir = Path(temporary)
            if os.name == "posix":
                with suppress(OSError):
                    os.chmod(work_dir, 0o700)
            progress_path = work_dir / "progress.jsonl"
            report_path = work_dir / "report.json"
            artifact_path = work_dir / f"{spec.target}.json"
            log_dir = work_dir / "logs"

            dataset_arg: Path | None = None
            if dataset_source == "builtin" and builtin_dataset_path is not None:
                dataset_arg = builtin_dataset_path.resolve()
            elif dataset_source == "custom" and custom_dataset is not None:
                raw_ds, _cached = await materialize_dataset_bytes(runtime, custom_dataset)
                dataset_arg = work_dir / "dataset.jsonl"
                await asyncio.to_thread(dataset_arg.write_bytes, raw_ds)

            if await _check_cancel_requested():
                await store.finish_optimization_job(
                    runtime.sessionmaker,
                    job_id,
                    status="cancelled",
                    stage="cancelled",
                    progress_json={**progress_state, "stage": "cancelled", "label": "Cancelled"},
                    log_tail=log_tail,
                    error="Cancelled by user",
                )
                return

            db_url_file = work_dir / "database.url"
            await asyncio.to_thread(_write_private_secret_file, db_url_file, db_url)

            cmd: list[str] = [
                sys.executable,
                "-m",
                "fleet_rlm.optimize",
                "--target",
                spec.target,
                "--database-url-file",
                str(db_url_file),
                "--output",
                str(artifact_path),
                "--log-dir",
                str(log_dir),
                "--progress-file",
                str(progress_path),
                "--report-file",
                str(report_path),
                "--seed",
                str(spec.seed),
            ]
            if spec.role_credentials:
                credentials_file = work_dir / "credentials.json"
                creds_payload = {
                    role: cred.to_credentials_payload()
                    for role, cred in spec.role_credentials.items()
                }
                await asyncio.to_thread(
                    _write_private_secret_file,
                    credentials_file,
                    json.dumps(creds_payload, separators=(",", ":")),
                )
                cmd.extend(["--credentials-file", str(credentials_file)])
            cmd.extend(_optimizer_arguments(spec, dataset_arg, branch_mode))

            env = build_subprocess_env(
                effective,
                target=spec.target,
                program_kind=spec.program_kind,
                local_interpreter=spec.local_interpreter,
                decision_credential=spec.role_credentials.get("decision_lm"),
            )

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(work_dir),
                env=env,
            )
            runtime.optimization_procs[job_id] = proc

            log_lock = asyncio.Lock()

            async def drain_stdout() -> None:
                nonlocal log_tail
                if proc.stdout is None:
                    return
                while True:
                    line = await _read_log_line(proc.stdout)
                    if not line:
                        break
                    decoded = opt_datasets.scrub_secrets(
                        line.decode("utf-8", errors="replace"), extra_secrets
                    )
                    async with log_lock:
                        log_tail = append_log_tail(log_tail, decoded)

            terminate_sent = False

            async def monitor_loop() -> None:
                nonlocal progress_state, cancelled, terminate_sent
                offset = 0
                last_flushed_tail = ""
                terminate_at = 0.0
                while proc.returncode is None:
                    offset, latest = await asyncio.to_thread(
                        read_latest_progress, progress_path, offset
                    )
                    async with log_lock:
                        tail_snapshot = log_tail
                    changed = tail_snapshot != last_flushed_tail
                    if latest is not None:
                        progress_state = opt_datasets.scrub_secrets(latest, extra_secrets)
                        changed = True
                    try:
                        if changed:
                            last_flushed_tail = tail_snapshot
                            await store.update_optimization_job_progress(
                                runtime.sessionmaker,
                                job_id,
                                status="running",
                                stage=str(progress_state.get("stage") or "running"),
                                progress_json=progress_state,
                                log_tail=tail_snapshot,
                            )
                        else:
                            await store.touch_optimization_job_heartbeat(
                                runtime.sessionmaker, job_id
                            )
                    except Exception as db_error:
                        _LOG.warning(
                            "Transient DB error updating optimization job %s progress (%s)",
                            job_id,
                            type(db_error).__name__,
                        )

                    cancel_now = await _poll_job_cancellation(
                        runtime.sessionmaker, job_id, cancel_event
                    )
                    if cancel_now:
                        cancelled = True
                        if not terminate_sent:
                            terminate_sent = True
                            terminate_at = asyncio.get_running_loop().time()
                            with suppress(ProcessLookupError, OSError):
                                proc.terminate()
                        elif (
                            asyncio.get_running_loop().time() - terminate_at
                            >= CANCEL_SIGKILL_GRACE_SECONDS
                        ):
                            with suppress(ProcessLookupError, OSError):
                                proc.kill()

                    if terminate_sent:
                        await asyncio.sleep(_MONITOR_POLL_INTERVAL_SECONDS)
                    else:
                        with suppress(TimeoutError):
                            await asyncio.wait_for(
                                cancel_event.wait(), timeout=_MONITOR_POLL_INTERVAL_SECONDS
                            )

            drain_task = asyncio.create_task(drain_stdout())
            monitor_task = asyncio.create_task(monitor_loop())
            try:
                await proc.wait()
                await drain_task
            finally:
                if proc.returncode is None:
                    if terminate_sent:
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except TimeoutError:
                            with suppress(ProcessLookupError, OSError):
                                proc.kill()
                            with suppress(Exception):
                                await proc.wait()
                    else:
                        terminate_sent = True
                        await _terminate_and_reap_proc(proc, grace_seconds=5.0)
                await _stop_monitor_task(monitor_task)
                await _stop_monitor_task(drain_task)
                _remove_secret_files(db_url_file, credentials_file)
                db_url_file = None
                credentials_file = None

            _, final_progress = await asyncio.to_thread(read_latest_progress, progress_path, 0)
            if final_progress is not None:
                progress_state = opt_datasets.scrub_secrets(final_progress, extra_secrets)
            scrubbed_log_tail = opt_datasets.scrub_secrets(log_tail, extra_secrets)

            report_payload: dict[str, Any] | None = None
            if report_path.is_file():
                with suppress(json.JSONDecodeError, OSError):
                    parsed_report = json.loads(
                        await asyncio.to_thread(report_path.read_text, encoding="utf-8")
                    )
                    if isinstance(parsed_report, dict):
                        report_payload = opt_datasets.scrub_secrets(parsed_report, extra_secrets)

            unsuccessful = _unsuccessful_exit(cancelled, proc.returncode, scrubbed_log_tail)
            if unsuccessful is not None:
                status, message = unsuccessful
                await store.finish_optimization_job(
                    runtime.sessionmaker,
                    job_id,
                    status=status,
                    stage=status,
                    progress_json={**progress_state, "stage": status, "label": status.capitalize()},
                    report_json=report_payload,
                    log_tail=scrubbed_log_tail,
                    error=opt_datasets.scrub_secrets(message, extra_secrets),
                )
                return

            artifact_key: str | None = None
            artifact_sha256: str | None = None
            artifact_size: int | None = None
            if artifact_path.is_file():
                artifact_key = runtime.storage.optimization_artifact_key(job_id, spec.target)
                artifact_sha256, artifact_size = await runtime.storage.put_object(
                    artifact_key, artifact_path
                )
                with suppress(OSError):
                    raw_artifact = await asyncio.to_thread(artifact_path.read_bytes)
                    cached_art = runtime.artifact_cache_dir / f"{artifact_sha256}.json"
                    await asyncio.to_thread(
                        _write_private_file_atomic,
                        runtime.artifact_cache_dir,
                        cached_art,
                        raw_artifact,
                    )

            report_inner = (
                report_payload.get("report")
                if isinstance(report_payload, dict)
                and isinstance(report_payload.get("report"), dict)
                else report_payload
            )
            if isinstance(report_inner, dict):
                report_inner = opt_datasets.scrub_secrets(report_inner, extra_secrets)
            mlflow_run_id = (
                str(report_payload.get("mlflow_run_id") or report_inner.get("mlflow_run_id") or "")
                if isinstance(report_payload, dict) and isinstance(report_inner, dict)
                else None
            ) or None
            resolved_decision_model = (
                str(report_inner.get("resolved_decision_model") or "")
                if isinstance(report_inner, dict)
                else ""
            ) or None

            await store.finish_optimization_job(
                runtime.sessionmaker,
                job_id,
                status="completed",
                stage="completed",
                saved=(
                    bool(report_payload["saved"])
                    if isinstance(report_payload, dict) and "saved" in report_payload
                    else None
                ),
                baseline_score=(
                    report_payload.get("baseline_score")
                    if isinstance(report_payload, dict)
                    else None
                ),
                optimized_score=(
                    report_payload.get("optimized_score")
                    if isinstance(report_payload, dict)
                    else None
                ),
                train_size=(
                    report_payload.get("train_size") if isinstance(report_payload, dict) else None
                ),
                val_size=(
                    report_payload.get("val_size") if isinstance(report_payload, dict) else None
                ),
                holdout_size=(
                    report_payload.get("holdout_size") if isinstance(report_payload, dict) else None
                ),
                progress_json={**progress_state, "stage": "completed", "label": "Completed"},
                report_json=report_inner,
                log_tail=scrubbed_log_tail,
                artifact_object_key=artifact_key,
                artifact_sha256=artifact_sha256,
                artifact_size_bytes=artifact_size,
                resolved_decision_model=resolved_decision_model,
                decision_provider_id=spec.decision_provider_id,
                mlflow_run_id=mlflow_run_id,
                mlflow_experiment=optimization_experiment(effective),
            )
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            await _terminate_and_reap_proc(proc, grace_seconds=5.0)
        with suppress(Exception):
            await store.finish_optimization_job(
                runtime.sessionmaker,
                job_id,
                status="cancelled",
                stage="cancelled",
                progress_json={**progress_state, "stage": "cancelled", "label": "Cancelled"},
                log_tail=opt_datasets.scrub_secrets(log_tail, extra_secrets),
                error="Cancelled by server shutdown",
            )
    except Exception as error:
        scrubbed_error = opt_datasets.scrub_secrets(
            str(error) or type(error).__name__, extra_secrets
        )[:500]
        with suppress(Exception):
            await store.finish_optimization_job(
                runtime.sessionmaker,
                job_id,
                status="failed",
                stage="failed",
                progress_json={**progress_state, "stage": "failed", "label": "Failed"},
                log_tail=opt_datasets.scrub_secrets(log_tail, extra_secrets),
                error=scrubbed_error,
            )
    finally:
        _remove_secret_files(db_url_file, credentials_file)
        runtime.optimization_procs.pop(job_id, None)
        runtime.optimization_tasks.pop(job_id, None)
        runtime.optimization_cancel_events.pop(job_id, None)
        if ephemeral_branch_id is not None:
            await delete_ephemeral_neon_branch(runtime.settings, ephemeral_branch_id)


async def shutdown_optimization_runtime(runtime: OptimizationRuntimeLike) -> None:
    """Gracefully unblock SSE streams, stop subprocesses, and close Daytona volume helpers."""
    runtime.shutdown_event.set()
    tasks = list(runtime.optimization_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        with suppress(Exception):
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=6.0)
    for proc in list(runtime.optimization_procs.values()):
        if proc is not None and proc.returncode is None:
            with suppress(ProcessLookupError, OSError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                with suppress(ProcessLookupError, OSError):
                    proc.kill()
                with suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
    with suppress(Exception):
        await interpreter.close_dataset_volume_helpers()
