"""Backwards-compatible re-export shim.

All definitions now live in :mod:`.builders` and :mod:`.registry`.
This module re-exports every public symbol so that existing imports
from ``fleet_rlm.runtime.models.rlm_runtime_modules`` keep working.
"""

import sys as _sys
from typing import Any as _Any
from typing import MutableMapping as _MutableMapping

import dspy  # — kept so patch targets like ``…rlm_runtime_modules.dspy.RLM`` resolve

from fleet_rlm.runtime.models.builders import (
    ClarificationQuestionPlanningModule,
    GroundedAnswerSynthesisModule,
    MemoryActionPlanningModule,
    MemoryMigrationPlanningModule,
    MemoryStructureAuditPlanningModule,
    RuntimeModuleBuildConfig,
    _chunk_document,
    _chunk_to_text,
    _coerce_bounded_int,
    _create_configured_runtime_rlm,
    _MemoryTreePrimedModule,
    _normalize_chunk_strategy,
    build_recursive_subquery_rlm,
    build_runtime_module_config,
    create_runtime_rlm,
)
from fleet_rlm.runtime.models.registry import (
    RUNTIME_MODULE_NAMES,
    RUNTIME_MODULE_REGISTRY,
    RuntimeModuleDefinition,
    _RuntimeModuleFactory,
    _RuntimeSignatureModule,
    _signature_runtime_module_class,
    build_runtime_module,
    runtime_module_class,
)


def get_or_build_runtime_module(
    cache: _MutableMapping[str, dspy.Module],
    name: str,
    *,
    config: RuntimeModuleBuildConfig,
) -> dspy.Module:
    """Return a cached runtime module, building it on first access.

    Thin wrapper so that ``unittest.mock.patch`` on this shim module's
    ``build_runtime_module`` takes effect.
    """
    module = cache.get(name)
    if module is not None:
        return module

    # Resolve through *this* module's namespace so patches land correctly.
    _build: _Any = _sys.modules[__name__].build_runtime_module
    module = _build(
        name,
        interpreter=config.interpreter,
        max_iterations=config.max_iterations,
        max_llm_calls=config.max_llm_calls,
        verbose=config.verbose,
    )
    cache[name] = module
    return module


__all__ = [
    "RUNTIME_MODULE_NAMES",
    "RUNTIME_MODULE_REGISTRY",
    "ClarificationQuestionPlanningModule",
    "GroundedAnswerSynthesisModule",
    "MemoryActionPlanningModule",
    "MemoryMigrationPlanningModule",
    "MemoryStructureAuditPlanningModule",
    "RuntimeModuleBuildConfig",
    "RuntimeModuleDefinition",
    "_MemoryTreePrimedModule",
    "_RuntimeModuleFactory",
    "_RuntimeSignatureModule",
    "_chunk_document",
    "_chunk_to_text",
    "_coerce_bounded_int",
    "_create_configured_runtime_rlm",
    "_normalize_chunk_strategy",
    "_signature_runtime_module_class",
    "build_recursive_subquery_rlm",
    "build_runtime_module",
    "build_runtime_module_config",
    "create_runtime_rlm",
    "get_or_build_runtime_module",
    "runtime_module_class",
]
