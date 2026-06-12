"""CLI command for running offline prompt optimization."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Optional, cast

import typer

from fleet_rlm.quality import module_registry, optimization_runner
from fleet_rlm.quality.skill_optimization import spec_for_skill


def optimize_command(
    module: str = typer.Argument(
        help="Registered module slug to optimize (use 'list' to see available modules).",
    ),
    dataset: Optional[Path] = typer.Argument(
        None,
        help="Path to JSON or JSONL dataset.",
    ),
    output_path: Optional[Path] = typer.Option(
        None,
        "--output-path",
        "-o",
        help="Where to save the optimized DSPy module artifact.",
    ),
    train_ratio: float = typer.Option(
        0.8,
        "--train-ratio",
        help="Training split ratio for GEPA compilation.",
    ),
    auto: str = typer.Option(
        "light",
        "--auto",
        help="Optimization intensity (light, medium, heavy).",
    ),
    skill_name: str | None = typer.Option(
        None,
        "--skill-name",
        help="Optimize a bundled or mounted Fleet skill by name instead of a registered module.",
    ),
    skill_path: Path | None = typer.Option(
        None,
        "--skill-path",
        help="Optimize a SKILL.md-compatible markdown file instead of a registered module.",
    ),
    trace_bundle_path: list[str] = typer.Option(
        [],
        "--trace-bundle-path",
        help="Offline trace bundle path available to the RLM-GEPA instruction proposer.",
    ),
    report: bool = typer.Option(
        False,
        "--report",
        help="Print a markdown report summary after optimization.",
    ),
) -> None:
    """Run offline prompt optimization for a registered DSPy module."""
    if auto not in ("light", "medium", "heavy"):
        typer.echo(f"Error: --auto must be light, medium, or heavy, got {auto!r}", err=True)
        raise typer.Exit(code=1)
    if skill_name and skill_path:
        typer.echo("Error: provide either --skill-name or --skill-path, not both.", err=True)
        raise typer.Exit(code=1)

    if module == "list":
        slugs = module_registry.list_module_slugs()
        typer.echo("Available modules:")
        for slug in slugs:
            typer.echo(f"  - {slug}")
        raise typer.Exit()

    if dataset is None:
        typer.echo("Error: DATASET is required unless MODULE is 'list'.", err=True)
        raise typer.Exit(code=1)
    if not dataset.exists():
        typer.echo(f"Error: Dataset file not found: {dataset}", err=True)
        raise typer.Exit(code=1)
    if not os.access(dataset, os.R_OK):
        typer.echo(f"Error: Dataset file is not readable: {dataset}", err=True)
        raise typer.Exit(code=1)

    if skill_name or skill_path:
        spec = spec_for_skill(
            skill_name=skill_name,
            skill_path=skill_path,
            trace_bundle_paths=trace_bundle_path,
        )
    else:
        spec = module_registry.get_module_spec(module)
        if spec is None:
            slugs = module_registry.list_module_slugs()
            typer.echo(f"Error: Unknown module slug {module!r}.", err=True)
            typer.echo(f"Available modules: {', '.join(slugs)}", err=True)
            raise typer.Exit(code=1)

    result = dict(
        optimization_runner.run_module_optimization(
            spec,
            dataset_path=dataset,
            output_path=output_path,
            train_ratio=train_ratio,
            auto=cast("Literal['light', 'medium', 'heavy']", auto),
            optimizer="gepa",
        )
    )

    if report:
        _print_report(module, result)
    else:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))


def _print_report(module_slug: str, result: dict) -> None:
    """Print a markdown-style report summary."""
    lines = [
        f"## Optimization Report: {module_slug}",
        "",
        f"- **Module:** {module_slug}",
        f"- **Program Spec:** {result.get('program_spec', 'N/A')}",
        f"- **Optimizer:** {result.get('optimizer', 'N/A')}",
        f"- **Train Examples:** {result.get('train_examples', 0)}",
        f"- **Validation Examples:** {result.get('validation_examples', 0)}",
        f"- **Validation Score:** {result.get('validation_score', 'N/A')}",
        f"- **Output Path:** {result.get('output_path', 'N/A')}",
        f"- **Manifest Path:** {result.get('manifest_path', 'N/A')}",
    ]
    typer.echo("\n".join(lines))
