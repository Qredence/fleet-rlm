"""Exact prepare-before-stream boundary for one lifecycle-issued Turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

import dspy
from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.artifacts.promotion import RunArtifactSink
from fleet_rlm.attachments.models import (
    AttachmentAccess,
    AttachmentRun,
    PreparedAttachment,
    PreparedAttachments,
    RunAttachmentSink,
)
from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.run_lifecycle import ClaimedRun, MemoryIntentBuilder
from fleet_rlm.chat.session_context import build_session_context_manifest
from fleet_rlm.observability.turn_tracing import turn_phase_span
from fleet_rlm.persistence.database import DatabaseConnectionError
from fleet_rlm.result_snapshot import ResultSnapshotSink
from fleet_rlm.rlm.program import AttachmentContextCapsule, AttachmentContextEntry, RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import ChildRuntimeFactory, RecursiveRLMOptions
from fleet_rlm.rlm.result import empty_rlm_usage
from fleet_rlm.rlm.runtime import (
    DelegationPolicy,
    ExecutionRuntime,
    PreparedCapabilities,
    RetainableEnvironmentRelease,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMInterpreter,
    RunIdentity,
    SessionView,
    program_fingerprint_for_context,
)
from fleet_rlm.rlm.session_runtime import ProgramFingerprint, SessionKey, SessionRLMRegistry
from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
from fleet_rlm.sessions.history import is_committed_conversation_turn, to_dspy_history
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import HistoryMessage
from fleet_rlm.workspace.models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES

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
    _closed: bool = field(default=False, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _close_error: BaseException | None = field(default=None, init=False)
    _completed_cleanups: set[int] = field(default_factory=set, init=False, repr=False)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            first_error: BaseException | None = None
            for index in reversed(range(len(self.cleanups))):
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
            self._close_error = None
            self._closed = True


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    execution: RLMExecutionContext
    artifact_sink: RunArtifactSink
    _resources: _PreparedTurnResources
    # Direct ownership-facing projections make the preparation boundary
    # inspectable without requiring callers to know the execution context tree.
    claim: ClaimedRun | None = None
    history: dspy.History | CommittedSessionHistory | None = None
    session_context: Any | None = None
    attachments: tuple[PreparedAttachment, ...] = ()
    capabilities: PreparedCapabilities | None = None
    program: RLMExecutionSpec | None = None
    program_fingerprint: ProgramFingerprint | None = None
    authorization: RunAuthority | None = None
    result_snapshot_sink: ResultSnapshotSink | None = None
    post_commit_memory_promotion: OwnedPostCommitMemoryPromotion | None = None
    memory_intent_builder: MemoryIntentBuilder | None = None
    # Internal engineering-observability correlation only: the preparation
    # fleet_turn root's MLflow trace id, attached by TurnRuntime after
    # preparation. Never persisted, never projected into SSE/product events.
    preparation_trace_id: str | None = None

    @property
    def resources(self) -> _PreparedTurnResources:
        """Return the owned cleanup boundary without exposing its internals."""
        return self._resources

    async def aclose(self) -> None:
        if self.post_commit_memory_promotion is not None:
            await self.post_commit_memory_promotion.wait_owned()
        await self._resources.aclose()


# Historical spellings retained while callers migrate to the Turn terminology.
PreparedRun = PreparedTurn
_PreparedRunResources = _PreparedTurnResources


def _workspace_memory_digest(capabilities: PreparedCapabilities) -> str:
    """Bounded defensive projection of the capability digest; never fails a Run."""
    digest = getattr(capabilities, "workspace_memory_digest", "")
    if not isinstance(digest, str) or len(digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
        return ""
    return digest


def _claim_history_records(
    claim: ClaimedRun,
) -> tuple[tuple[CommittedTurn, ...], tuple[str, ...]]:
    """Project the claimed Session checkpoint to canonical ``(committed_turns, user_requests)``.

    The claimed checkpoint is the immutable ``SessionHistory`` already
    protected by the Run claim. It may include bounded failure tombstones for
    audit/retry surfaces; their attached ``CommittedTurn`` metadata lets this
    projection exclude them while preserving the canonical user/assistant
    pairing for successful Turns.
    """
    committed_turns: list[CommittedTurn] = []
    user_requests: list[str] = []
    pending_user_text: str | None = None
    for message in claim.history.messages:
        if not isinstance(message, HistoryMessage):
            continue
        if message.role == "user":
            # A second user message without an intervening assistant answer
            # means the previous user request was never committed; drop it so
            # the canonical factory never pairs an answer with the wrong
            # request.
            pending_user_text = message.content
            continue
        if message.role == "assistant":
            if message.committed_turn is not None and not is_committed_conversation_turn(message.committed_turn):
                # Failure/cancellation tombstones remain in the bounded
                # Session audit projection, but never become model context.
                pending_user_text = None
                continue
            if pending_user_text is None:
                # Defensive: an assistant answer without a prior user
                # request cannot be paired. The canonical factory would
                # reject the missing user request through its own
                # validation, but skipping the orphan here keeps the
                # projection total.
                continue
            committed_turns.append(
                CommittedTurn(
                    schema_version=1,
                    parts=(
                        UsagePart(value=empty_rlm_usage()),
                        TextPart(text=message.content),
                    ),
                )
            )
            user_requests.append(pending_user_text)
            pending_user_text = None
    return tuple(committed_turns), tuple(user_requests)


def claim_history_records(
    claim: ClaimedRun,
) -> tuple[tuple[CommittedTurn, ...], tuple[str, ...]]:
    """Return canonical committed Turns and requests for a claimed checkpoint."""
    return _claim_history_records(claim)


def build_dspy_history_for_claim(claim: ClaimedRun) -> dspy.History:
    """Build the canonical ``dspy.History`` snapshot for one claimed Session checkpoint.

    The helper is the single P44.8 entry point that fetches the committed
    Turns and user requests for the claimed checkpoint and materializes the
    exact installed ``dspy.History`` instance. The function never bypasses
    the claim (it consumes ``ClaimedRun.history``), never reads the durable
    store directly, and never inspects uncommitted state.

    The returned object is the exact installed ``dspy.History`` Pydantic
    model (DSPy 3.3.1) materialized through :func:`to_dspy_history` so the
    canonical conversation factory still applies its terminal-exclusion
    rules. A claim with no committed Turns yields a valid empty
    ``dspy.History(messages=[])`` that the native ``dspy.RLM._validate_inputs``
    contract accepts as the canonical empty History.
    """
    committed_turns, user_requests = claim_history_records(claim)
    return to_dspy_history(committed_turns, user_requests=user_requests)


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
    context_mount_path: str | None = None
    workspace_memory_store: Any | None = None
    post_commit_memory_promotion: OwnedPostCommitMemoryPromotion | None = None
    memory_intent_builder: MemoryIntentBuilder | None = None
    # Optional provider-owned root release. ``release`` remains per-Turn when
    # ``release_is_resident`` is false; the resident Session runtime takes
    # ``resident_release`` instead.
    resident_release: AsyncCleanup | None = None
    release_is_resident: bool = True
    # Provider-specific transport for the canonical committed conversation.
    # In-process runs use the exact dspy.History built below; Daytona supplies
    # CommittedSessionHistory because SandboxSerializable values cross its
    # interpreter boundary while raw Pydantic History does not.
    history_transport: dspy.History | CommittedSessionHistory | None = None


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


class DefaultRunPreparer:
    """Build exactly one complete immutable execution context before delivery."""

    def __init__(
        self,
        *,
        models: RLMModelBundle,
        options: RLMOptions,
        attachments: RunAttachmentPreparer,
        environments: RunEnvironmentProvider,
        capabilities: CapabilityPreparer,
        recursive_options: RecursiveRLMOptions | None = None,
        session_runtime_registry: SessionRLMRegistry | None = None,
    ) -> None:
        self._models = models
        self._options = options
        self._attachments = attachments
        self._environments = environments
        self._capabilities = capabilities
        self._recursive_options = recursive_options or RecursiveRLMOptions()
        self._session_runtime_registry = session_runtime_registry

    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedTurn:
        """
        Prepare the execution context and resources required to run a Run.

        Parameters:
            run (ClaimedRun): Run request and execution metadata.
            deadline (float): Absolute deadline for Run preparation.

        Returns:
            PreparedTurn: Prepared execution context, artifact sinks, and managed resources.

        Raises:
            RunPreparationCancelledError: If the Run is cancelled.
            RunPreparationTimeoutError: If Run preparation exceeds the deadline.
            RunPreparationUnavailableError: If required preparation services or resources are unavailable.
        """
        try:
            if await run.cancellation_requested():
                raise RunPreparationCancelledError("Turn cancelled")
        except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
            raise RunPreparationUnavailableError("Turn cancellation status is unavailable") from exc

        if self._session_runtime_registry is not None:
            await self._session_runtime_registry.evict_configured_idle(deadline=deadline)
            await self._session_runtime_registry.close_unhealthy(
                SessionKey(
                    workspace_id=str(run.access.workspace_id),
                    session_id=str(run.session_id),
                ),
                deadline=deadline,
            )

        with turn_phase_span("Turn.acquire_environment", inputs={}) as environment_phase:
            try:
                environment = await self._environments.acquire(run, deadline=deadline)
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

        # ``resident_release`` owns a provider root that may outlive this
        # prepared Turn.  ``release`` remains the per-preparation ownership
        # boundary (for Daytona, it releases the Session preparation gate).
        # A reused Daytona root has no resident callback and must not retain
        # that per-Turn gate wrapper in the Session state.
        if environment.resident_release is not None:
            environment_release: RetainableEnvironmentRelease | None = RetainableEnvironmentRelease(
                environment.resident_release
            )
            turn_environment_release: RetainableEnvironmentRelease | None = RetainableEnvironmentRelease(
                environment.release
            )
        elif environment.release_is_resident:
            environment_release = RetainableEnvironmentRelease(environment.release)
            turn_environment_release = None
        else:
            environment_release = None
            turn_environment_release = RetainableEnvironmentRelease(environment.release)
        staged = PreparedAttachments((), ())
        capabilities: PreparedCapabilities | None = None

        async def remove_staged() -> None:
            await self._remove_staged(environment.attachment_sink, staged)

        try:
            self._check_deadline(deadline)
            with turn_phase_span(
                "Turn.stage_attachments",
                inputs={"attachment_count": len(run.input.attachment_ids)},
            ) as attachments_phase:
                try:
                    staged = await self._attachments.prepare_run(
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
                        capabilities = await self._prepare_capabilities(run, environment, staged, deadline)
                except TimeoutError:
                    raise RunPreparationTimeoutError("Turn preparation timed out") from None
                except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                    raise RunPreparationUnavailableError("Turn capabilities are unavailable") from exc
                capabilities_phase.set_outputs({"notice_count": len(getattr(capabilities, "preparation_notices", ()))})
            try:
                if await run.cancellation_requested():
                    raise RunPreparationCancelledError("Turn cancelled")
            except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                raise RunPreparationUnavailableError("Turn cancellation status is unavailable") from exc
            self._check_deadline(deadline)

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
            cleanups: list[AsyncCleanup] = []
            if environment_release is not None:
                cleanups.append(environment_release.release)
            if turn_environment_release is not None:
                cleanups.append(turn_environment_release.release)
            if capabilities is not None:
                cleanups.append(capabilities.aclose)
            cleanups.append(remove_staged)
            await asyncio.shield(_PreparedTurnResources(tuple(cleanups)).aclose())
            raise

        assert capabilities is not None

        cleanups: list[AsyncCleanup] = []
        if environment_release is not None:
            cleanups.append(environment_release.release)
        if turn_environment_release is not None:
            cleanups.append(turn_environment_release.release)
        cleanups.extend((capabilities.aclose, remove_staged))
        resources = _PreparedTurnResources(tuple(cleanups))
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
                # Canonical committed Session conversation materialized from
                # the claimed checkpoint. Providers may select a typed
                # transport at their adapter boundary; otherwise the in-process
                # composition reuses the exact dspy.History instance.
                history=(
                    environment.history_transport
                    if environment.history_transport is not None
                    else build_dspy_history_for_claim(run)
                ),
            ),
            execution=ExecutionRuntime(
                models=self._models,
                options=self._options,
                interpreter=environment.interpreter,
                cancellation_requested=run.cancellation_requested,
                deadline=deadline,
                environment_release=environment_release,
            ),
            capabilities=capabilities,
            delegation=DelegationPolicy(
                child_runtime_factory=environment.child_runtime_factory,
                recursive_options=self._recursive_options,
            ),
            selected_skill_count=len(run.input.skill_selections),
        )
        history = execution.session.history
        return PreparedTurn(
            execution=execution,
            artifact_sink=environment.artifact_sink,
            _resources=resources,
            claim=run,
            history=history,
            session_context=execution.session.session_context,
            attachments=execution.session.attachments,
            capabilities=capabilities,
            program=capabilities.spec,
            # The runner calls the same helper again with its observed and
            # recursive Tool wrappers; this preparation value is the composed
            # program identity available before execution starts.
            program_fingerprint=program_fingerprint_for_context(execution),
            authorization=run.authority,
            result_snapshot_sink=environment.result_snapshot_sink,
            post_commit_memory_promotion=environment.post_commit_memory_promotion,
            memory_intent_builder=environment.memory_intent_builder,
        )

    async def aclose(self) -> bool:
        """Close provider-owned resident root leases during composition shutdown."""
        close = getattr(self._environments, "aclose", None)
        if callable(close):
            result = await close()
            return result is not False
        return True

    async def _prepare_capabilities(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        staged: PreparedAttachments,
        deadline: float,
    ) -> PreparedCapabilities:
        task = asyncio.create_task(self._capabilities.prepare(run, environment, staged, deadline=deadline))
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

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise RunPreparationTimeoutError("Turn preparation timed out")

    @staticmethod
    async def _remove_staged(
        sink: RunAttachmentSink,
        prepared: PreparedAttachments,
    ) -> None:
        for item in reversed(prepared.staged):
            try:
                await sink.remove_private(item.sandbox_path)
            except Exception:
                continue


__all__ = [
    "AsyncCleanup",
    "CapabilityPreparer",
    "DefaultRunPreparer",
    "PreparedRun",
    "PreparedTurn",
    "RunAttachmentPreparer",
    "RunEnvironment",
    "RunEnvironmentProvider",
    "RunPreparation",
    "RunPreparationCancelledError",
    "RunPreparationError",
    "RunPreparationTimeoutError",
    "RunPreparationUnavailableError",
    "build_dspy_history_for_claim",
    "claim_history_records",
]
