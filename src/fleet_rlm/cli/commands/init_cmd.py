"""Init command for bootstrapping Claude Code scaffold assets.

This module provides a registration function for the init command which
copies bundled RLM skills, agents, teams, and hooks from the installed
fleet-rlm package to the user's ~/.claude/ directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import typer

import fleet_rlm.scaffold as scaffold


def register_init_command(
    app: typer.Typer,
    *,
    _handle_error: Callable[[Exception], None],
) -> None:
    """Register the init CLI command on *app*.

    Args:
        app: Typer app instance to register commands on.
        _handle_error: Error handling callback.
    """

    @app.command("init")
    def init(
        target: Path | None = typer.Option(
            None,
            help="Target directory (defaults to ~/.claude)",
        ),
        force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
        skills_only: bool = typer.Option(
            False, "--skills-only", help="Install only skills, not agents"
        ),
        agents_only: bool = typer.Option(
            False, "--agents-only", help="Install only agents, not skills"
        ),
        teams_only: bool = typer.Option(
            False, "--teams-only", help="Install only team templates"
        ),
        hooks_only: bool = typer.Option(
            False, "--hooks-only", help="Install only hook templates"
        ),
        no_teams: bool = typer.Option(
            False, "--no-teams", help="Skip installing team templates"
        ),
        no_hooks: bool = typer.Option(
            False, "--no-hooks", help="Skip installing hook templates"
        ),
        list_available: bool = typer.Option(
            False, "--list", help="List available scaffold assets (no install)"
        ),
    ) -> None:
        """Bootstrap Claude Code scaffold assets to user-level directory.

        Copies the bundled RLM skills, agents, teams, and hooks from the installed
        fleet-rlm package to ~/.claude/ (or a custom target).
        """
        try:
            # Default to ~/.claude if no target specified
            if target is None:
                target = Path.home() / ".claude"

            # List mode: just show what's available
            if list_available:
                typer.echo("Available Skills:")
                for skill in scaffold.list_skills():
                    typer.echo(
                        f"  - {skill['name']}: {skill['description']} ({skill['files']} files)"
                    )
                typer.echo("\nAvailable Agents:")
                for agent in scaffold.list_agents():
                    typer.echo(
                        f"  - {agent['name']}: {agent['description']} "
                        f"(model: {agent['model']})"
                    )
                typer.echo("\nAvailable Teams:")
                for team in scaffold.list_teams():
                    typer.echo(
                        f"  - {team['name']}: {team['description']} ({team['files']} files)"
                    )
                typer.echo("\nAvailable Hooks:")
                for hook in scaffold.list_hooks():
                    event = f", event: {hook['event']}" if hook["event"] else ""
                    typer.echo(f"  - {hook['name']}: {hook['description']}{event}")
                return

            # Install mode
            only_modes = [
                ("skills", skills_only),
                ("agents", agents_only),
                ("teams", teams_only),
                ("hooks", hooks_only),
            ]
            active_only_modes = [name for name, enabled in only_modes if enabled]

            if len(active_only_modes) > 1:
                typer.echo(
                    "Error: Only one --*-only mode can be specified at a time.",
                    err=True,
                )
                raise typer.Exit(code=1)

            if active_only_modes and (no_teams or no_hooks):
                typer.echo(
                    "Error: --*-only modes cannot be combined with --no-teams/--no-hooks.",
                    err=True,
                )
                raise typer.Exit(code=1)

            if agents_only:
                installed = scaffold.install_agents(target, force=force)
                total = scaffold.list_agents()
                typer.echo(
                    f"Installed {len(installed)} of {len(total)} agents to {target}/agents/"
                )
            elif skills_only:
                installed = scaffold.install_skills(target, force=force)
                total = scaffold.list_skills()
                typer.echo(
                    f"Installed {len(installed)} of {len(total)} skills to {target}/skills/"
                )
            elif teams_only:
                installed = scaffold.install_teams(target, force=force)
                total = scaffold.list_teams()
                typer.echo(
                    f"Installed {len(installed)} of {len(total)} teams to {target}/teams/"
                )
            elif hooks_only:
                installed = scaffold.install_hooks(target, force=force)
                total = scaffold.list_hooks()
                typer.echo(
                    f"Installed {len(installed)} of {len(total)} hooks to {target}/hooks/"
                )
            else:
                # Install all categories (with optional exclusions).
                result = scaffold.install_all(
                    target,
                    force=force,
                    include_teams=not no_teams,
                    include_hooks=not no_hooks,
                )

                summary_parts = [
                    f"{len(result['skills_installed'])} of {result['skills_total']} skills",
                    f"{len(result['agents_installed'])} of {result['agents_total']} agents",
                ]
                if not no_teams:
                    summary_parts.append(
                        f"{len(result['teams_installed'])} of {result['teams_total']} teams"
                    )
                if not no_hooks:
                    summary_parts.append(
                        f"{len(result['hooks_installed'])} of {result['hooks_total']} hooks"
                    )

                typer.echo(f"Installed {', '.join(summary_parts)} to {target}/")
                if result["skills_installed"]:
                    typer.echo(f"  Skills: {', '.join(result['skills_installed'])}")
                if result["agents_installed"]:
                    typer.echo(f"  Agents: {', '.join(result['agents_installed'])}")
                if not no_teams and result["teams_installed"]:
                    typer.echo(f"  Teams: {', '.join(result['teams_installed'])}")
                if not no_hooks and result["hooks_installed"]:
                    typer.echo(f"  Hooks: {', '.join(result['hooks_installed'])}")

                total_skipped = (
                    result["skills_total"]
                    - len(result["skills_installed"])
                    + result["agents_total"]
                    - len(result["agents_installed"])
                )
                if not no_teams:
                    total_skipped += result["teams_total"] - len(
                        result["teams_installed"]
                    )
                if not no_hooks:
                    total_skipped += result["hooks_total"] - len(
                        result["hooks_installed"]
                    )
                if total_skipped > 0:
                    typer.echo(
                        f"  Skipped {total_skipped} existing (use --force to overwrite)"
                    )

        except Exception as exc:
            _handle_error(exc)
