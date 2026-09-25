"""Exact prepare-before-stream boundary for one lifecycle-issued Turn."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Literal, Protocol
from uuid import UUID

import dspy
from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.artifacts.models import ArtifactAccess
from fleet_rlm.artifacts.promotion import RunArtifactSink
from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.artifacts.tools import ArtifactToolHost
from fleet_rlm.attachments import (
    AttachmentAccess,
    AttachmentRun,
    AttachmentToolHost,
    PreparedAttachment,
    PreparedAttachments,
    RunAttachmentSink,
)
from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.tracing import turn_phase_span
from fleet_rlm.paths import VolumePaths
from fleet_rlm.persistence.database import DatabaseConnectionError
from fleet_rlm.result_snapshot import ResultSnapshotSink
from fleet_rlm.rlm.budget import BudgetLimits, TurnBudget
from fleet_rlm.rlm.events import (
    AsyncToolBridge,
    AttachmentRead,
    SkillActivated,
    SkillLoaded,
    ToolEventView,
)
from fleet_rlm.rlm.execution import (
    DelegationPolicy,
    ExecutionRuntime,
    PreparationNotice,
    PreparedCapabilities,
    RetainableEnvironmentRelease,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMInterpreter,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.program import AttachmentContextCapsule, AttachmentContextEntry, RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import ChildRuntimeFactory, RecursiveRLMOptions
from fleet_rlm.sessions.context import build_session_context_manifest
from fleet_rlm.sessions.history import dspy_history_for_claim
from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
from fleet_rlm.sessions.history_transport import committed_history_for_claim
from fleet_rlm.sessions.run_state import ClaimedRun
from fleet_rlm.sessions.task import SessionTaskService, task_checkpoint_summary
from fleet_rlm.sessions.task_tools import SessionTaskToolHost
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.skills.models import SkillDefinition
from fleet_rlm.skills.resolver import resolve_selected_skills, resolved_schema, resolved_signature
from fleet_rlm.skills.tools import SkillToolHost
from fleet_rlm.turn_settlement import MemoryIntentBuilder, OwnedPostCommitMemoryPromotion
from fleet_rlm.workspace.memory import (
    MemoryCandidate,
    MemoryCandidateCollector,
    MemoryCandidateToolHost,
    WorkspaceMemoryToolHost,
    build_memory_promotion_intents,
    prepare_turn_memory_digest,
    promote_turn_memory_candidates,
)
from fleet_rlm.workspace.models import (
    DAYTONA_WORKSPACE_CAPABILITY,
    WORKSPACE_MEMORY_INJECTION_TAIL_BYTES,
    SessionWorkspaceFS,
    WorkspaceCapabilityMetadata,
)
from fleet_rlm.workspace.projects import ProjectToolHost
from fleet_rlm.workspace.storage import StorageSession, VolumeBlobFs
from fleet_rlm.workspace.workspace import WorkspaceToolHost

AsyncCleanup = Callable[[], Awaitable[Any]]


class RunPreparationError(RuntimeError):
    """Base class for safe preparation failures."""


class RunPreparationCancelledError(RunPreparationError):
    pass


class RunPreparationTimeoutError(RunPreparationError):
    pass


class RunPreparationUnavailableError(RunPreparationError):
    pass


@dataclass(slots=True)
class _PreparedTurnResources:
    cleanups: tuple[AsyncCleanup, ...]
    pre_commit_cleanup_indices: frozenset[int] = frozenset()
    _closed: bool = field(default=False, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _close_error: BaseException | None = field(default=None, init=False)
    _completed_cleanups: set[int] = field(default_factory=set, init=False, repr=False)

    async def aclose_pre_commit(self) -> None:
        """Close the narrow execution boundary required before success commit.

        Explicit pre-commit obligations must settle before durable success.
        Retained broker preparation currently registers none; attachment removal,
        capabilities and provider lease release remain post-settlement owners.
        """
        await self._aclose_indices(self.pre_commit_cleanup_indices)

    async def aclose(self) -> None:
        await self._aclose_indices(frozenset(range(len(self.cleanups))), close_all=True)

    async def _aclose_indices(self, indices: frozenset[int], *, close_all: bool = False) -> None:
        async with self._lock:
            if self._closed:
                return
            first_error: BaseException | None = None
            for index in sorted(indices, reverse=True):
                if index in self._completed_cleanups:
                    continue
                cleanup = self.cleanups[index]
                try:
                    await cleanup()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    # A later retry only needs to re-run owners that did not
                    # cross their own successful cleanup boundary.
                    self._completed_cleanups.add(index)
            if first_error is not None:
                # Do not publish the closed boundary until every cleanup has
                # settled. A later owner can retry idempotent releases after a
                # transient provider/gate failure.
                self._close_error = RuntimeError("prepared Turn cleanup failed")
                raise self._close_error from first_error
            if close_all:
                self._close_error = None
                self._closed = True


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    execution: RLMExecutionContext
    artifact_sink: RunArtifactSink
    _resources: _PreparedTurnResources
    result_snapshot_sink: ResultSnapshotSink | None = None
    post_commit_memory_promotion: OwnedPostCommitMemoryPromotion | None = None
    memory_intent_builder: MemoryIntentBuilder | None = None
    # Internal engineering-observability correlation only: the preparation
    # fleet_turn root's MLflow trace and span ids, attached by TurnRuntime after
    # preparation. Never persisted, never projected into SSE/product events.
    preparation_trace_id: str | None = None
    preparation_span_id: str | None = None
    image_identity: str | None = None

    @property
    def resources(self) -> _PreparedTurnResources:
        """Return the owned cleanup boundary without exposing its internals."""
        return self._resources

    async def aclose(self) -> None:
        if self.post_commit_memory_promotion is not None:
            await self.post_commit_memory_promotion.wait_owned()
        await self._resources.aclose()

    async def aclose_before_commit(self) -> None:
        """Contain native execution resources before a successful Turn commits."""
        await self._resources.aclose_pre_commit()


def _workspace_memory_digest(capabilities: PreparedCapabilities) -> str:
    """Bounded defensive projection of the capability digest; never fails a Run."""
    digest = getattr(capabilities, "workspace_memory_digest", "")
    if not isinstance(digest, str) or len(digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
        return ""
    return digest


def _active_task_summary(capabilities: PreparedCapabilities) -> str:
    summary = getattr(capabilities, "active_task_summary", "")
    if not isinstance(summary, str) or len(summary) > 2048:
        return ""
    return summary


class RunPreparation(Protocol):
    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedTurn: ...


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    interpreter: RLMInterpreter | None
    attachment_sink: RunAttachmentSink
    artifact_sink: RunArtifactSink
    release: AsyncCleanup
    result_snapshot_sink: ResultSnapshotSink | None = None
    child_runtime_factory: ChildRuntimeFactory | None = None
    child_result_writer: Callable[[int, str, bytes], Awaitable[str]] | None = None
    context_mount_path: str | None = None
    workspace_memory_store: Any | None = None
    volume_fs: VolumeBlobFs | None = None
    session_workspace: StorageSession | None = None
    project_workspace: StorageSession | None = None
    # Optional provider-owned root release. ``release`` remains per-Turn when
    # ``release_is_resident`` is false; the resident Session runtime takes
    # ``resident_release`` instead.
    resident_release: AsyncCleanup | None = None
    release_is_resident: bool = True
    # DSPy in-process interpreters accept native History; the Daytona Sandbox
    # bridge needs the serializable Session transport.
    history_format: Literal["dspy", "sandbox"] = "dspy"
    # Synchronous provider fence used when the resident RLM becomes tainted.
    # This is an internal lifecycle hook; it does not cross the HTTP/SSE seam.
    mark_tainted: Callable[[], None] | None = None
    # Composition-owned loop bridge for async host Tools invoked through
    # DSPy's synchronous interpreter seam.
    async_bridge: AsyncToolBridge | None = None
    image_identity: str | None = None


class RunEnvironmentProvider(Protocol):
    async def acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment: ...


class RunAttachmentPreparer(Protocol):
    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: RunAttachmentSink,
    ) -> PreparedAttachments: ...


class CapabilityPreparer(Protocol):
    async def prepare(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedCapabilities: ...


@dataclass(frozen=True, slots=True)
class TurnPreparationPlan:
    """Immutable inputs for one Turn preparation function."""

    models: RLMModelBundle
    options: RLMOptions
    attachments: RunAttachmentPreparer
    environments: RunEnvironmentProvider
    capabilities: CapabilityPreparer
    task_service: SessionTaskService | None = None
    recursive_options: RecursiveRLMOptions = field(default_factory=RecursiveRLMOptions)
    wrap_up_seconds: float = 300.0
    budget_limits: BudgetLimits = field(default_factory=BudgetLimits)

    def __post_init__(self) -> None:
        object.__setattr__(self, "wrap_up_seconds", max(0.0, float(self.wrap_up_seconds)))

    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedTurn:
        return await prepare_turn(self, run, deadline=deadline)

    async def aclose(self) -> bool:
        return await close_turn_preparation(self)


async def _check_cancellation(run: ClaimedRun) -> None:
    try:
        if await run.cancellation_requested():
            raise RunPreparationCancelledError("Turn cancelled")
    except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
        raise RunPreparationUnavailableError("Turn cancellation status is unavailable") from exc


async def prepare_turn(plan: TurnPreparationPlan, run: ClaimedRun, *, deadline: float) -> PreparedTurn:
    await _check_cancellation(run)

    if plan.task_service is not None:
        try:
            async with asyncio.timeout_at(deadline):
                await plan.task_service.seed(
                    run.session_id,
                    user_id=run.access.user_id,
                    workspace_id=run.access.workspace_id,
                    first_request=run.input.text,
                )
        except TimeoutError:
            raise RunPreparationTimeoutError("Turn preparation timed out") from None
        except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
            raise RunPreparationUnavailableError("Session task state is unavailable") from exc
        await _check_cancellation(run)

    with turn_phase_span("Turn.acquire_environment", inputs={}) as environment_phase:
        try:
            environment = await plan.environments.acquire(run, deadline=deadline)
        except RunPreparationError:
            raise
        except Exception as exc:
            raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
        environment_phase.set_outputs(
            {
                "has_interpreter": environment.interpreter is not None,
                "has_snapshot_sink": environment.result_snapshot_sink is not None,
            }
        )

    if environment.resident_release is not None:
        environment_release: RetainableEnvironmentRelease | None = RetainableEnvironmentRelease(
            environment.resident_release,
            taint_callback=environment.mark_tainted,
        )
        turn_environment_release: RetainableEnvironmentRelease | None = RetainableEnvironmentRelease(
            environment.release
        )
    elif environment.release_is_resident:
        environment_release = RetainableEnvironmentRelease(
            environment.release,
            taint_callback=environment.mark_tainted,
        )
        turn_environment_release = None
    else:
        environment_release = None
        turn_environment_release = RetainableEnvironmentRelease(environment.release)

    staged = PreparedAttachments((), ())
    capabilities: PreparedCapabilities | None = None

    async def remove_staged() -> None:
        await _remove_staged(environment.attachment_sink, staged)

    def _build_resources() -> _PreparedTurnResources:
        cleanups: list[AsyncCleanup] = []
        if environment_release is not None:
            cleanups.append(environment_release.release)
        if turn_environment_release is not None:
            cleanups.append(turn_environment_release.release)
        if capabilities is not None:
            cleanups.append(capabilities.aclose)
        cleanups.append(remove_staged)
        return _PreparedTurnResources(tuple(cleanups))

    try:
        _check_deadline(deadline)
        with turn_phase_span(
            "Turn.stage_attachments",
            inputs={"attachment_count": len(run.input.attachment_ids)},
        ) as attachments_phase:
            try:
                staged = await plan.attachments.prepare_run(
                    AttachmentAccess(run.access.user_id, run.access.workspace_id),
                    run.input.attachment_ids,
                    AttachmentRun(run.session_id, run.run_id),
                    environment.attachment_sink,
                )
            except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                raise RunPreparationUnavailableError("Turn attachments are unavailable") from exc
            attachments_phase.set_outputs(
                {
                    "staged_count": len(staged.refs),
                    "staged_bytes": sum(ref.byte_size for ref in staged.refs),
                }
            )

        with turn_phase_span(
            "Turn.prepare_capabilities",
            inputs={"skill_selection_count": len(run.input.skill_selections)},
        ) as capabilities_phase:
            try:
                async with asyncio.timeout_at(deadline):
                    capabilities = await _prepare_capabilities(plan, run, environment, staged, deadline)
            except TimeoutError:
                raise RunPreparationTimeoutError("Turn preparation timed out") from None
            except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                raise RunPreparationUnavailableError("Turn capabilities are unavailable") from exc
            capabilities_phase.set_outputs({"notice_count": len(getattr(capabilities, "preparation_notices", ()))})

        await _check_cancellation(run)
        _check_deadline(deadline)

        staged_by_id = {item.attachment_id: item for item in staged.staged}
        attachment_context = None
        if staged.refs and environment.context_mount_path is not None:
            attachment_context = AttachmentContextCapsule(
                tuple(
                    AttachmentContextEntry(
                        attachment_id=ref.id,
                        filename=ref.filename,
                        content_type=ref.content_type,
                        byte_size=ref.byte_size,
                        checksum_sha256=ref.checksum_sha256,
                        sandbox_path=staged_by_id[ref.id].sandbox_path,
                    )
                    for ref in staged.refs
                ),
                mount_root=environment.context_mount_path,
            )
    except BaseException:
        await asyncio.shield(_build_resources().aclose())
        raise

    assert capabilities is not None
    resources = _build_resources()
    try:
        turn_budget = TurnBudget(
            deadline=deadline if math.isfinite(deadline) else None,
            limits=plan.budget_limits,
        )
        turn_models = plan.models.bind_turn_deadline(
            deadline=deadline,
            reserve_seconds=plan.wrap_up_seconds,
            budget=turn_budget,
        )
    except BaseException:
        await asyncio.shield(resources.aclose())
        raise

    execution = RLMExecutionContext(
        identity=RunIdentity(
            run_id=run.run_id,
            session_id=run.session_id,
            access=run.access,
            authority=run.authority,
        ),
        session=SessionView(
            request=run.input.text,
            session_context=build_session_context_manifest(
                run.session_id,
                run.checkpoint_version,
                run.history,
            ),
            attachments=tuple(
                PreparedAttachment(
                    ref.id,
                    ref.filename,
                    ref.content_type,
                    ref.byte_size,
                    ref.checksum_sha256,
                )
                for ref in staged.refs
            ),
            attachment_context=attachment_context,
            preparation_notices=tuple(getattr(capabilities, "preparation_notices", ())),
            workspace_memory_digest=_workspace_memory_digest(capabilities),
            active_task_summary=_active_task_summary(capabilities),
            history=(
                committed_history_for_claim(run)
                if environment.history_format == "sandbox"
                else dspy_history_for_claim(run)
            ),
        ),
        execution=ExecutionRuntime(
            models=turn_models,
            options=plan.options,
            interpreter=environment.interpreter,
            cancellation_requested=run.cancellation_requested,
            deadline=deadline,
            wrap_up_seconds=plan.wrap_up_seconds,
            environment_release=environment_release,
            async_bridge=environment.async_bridge,
        ),
        capabilities=capabilities,
        delegation=DelegationPolicy(
            child_runtime_factory=environment.child_runtime_factory,
            child_result_writer=environment.child_result_writer,
            recursive_options=plan.recursive_options,
        ),
        selected_skill_count=len(run.input.skill_selections),
    )
    return PreparedTurn(
        execution=execution,
        artifact_sink=environment.artifact_sink,
        _resources=resources,
        result_snapshot_sink=environment.result_snapshot_sink,
        post_commit_memory_promotion=getattr(capabilities, "post_commit_memory_promotion", None),
        memory_intent_builder=getattr(capabilities, "memory_intent_builder", None),
        image_identity=environment.image_identity,
    )


async def close_turn_preparation(plan: TurnPreparationPlan) -> bool:
    close = getattr(plan.environments, "aclose", None)
    return bool(await close()) if callable(close) else True


async def wait_for_session_idle(
    plan: TurnPreparationPlan,
    workspace_id: UUID,
    session_id: UUID,
    *,
    deadline: float,
) -> None:
    wait = getattr(plan.environments, "wait_for_session_idle", None)
    if callable(wait):
        await wait(workspace_id, session_id, deadline=deadline)


async def _prepare_capabilities(
    plan: TurnPreparationPlan,
    run: ClaimedRun,
    environment: RunEnvironment,
    staged: PreparedAttachments,
    deadline: float,
) -> PreparedCapabilities:
    task = asyncio.create_task(plan.capabilities.prepare(run, environment, staged, deadline=deadline))
    try:
        while not task.done():
            if await run.cancellation_requested():
                task.cancel()
                raise RunPreparationCancelledError("Turn cancelled")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                task.cancel()
                raise RunPreparationTimeoutError("Turn preparation timed out")
            await asyncio.wait((task,), timeout=min(0.05, remaining))
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _check_deadline(deadline: float) -> None:
    if asyncio.get_running_loop().time() >= deadline:
        raise RunPreparationTimeoutError("Turn preparation timed out")


async def _remove_staged(sink: RunAttachmentSink, prepared: PreparedAttachments) -> None:
    for item in reversed(prepared.staged):
        try:
            await sink.remove_private(item.sandbox_path)
        except Exception:
            continue


class EmptySkillHost:
    """No-op ledger used only when the bundled host catalog is unavailable."""

    def drain_public_events(self) -> list[dict[str, Any]]:
        return []

    def loaded_definitions(self) -> tuple[SkillDefinition, ...]:
        return ()


def skill_event(item: Mapping[str, Any]) -> SkillActivated | SkillLoaded:
    if item.get("kind") == "skill.activated":
        return SkillActivated(
            str(item["skill_id"]),
            str(item["name"]),
            str(item["version"]),
            str(item["trust"]),
            tuple(str(value) for value in item.get("affordances", ())),
        )
    return SkillLoaded(str(item["skill_id"]), str(item["name"]), str(item["version"]))


class PreparedHostCapabilities:
    """Runtime-neutral execution spec plus host activation/loading ledgers."""

    def __init__(
        self,
        spec: RLMExecutionSpec,
        *,
        files: Any,
        skills: Any,
        close_files: bool,
        artifact_candidates: bool,
        artifacts: Any | None = None,
        preparation_notices: tuple[PreparationNotice, ...] = (),
        memory_candidates: MemoryCandidateCollector | None = None,
        workspace_memory_digest: str = "",
        active_task_summary: str = "",
        post_commit_memory_promotion: OwnedPostCommitMemoryPromotion | None = None,
        memory_intent_builder: MemoryIntentBuilder | None = None,
    ) -> None:
        self.spec = spec
        self._files = files
        self._skills = skills
        self._close_files = close_files
        self._artifact_candidates = artifact_candidates
        self._artifacts = artifacts
        self._memory_candidates = memory_candidates
        self.preparation_notices = preparation_notices
        if (
            not isinstance(workspace_memory_digest, str)
            or len(workspace_memory_digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
        ):
            workspace_memory_digest = ""
        self.workspace_memory_digest = workspace_memory_digest
        self.active_task_summary = active_task_summary[:2048] if isinstance(active_task_summary, str) else ""
        self.post_commit_memory_promotion = post_commit_memory_promotion
        self.memory_intent_builder = memory_intent_builder

    def drain_public_details(self) -> tuple[AttachmentRead | SkillActivated | SkillLoaded, ...]:
        values: list[AttachmentRead | SkillActivated | SkillLoaded] = []
        for host in (self._files, self._artifacts):
            if host is None:
                continue
            drain = getattr(host, "drain_public_events", None)
            if not callable(drain):
                continue
            for item in drain():
                if item.get("event_kind", "attachment.read") != "attachment.read":
                    continue
                values.append(
                    AttachmentRead(
                        UUID(str(item["attachment_id"])),
                        str(item["filename"]),
                        int(item["byte_size"]),
                    )
                )
        values.extend(skill_event(item) for item in self._skills.drain_public_events())
        return tuple(values)

    def loaded_skills(self) -> tuple[SkillDefinition, ...]:
        """Return the pinned Skills available at the instant of delegation."""
        return self._skills.loaded_definitions()

    def drain_artifact_candidates(self) -> Any:
        if not self._artifact_candidates:
            return ()
        if self._artifacts is not None:
            return self._artifacts.drain_artifact_candidates()
        drain = getattr(self._files, "drain_artifact_candidates", None)
        return drain() if callable(drain) else ()

    def drain_memory_candidates(self) -> tuple[MemoryCandidate, ...]:
        """Drain Run-scoped memory proposals; empty when the policy did not expose them."""
        if self._memory_candidates is None:
            return ()
        return self._memory_candidates.drain()

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        recorder = getattr(self._files, "record_attachment_accesses", None)
        if callable(recorder):
            recorder(attachment_ids)

    async def aclose(self) -> None:
        first_error: BaseException | None = None
        if self._close_files:
            try:
                await self._files.aclose()
            except asyncio.CancelledError as exc:
                first_error = exc
            except Exception as exc:
                first_error = exc
        if self._artifacts is not None:
            try:
                await self._artifacts.aclose()
            except asyncio.CancelledError as exc:
                if first_error is None:
                    first_error = exc
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


async def prepare_host_capabilities(
    *,
    turn: ClaimedRun,
    skill_catalog: SkillCatalog,
    base_tools: Sequence[dspy.Tool],
    base_event_views: Mapping[str, ToolEventView],
    workspace: WorkspaceCapabilityMetadata,
    workspace_fs: SessionWorkspaceFS | None = None,
    artifact_reader: ArtifactReader | None = None,
    deadline: float,
) -> tuple[RLMExecutionSpec, SkillToolHost | EmptySkillHost, tuple[PreparationNotice, ...]]:
    """Resolve history and exact Skills identically for every Run environment."""
    history_host = SessionHistoryToolHost(turn.history)
    history_tools = history_host.as_tools()
    event_views = {**base_event_views, **history_host.event_views()}
    selections = tuple(turn.input.skill_selections)

    async def read_artifact(artifact_id: UUID, max_bytes: int) -> bytes:
        assert artifact_reader is not None
        content = await artifact_reader.content(
            ArtifactAccess(user_id=turn.access.user_id, workspace_id=turn.access.workspace_id),
            artifact_id,
            max_bytes=max_bytes,
        )
        return content.data

    if getattr(skill_catalog, "unavailable", False):
        from fleet_rlm.skills.errors import InvalidSkillSelectionError

        if selections:
            raise InvalidSkillSelectionError() from None
        return (
            RLMExecutionSpec(
                skill_cards=(),
                tools=(*base_tools, *history_tools),
                tool_event_views=event_views,
                workspace=workspace,
                read_artifact=read_artifact if artifact_reader is not None else None,
            ),
            EmptySkillHost(),
            (PreparationNotice("skills_unavailable", "Skills are unavailable"),),
        )

    resolved = resolve_selected_skills(skill_catalog, selections)
    if await turn.cancellation_requested():
        raise RunPreparationCancelledError("Turn cancelled")
    if asyncio.get_running_loop().time() >= deadline:
        raise RunPreparationTimeoutError("Turn preparation timed out")

    skill_host = SkillToolHost(
        skill_catalog,
        allowed_skill_ids=(frozenset(skill.card.id for skill in resolved.selected) if selections else None),
        workspace=workspace_fs,
    )
    schema_id, schema_version = resolved_schema(resolved)
    spec = RLMExecutionSpec(
        skill_cards=resolved.cards,
        signature=resolved_signature(resolved),
        skill_instructions=resolved.instructions,
        output_schema_id=schema_id,
        output_schema_version=schema_version,
        tools=(*base_tools, *history_tools, *skill_host.as_tools()),
        tool_event_views={**event_views, **skill_host.event_views()},
        workspace=workspace,
        read_artifact=read_artifact if artifact_reader is not None else None,
    )
    for skill in resolved.selected:
        skill_host.mark_preloaded(skill)
    return spec, skill_host, ()


@dataclass(slots=True)
class DaytonaCapabilityPreparer:
    """Assemble Run capabilities from storage selected by the environment owner."""

    settings: Settings
    skill_catalog: SkillCatalog
    volume_paths: VolumePaths
    artifact_reader: ArtifactReader | None = None
    task_service: SessionTaskService | None = None

    async def prepare(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedHostCapabilities:
        if (
            environment.volume_fs is None
            or environment.session_workspace is None
            or environment.project_workspace is None
            or environment.workspace_memory_store is None
        ):
            raise TypeError("Daytona environment is missing required capability storage")

        volume_fs = environment.volume_fs
        session_workspace = environment.session_workspace
        projects_fs = environment.project_workspace

        def read_child_source(path: str, max_bytes: int) -> bytes:
            if path.startswith("projects/"):
                return projects_fs.read_file_bytes(path.removeprefix("projects/"), max_bytes=max_bytes)
            return session_workspace.read_file_bytes(path, max_bytes=max_bytes)

        attachment_host = AttachmentToolHost(
            attachments=attachments.refs,
            staged_attachments=attachments.staged,
            volume_fs=volume_fs,
        )
        artifact_host = ArtifactToolHost(
            volume_fs=volume_fs,
            user_id=run.access.user_id,
            workspace_id=run.access.workspace_id,
            session_id=run.session_id,
            run_id=run.run_id,
            max_artifact_bytes=self.settings.max_artifact_bytes,
            volume_paths=self.volume_paths,
        )
        workspace_host = WorkspaceToolHost(session_workspace, max_file_bytes=self.settings.max_upload_bytes)
        project_host = ProjectToolHost(projects_fs, max_file_bytes=self.settings.max_upload_bytes)
        memory_host = WorkspaceMemoryToolHost(environment.workspace_memory_store)

        task_host = None
        task_summary = ""
        if self.task_service is not None:
            checkpoint = await self.task_service.read(
                run.session_id,
                user_id=run.access.user_id,
                workspace_id=run.access.workspace_id,
            )
            task_summary = task_checkpoint_summary(checkpoint)
            if environment.async_bridge is not None:
                task_host = SessionTaskToolHost(
                    self.task_service,
                    session_id=run.session_id,
                    user_id=run.access.user_id,
                    workspace_id=run.access.workspace_id,
                    dispatcher=environment.async_bridge,
                )

        memory_candidates = None
        candidate_tools: tuple[Any, ...] = ()
        candidate_views: dict[str, Any] = {}
        if self.settings.rlm_autonomous_memory_categories:
            memory_candidates = MemoryCandidateCollector(
                run_id=run.run_id,
                allowed_categories=self.settings.rlm_autonomous_memory_categories,
            )
            candidate_host = MemoryCandidateToolHost(memory_candidates)
            candidate_tools = candidate_host.as_tools()
            candidate_views = dict(candidate_host.event_views())

        memory_digest = await prepare_turn_memory_digest(environment.workspace_memory_store, request=run.input.text)
        allowed_categories = tuple(self.settings.rlm_autonomous_memory_categories)
        memory_promotion = OwnedPostCommitMemoryPromotion(
            partial(
                promote_turn_memory_candidates,
                environment.workspace_memory_store,
                allowed_categories=allowed_categories,
            )
        )

        def memory_intent_builder(run_id: UUID, candidates: tuple[MemoryCandidate, ...]) -> tuple[Any, ...]:
            return build_memory_promotion_intents(
                run_id=run_id,
                candidates=candidates,
                allowed_categories=allowed_categories,
            )

        base_views = {
            **attachment_host.event_views(),
            **artifact_host.event_views(),
            **workspace_host.event_views(),
            **project_host.event_views(),
            **memory_host.event_views(),
            **(task_host.event_views() if task_host is not None else {}),
            **candidate_views,
        }
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=run,
            skill_catalog=self.skill_catalog,
            base_tools=(
                *attachment_host.as_tools(),
                *artifact_host.as_tools(),
                *workspace_host.as_tools(),
                *project_host.as_tools(),
                *memory_host.as_tools(),
                *(task_host.as_tools() if task_host is not None else ()),
                *candidate_tools,
            ),
            base_event_views=base_views,
            workspace=DAYTONA_WORKSPACE_CAPABILITY,
            workspace_fs=session_workspace,
            artifact_reader=self.artifact_reader,
            deadline=deadline,
        )
        spec = replace(spec, child_source_reader=read_child_source)
        return PreparedHostCapabilities(
            spec,
            files=attachment_host,
            artifacts=artifact_host,
            skills=skill_host,
            close_files=True,
            artifact_candidates=True,
            preparation_notices=notices,
            workspace_memory_digest=memory_digest,
            active_task_summary=task_summary,
            memory_candidates=memory_candidates,
            post_commit_memory_promotion=memory_promotion,
            memory_intent_builder=memory_intent_builder,
        )


__all__ = [
    "AsyncCleanup",
    "CapabilityPreparer",
    "DaytonaCapabilityPreparer",
    "EmptySkillHost",
    "PreparedHostCapabilities",
    "PreparedTurn",
    "RunAttachmentPreparer",
    "RunEnvironment",
    "RunEnvironmentProvider",
    "RunPreparation",
    "RunPreparationCancelledError",
    "RunPreparationError",
    "RunPreparationTimeoutError",
    "RunPreparationUnavailableError",
    "TurnPreparationPlan",
    "close_turn_preparation",
    "prepare_host_capabilities",
    "prepare_turn",
    "skill_event",
    "wait_for_session_idle",
]
