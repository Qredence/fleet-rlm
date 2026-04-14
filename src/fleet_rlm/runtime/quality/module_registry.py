"""Central registry of optimizable DSPy worker modules.

This registry is the single source of truth consumed by:
- the offline CLI (``fleet-rlm optimize``)
- the API router (``POST /api/v1/optimization/run`` with ``module_slug``)
- the frontend metadata endpoint (``GET /api/v1/optimization/modules``)

Each per-module optimization file registers its ``ModuleOptimizationSpec``
by providing a factory function that the registry invokes lazily on first use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
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


# -- Module registry --------------------------------------------------------

_REGISTRY: dict[str, ModuleOptimizationSpec] = {}


def register_module(spec: ModuleOptimizationSpec) -> None:
    """Register a module specification by slug."""
    _REGISTRY[spec.module_slug] = spec


def get_module_spec(slug: str) -> ModuleOptimizationSpec | None:
    """Look up a registered module by slug.  Returns ``None`` if unknown."""
    _ensure_registered()
    return _REGISTRY.get(slug)


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
            "required_dataset_keys": spec.required_dataset_keys,
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
    try:
        from . import optimize_reflect_and_revise as _m1  # noqa: F401
    except Exception:
        pass
    try:
        from . import optimize_recursive_context_selection as _m2  # noqa: F401
    except Exception:
        pass
    try:
        from . import optimize_recursive_decomposition as _m3  # noqa: F401
    except Exception:
        pass
    try:
        from . import optimize_recursive_repair as _m4  # noqa: F401
    except Exception:
        pass
    try:
        from . import optimize_recursive_verification as _m5  # noqa: F401
    except Exception:
        pass


def _reset_registry() -> None:
    """Reset registration state — for testing only."""
    global _REGISTERED
    _REGISTERED = False
    _REGISTRY.clear()
