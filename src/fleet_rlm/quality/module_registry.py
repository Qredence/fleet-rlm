"""Central registry of optimizable DSPy worker modules.

This registry is the single source of truth consumed by:
- the offline CLI (``fleet-rlm optimize``)
- the API router (``POST /api/v1/optimization/run`` with ``module_slug``)
- the frontend metadata endpoint (``GET /api/v1/optimization/modules``)

Each per-module optimization file registers its ``ModuleOptimizationSpec``
by providing a factory function that the registry invokes lazily on first use.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class MetricProfile:
    """Versioned quality policy owned by one managed optimization target."""

    profile_id: str
    version: str = "1"
    minimum_test_examples: int = 1
    minimum_score_delta: float = 0.0
    maximum_cost_increase_ratio: float = 0.20
    maximum_p95_latency_increase_ratio: float = 0.20
    critical_slices: tuple[str, ...] = ()

    @property
    def qualified_id(self) -> str:
        return f"{self.profile_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class ModuleOptimizationSpec:
    """Describes an optimizable DSPy module for the GEPA offline pipeline."""

    module_slug: str
    label: str
    program_spec: str
    artifact_filename: str
    input_keys: list[str]
    required_dataset_keys: list[str]

    # Lazy callables — avoid heavy imports at registry load time
    module_factory: Callable[[], Any]
    row_converter: Callable[[list[dict[str, Any]]], list[Any]]
    metric_builder: Callable[[], Any]

    metric_name: str = ""
    description: str = ""
    artifact_writer: Callable[[Any, str], dict[str, Any]] | None = None
    instruction_proposer_factory: Callable[[], Any] | None = None
    runtime_module_name: str | None = None
    signature_class_name: str | None = None
    output_keys: list[str] | None = None
    optimization_target_kind: str = "custom"
    target_version: str = "1"
    metric_profile: MetricProfile | None = None
    proposer_policy: str = "dspy_default"
    evaluation_concurrency: int = 1
    artifact_codec: str = "dspy_state_json"
    confidence_aware: bool = False
    hard_gates: tuple[str, ...] = field(default_factory=tuple)


# -- Module registry --------------------------------------------------------

_REGISTRY: dict[str, ModuleOptimizationSpec] = {}
_MODULE_ENTRYPOINTS: tuple[str, ...] = (
    "fleet_rlm.quality.optimize_longcot",
    "fleet_rlm.quality.runtime_signature_optimization",
)


def register_module(spec: ModuleOptimizationSpec) -> None:
    """Register a module specification by slug."""
    _REGISTRY[spec.module_slug] = spec


def get_module_spec(slug: str) -> ModuleOptimizationSpec | None:
    """Look up a registered module by slug.  Returns ``None`` if unknown."""
    _ensure_registered()
    return _REGISTRY.get(slug)


def build_module_with_optional_activation(
    slug: str,
    *,
    active_artifact: object | None = None,
) -> object | None:
    """Build a fresh registered module and optionally apply an activated artifact.

    When ``active_artifact`` is None the factory default is returned unchanged
    (ADR-0006 fail-closed / quality-activation-disabled behavior).
    """
    from fleet_rlm.runtime.active_artifacts import ActiveArtifact, load_module_state

    spec = get_module_spec(slug)
    if spec is None:
        return None
    module = spec.module_factory()
    if active_artifact is None:
        return module
    if not isinstance(active_artifact, ActiveArtifact):
        return module
    return load_module_state(module, active_artifact)


def list_module_slugs() -> list[str]:
    """Return all registered module slugs in sorted order."""
    _ensure_registered()
    return sorted(_REGISTRY)


def list_module_metadata() -> list[dict[str, Any]]:
    """Return lightweight metadata dicts for all registered modules.

    Suitable for serialization to the frontend.
    """
    _ensure_registered()
    return [
        {
            "slug": spec.module_slug,
            "label": spec.label,
            "description": spec.description,
            "program_spec": spec.program_spec,
            "runtime_module_name": spec.runtime_module_name,
            "signature_class_name": spec.signature_class_name,
            "input_keys": spec.input_keys,
            "output_keys": list(spec.output_keys or []),
            "optimization_target_kind": spec.optimization_target_kind,
            "required_dataset_keys": spec.required_dataset_keys,
            "target_version": spec.target_version,
            "metric_profile_id": spec.metric_profile.qualified_id if spec.metric_profile else spec.metric_name,
            "proposer_policy": spec.proposer_policy,
            "evaluation_concurrency": spec.evaluation_concurrency,
            "artifact_codec": spec.artifact_codec,
            "confidence_aware": spec.confidence_aware,
        }
        for spec in sorted(_REGISTRY.values(), key=lambda s: s.module_slug)
    ]


# -- Lazy registration -------------------------------------------------------

_REGISTERED = False


def _ensure_registered() -> None:
    """Import per-module entrypoints to trigger registration on first access."""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    # Each import triggers a module-level ``register_module()`` call.
    for module_name in _MODULE_ENTRYPOINTS:
        try:
            __import__(module_name)
        except Exception as exc:
            sys.stderr.write(f"[fleet_rlm.quality] failed to load {module_name}: {exc}\n")


def _reset_registry() -> None:
    """Reset registration state — for testing only."""
    global _REGISTERED
    _REGISTERED = False
    _REGISTRY.clear()
    for module_name in _MODULE_ENTRYPOINTS:
        sys.modules.pop(module_name, None)
