"""Shared optimization spec resolution and execution dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from fleet_rlm.quality import module_registry, optimization_runner
from fleet_rlm.quality.module_registry import ModuleOptimizationSpec
from fleet_rlm.quality.skill_optimization import spec_for_skill


def resolve_optimization_spec(
    *,
    module_slug: str | None,
    skill_name: str | None,
    skill_path: str | None,
    program_spec: str,
    trace_bundle_paths: list[str] | None = None,
) -> ModuleOptimizationSpec:
    """Resolve the optimization target spec from request fields."""
    if skill_name or skill_path:
        return spec_for_skill(
            skill_name=skill_name,
            skill_path=skill_path,
            trace_bundle_paths=trace_bundle_paths,
        )
    if module_slug:
        spec = module_registry.get_module_spec(module_slug)
        if spec is None:
            raise ValueError(f"Unknown module slug: {module_slug!r}")
        return spec
    return optimization_runner.spec_for_program(program_spec)


def run_optimization_for_spec(
    spec: ModuleOptimizationSpec,
    *,
    dataset_path: Path,
    output_path: Path | None,
    default_output_root: Path | None,
    auto: Literal["light", "medium", "heavy"],
    train_ratio: float,
    optimizer: optimization_runner.OptimizerName,
    run_id: int | None = None,
    max_metric_calls: int | None = None,
    trace_bundle_paths: list[str] | None = None,
    reflection_lm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the unified optimization pipeline for a resolved spec."""
    return cast(
        dict[str, Any],
        optimization_runner.run_module_optimization(
            spec,
            dataset_path=dataset_path,
            output_path=output_path,
            default_output_root=default_output_root,
            train_ratio=train_ratio,
            auto=auto,
            max_metric_calls=max_metric_calls,
            optimizer=optimizer,
            run_id=run_id,
            reflection_lm_config=reflection_lm_config,
            trace_bundle_paths=trace_bundle_paths,
        ),
    )


__all__ = ["resolve_optimization_spec", "run_optimization_for_spec"]
