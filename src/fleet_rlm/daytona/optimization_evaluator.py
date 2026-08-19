"""Disposable no-volume Daytona lifecycle for safe optimization evaluation.

This module owns strict evaluator sandbox creation and destruction.  It does not
bootstrap a workspace, upload the checkout, stage context, or inject model
credentials.  Candidate execution stays blocked for public optimization runs
until the separate read-only input and broker-policy live proofs exist.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import dspy

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec
from fleet_rlm.optimization.curated_input import CuratedEvaluationStore
from fleet_rlm.optimization.evidence import ValidatedStrictDaytonaProof
from fleet_rlm.rlm.dspy_contract import (
    PredictionOutputError,
    PredictionResult,
    RLMOptions,
    RLMUsage,
    build_native_rlm,
    observed_usage,
    prediction_result,
)
from fleet_rlm.runtime.owned_effect import OwnedEffect

if TYPE_CHECKING:
    from fleet_rlm.optimization.types import OptimizationRecord

_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OptimizationSandboxPolicyError(ValueError):
    """A policy would weaken the safe evaluator sandbox boundary."""


class StrictEvaluationCapabilityError(RuntimeError):
    """Required live isolation evidence has not authorized evaluator execution."""


class StrictEvaluationError(RuntimeError):
    """A sanitized strict evaluator lifecycle failure."""


class StrictEvaluationCleanupError(StrictEvaluationError):
    """The only failure was inability to dispose strict evaluator resources."""


class OptimizationSandboxPlatform(Protocol):
    """Narrow Daytona platform surface used by disposable evaluator sandboxes."""

    async def create(
        self,
        *,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
        labels: dict[str, str] | None = None,
        with_volume: bool = True,
        ephemeral: bool = False,
        network_block_all: bool = False,
        network_allow_list: str | None = None,
        domain_allow_list: str | None = None,
        auto_stop_interval: int | None = None,
        auto_delete_interval: int | None = None,
    ) -> Any:
        """
        Create a Daytona sandbox with the requested storage, network, lifecycle, and labeling settings.

        Parameters:
            volume_id (str | None): Identifier of the volume to attach.
            mount_path (str | None): Path where the volume is mounted.
            volume_subpath (str | None): Subdirectory of the volume to mount.
            labels (dict[str, str] | None): Labels to associate with the sandbox.
            with_volume (bool): Whether to attach a volume.
            ephemeral (bool): Whether the sandbox is disposable.
            network_block_all (bool): Whether to block all network access by default.
            network_allow_list (str | None): Network allow-list configuration.
            domain_allow_list (str | None): Domain-based network allow-list configuration.
            auto_stop_interval (int | None): Interval after which the sandbox stops automatically.
            auto_delete_interval (int | None): Interval after which the sandbox is deleted automatically.

        Returns:
            Any: The created sandbox.
        """
        ...

    async def delete(self, sandbox_id: Any) -> None:
        """Delete a disposable optimization sandbox identified by its provider ID."""
        ...


@dataclass(frozen=True, slots=True)
class OptimizationSandboxPolicy:
    """Validated Daytona creation policy for a single untrusted evaluation."""

    snapshot: str
    gateway_domains: tuple[str, ...]
    # Daytona's CIDR and domain allow-list modes are mutually exclusive.  Keep
    # this field only to reject obsolete callers explicitly rather than silently
    # selecting a broader firewall mode.
    gateway_cidrs: tuple[str, ...] = ()
    auto_stop_interval_seconds: int = 300
    # Daytona ignores this setting for ephemeral sandboxes.  Pin its effective
    # value instead of sending a contradictory provider request.
    auto_delete_interval_seconds: int = 0

    def __post_init__(self) -> None:
        """
        Validate and normalize the optimization sandbox policy.

        Raises:
            OptimizationSandboxPolicyError: If the snapshot, gateway domains, network
                restrictions, or lifecycle intervals are invalid.
        """
        if not self.snapshot.strip():
            raise OptimizationSandboxPolicyError("trusted optimization snapshot is required")
        if not self.gateway_domains:
            raise OptimizationSandboxPolicyError("at least one approved gateway domain is required")
        normalized_domains = tuple(sorted({domain.strip().lower() for domain in self.gateway_domains}))
        if any(not _DOMAIN.fullmatch(domain) for domain in normalized_domains):
            raise OptimizationSandboxPolicyError("gateway domains must be bare DNS host names")
        if self.gateway_cidrs:
            raise OptimizationSandboxPolicyError(
                "strict evaluator requires Daytona domain allow-list mode without gateway CIDRs"
            )
        if self.auto_stop_interval_seconds < 1:
            raise OptimizationSandboxPolicyError("positive Daytona auto-stop interval is required")
        if self.auto_delete_interval_seconds != 0:
            raise OptimizationSandboxPolicyError("ephemeral strict evaluator cannot set a Daytona auto-delete interval")
        object.__setattr__(self, "gateway_domains", normalized_domains)
        object.__setattr__(self, "gateway_cidrs", ())

    @property
    def policy_id(self) -> str:
        """Return a stable non-secret identifier for evidence and labels."""
        import json

        encoded = json.dumps(
            {
                "snapshot": self.snapshot,
                "domains": self.gateway_domains,
                "stop": self.auto_stop_interval_seconds,
                "delete": self.auto_delete_interval_seconds,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class DisposableOptimizationSandboxFactory:
    """Create and delete evaluator sandboxes without workspace provisioning."""

    def __init__(self, *, platform: OptimizationSandboxPlatform, sandbox_spec: DaytonaSandboxSpec) -> None:
        self._platform = platform
        self._sandbox_spec = sandbox_spec

    async def create(
        self,
        *,
        policy: OptimizationSandboxPolicy,
        run_id: str,
        candidate_sha256: str,
        record_id: str,
    ) -> Any:
        """Create an ephemeral, no-volume evaluator sandbox using the specified policy and evidence labels.

        Parameters:
            policy (OptimizationSandboxPolicy): Validated sandbox policy to apply.
            run_id (str): Identifier for the evaluation run.
            candidate_sha256 (str): SHA-256 digest of the candidate.
            record_id (str): Identifier for the evaluated record.

        Returns:
            The created sandbox.
        """
        if policy.snapshot != self._sandbox_spec.snapshot:
            raise OptimizationSandboxPolicyError("policy snapshot does not match the configured trusted snapshot")
        labels = {
            "fleet-purpose": "optimization-evaluator",
            "fleet-policy": policy.policy_id[:24],
            "fleet-run": _label_value(run_id),
            "fleet-candidate": _label_value(candidate_sha256),
            "fleet-record": _label_value(record_id),
        }
        return await self._platform.create(
            labels=labels,
            with_volume=False,
            ephemeral=True,
            domain_allow_list=",".join(policy.gateway_domains),
            auto_stop_interval=policy.auto_stop_interval_seconds,
        )

    async def delete(self, sandbox: Any) -> None:
        """Delete the sandbox directly; lifecycle TTL is only a backstop."""
        await self._platform.delete(sandbox)


@dataclass(frozen=True, slots=True)
class StrictEvaluationProof:
    """Non-forgeable-by-candidate prerequisites for strict evaluator execution.

    These values are deliberately supplied by trusted host composition after the
    separate live tests have recorded both provider properties.  The lifecycle
    cannot infer either property from Daytona creation arguments.
    """

    readonly_input_boundary_verified: bool
    gateway_broker_verified: bool
    proof_id: str

    def __post_init__(self) -> None:
        """
        Validate the strict evaluation proof identifier.

        Raises:
            OptimizationSandboxPolicyError: If the proof identifier is missing or invalid.
        """
        if not _label_value(self.proof_id):
            raise OptimizationSandboxPolicyError("strict evaluator proof identifier is required")


@dataclass(frozen=True, slots=True)
class StrictEvaluationModels:
    """Fixed host-owned LMs; candidates cannot select routing or credentials."""

    root_lm: dspy.LM
    sub_lm: dspy.LM


@dataclass(frozen=True, slots=True)
class StrictEvaluationRequest:
    """One candidate-record attempt admitted by the trusted evaluator host."""

    candidate: str
    record: OptimizationRecord
    run_id: str
    attempt: int = 1

    def __post_init__(self) -> None:
        """
        Validate the candidate evaluation request fields.

        Raises:
            OptimizationSandboxPolicyError: If the candidate or run identifier is empty,
                or if the attempt number is less than one.
        """
        if not isinstance(self.candidate, str) or not self.candidate.strip():
            raise OptimizationSandboxPolicyError("candidate instructions must be non-empty text")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise OptimizationSandboxPolicyError("strict evaluator run identifier is required")
        if self.attempt < 1:
            raise OptimizationSandboxPolicyError("strict evaluator attempt must be positive")

    @property
    def candidate_sha256(self) -> str:
        """Return the SHA-256 digest of the candidate text."""
        return hashlib.sha256(self.candidate.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StrictEvaluationResult:
    """Sanitized host-side result; raw candidate, record, and answer are omitted."""

    prediction: PredictionResult
    usage: RLMUsage
    elapsed_ms: int
    candidate_sha256: str
    record_sha256: str
    policy_id: str
    proof_id: str
    curated_input_sha256: str
    curated_input_schema: str
    termination_mode: str


class StrictDaytonaEvaluationLifecycle:
    """Own one fresh strict sandbox, RLM, validation, and direct deletion attempt."""

    def __init__(
        self,
        *,
        factory: DisposableOptimizationSandboxFactory,
        policy: OptimizationSandboxPolicy,
        proof: ValidatedStrictDaytonaProof,
        models: StrictEvaluationModels,
        options: RLMOptions,
        execution_timeout_seconds: int = 60,
        schema_id: str = "fleet.signature-optimization",
        schema_version: str = "1",
    ) -> None:
        """
        Initialize the strict Daytona evaluation lifecycle.

        Parameters:
            factory (DisposableOptimizationSandboxFactory): Factory for creating and deleting evaluation sandboxes.
            policy (OptimizationSandboxPolicy): Sandbox isolation and lifecycle policy.
            proof (ValidatedStrictDaytonaProof): Validated evidence that the required isolation
                and gateway controls are active.
            models (StrictEvaluationModels): Host-selected models used for candidate evaluation.
            options (RLMOptions): Runtime options for the evaluator.
            execution_timeout_seconds (int): Maximum execution time for an evaluation.
            schema_id (str): Identifier for the evaluator output schema.
            schema_version (str): Version of the evaluator output schema.

        Raises:
            OptimizationSandboxPolicyError: If the timeout or schema identity is invalid.
            StrictEvaluationCapabilityError: If the proof is not validated or does not match the configured policy.
        """
        if execution_timeout_seconds < 1:
            raise OptimizationSandboxPolicyError("strict evaluator timeout must be positive")
        if not schema_id.strip() or not schema_version.strip():
            raise OptimizationSandboxPolicyError("strict evaluator schema identity is required")
        self._factory = factory
        self._policy = policy
        if not isinstance(proof, ValidatedStrictDaytonaProof):
            raise StrictEvaluationCapabilityError("strict evaluator requires a validated Daytona proof receipt")
        self._proof = proof
        self._proof.require_matches(
            policy_id=policy.policy_id,
            snapshot=policy.snapshot,
            gateway_domains=policy.gateway_domains,
            auto_stop_interval_seconds=policy.auto_stop_interval_seconds,
            auto_delete_interval_seconds=policy.auto_delete_interval_seconds,
        )
        self._models = models
        self._options = options
        self._execution_timeout_seconds = execution_timeout_seconds
        self._schema_id = schema_id
        self._schema_version = schema_version

    async def evaluate(self, request: StrictEvaluationRequest) -> StrictEvaluationResult:
        """
        Evaluate one candidate in an isolated disposable sandbox.

        Parameters:
            request (StrictEvaluationRequest): Candidate, curated record, run identifier,
                and attempt metadata for the evaluation.

        Returns:
            StrictEvaluationResult: Sanitized prediction and evaluation evidence,
                including usage, timing, digests, policy and proof identifiers, and
                termination mode.

        Raises:
            StrictEvaluationError: If execution times out or produces invalid typed
                output.
            StrictEvaluationCleanupError: If evaluation succeeds but sandbox cleanup
                fails.
        """
        candidate_sha256 = request.candidate_sha256
        _require_sha256(request.record.content_sha256, "record")
        curated_input = CuratedEvaluationStore(candidate=request.candidate, record=request.record)
        handle = curated_input.handle
        reader = curated_input.broker_tool(handle=handle)
        sandbox = await self._factory.create(
            policy=self._policy,
            run_id=request.run_id,
            candidate_sha256=candidate_sha256,
            record_id=request.record.record_id,
        )
        interpreter: DaytonaCodeInterpreter | None = None
        primary_error: BaseException | None = None
        try:
            loop = asyncio.get_running_loop()
            interpreter = DaytonaCodeInterpreter(
                backend=sandbox_backend(
                    sandbox,
                    loop=loop,
                    timeout_s=self._execution_timeout_seconds,
                ),
                tools={"read_curated_input": reader},
                execution_output_cap=self._options.max_output_chars,
            )
            signature = _strict_evaluator_signature()
            tool = dspy.Tool(
                reader,
                name="read_curated_input",
                desc="Read bounded canonical curated evaluation input with the supplied capability handle.",
            )
            rlm = build_native_rlm(
                signature=signature,
                options=self._options,
                tools=(tool,),
                sub_lm=self._models.sub_lm,
                verbose=False,
            )
            kwargs = _strict_named_inputs(handle.public_value())
            started = time.perf_counter()
            try:
                async with asyncio.timeout(self._execution_timeout_seconds):
                    with dspy.context(lm=self._models.root_lm, adapter=dspy.JSONAdapter(), track_usage=True):
                        prediction = await rlm.acall(interpreter, **kwargs)
            except TimeoutError as exc:
                raise StrictEvaluationError("strict evaluator execution timed out") from exc
            elapsed_ms = int((time.perf_counter() - started) * 1_000)
            try:
                validated = prediction_result(
                    prediction,
                    signature,
                    schema_id=self._schema_id,
                    schema_version=self._schema_version,
                    max_output_chars=self._options.max_output_chars,
                )
            except PredictionOutputError as exc:
                raise StrictEvaluationError("strict evaluator returned invalid typed output") from exc
            usage = observed_usage(prediction, duration_ms=elapsed_ms)
            return StrictEvaluationResult(
                prediction=validated,
                usage=usage,
                elapsed_ms=elapsed_ms,
                candidate_sha256=candidate_sha256,
                record_sha256=request.record.content_sha256,
                policy_id=self._policy.policy_id,
                proof_id=self._proof.proof_id,
                curated_input_sha256=curated_input.consume().sha256,
                curated_input_schema=curated_input.receipt.schema,
                termination_mode=_termination_mode(prediction),
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_error = await self._cleanup(interpreter, sandbox)
            if primary_error is None and cleanup_error is not None:
                raise StrictEvaluationCleanupError("strict evaluator sandbox cleanup failed") from cleanup_error

    async def _cleanup(self, interpreter: DaytonaCodeInterpreter | None, sandbox: Any) -> BaseException | None:
        """
        Attempt interpreter shutdown and sandbox deletion, preserving cleanup failures during cancellation.

        Returns:
            BaseException | None: The first cleanup error, or `None` when cleanup succeeds.

        Raises:
            asyncio.CancelledError: If the caller is cancelled while sandbox deletion is pending.
        """
        cleanup_error: BaseException | None = None
        if interpreter is not None:
            try:
                await asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True)
            except BaseException as exc:
                cleanup_error = exc
        delete_effect = OwnedEffect.start(self._factory.delete(sandbox))
        try:
            delete_wait = await delete_effect.settle()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
            cancelled = delete_effect.caller_cancelled
        else:
            cancelled = delete_wait.caller_cancelled
        if cancelled:
            raise asyncio.CancelledError
        return cleanup_error


def _strict_evaluator_signature() -> type[dspy.Signature]:
    """
    Define the fixed interface for strict curated-input evaluation.

    The signature exposes a curated-input capability handle and one typed answer field while
    excluding direct access to task data and external resources.

    Returns:
        type[dspy.Signature]: The strict curated-evaluation signature class.
    """

    class StrictCuratedEvaluationSignature(dspy.Signature):
        """Evaluate the task using only the curated input capability.

        The ``curated_input_handle`` is not the task content. Use
        ``read_curated_input`` with its transaction ID and SHA-256 to inspect
        bounded candidate and record projections. Do not use filesystem,
        environment, workspace, attachments, history, or external tools.
        Return exactly one typed answer through ``SUBMIT(answer=...)``.
        """

        curated_input_handle: dict[str, str | int] = dspy.InputField(
            desc="Capability handle for bounded host-curated evaluation input"
        )
        answer: str = dspy.OutputField(desc="Bounded typed evaluation answer")

    return StrictCuratedEvaluationSignature


def _strict_named_inputs(handle: dict[str, str | int]) -> dict[str, Any]:
    """
    Expose a copied curated-input capability handle for evaluator access.

    Parameters:
        handle (dict[str, str | int]): Public handle identifying the curated-input capability.

    Returns:
        dict[str, Any]: Mapping containing the copied handle under `curated_input_handle`.
    """
    return {"curated_input_handle": dict(handle)}


def _termination_mode(prediction: Any) -> str:
    """
    Determine how the evaluator terminated based on the prediction metadata.

    Parameters:
        prediction (Any): The evaluator prediction to inspect.

    Returns:
        str: `"native_extraction_fallback"` when forced final output extraction was used; `"typed_submit"` otherwise.
    """
    if getattr(prediction, "final_reasoning", None) == "Extract forced final output":
        return "native_extraction_fallback"
    return "typed_submit"


def _require_sha256(value: str, label: str) -> None:
    """Validate that a labeled value is a lowercase SHA-256 digest."""
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise OptimizationSandboxPolicyError(f"strict evaluator {label} digest must be SHA-256")


def _label_value(value: str) -> str:
    """
    Normalize and validate a sandbox evidence label.

    Parameters:
        value (str): Label value to normalize and validate.

    Returns:
        str: Lowercase, trimmed label suitable for sandbox evidence metadata.

    Raises:
        OptimizationSandboxPolicyError: If the label is empty, exceeds 64 characters, or
            contains unsupported characters.
    """
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 64 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", normalized):
        raise OptimizationSandboxPolicyError("sandbox evidence labels must be safe bounded identifiers")
    return normalized


__all__ = [
    "DisposableOptimizationSandboxFactory",
    "OptimizationSandboxPlatform",
    "OptimizationSandboxPolicy",
    "OptimizationSandboxPolicyError",
    "StrictDaytonaEvaluationLifecycle",
    "StrictEvaluationCapabilityError",
    "StrictEvaluationCleanupError",
    "StrictEvaluationError",
    "StrictEvaluationModels",
    "StrictEvaluationProof",
    "StrictEvaluationRequest",
    "StrictEvaluationResult",
]
