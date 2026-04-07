# Using with Claude Code

This guide covers using the packaged Claude Code scaffold as an alternative
interface over `fleet-rlm`.

## What Gets Installed

`fleet-rlm` ships curated assets under `src/fleet_rlm/scaffold/`:

- Skills: `skills/`
- Agent definitions: `agents/`
- Team templates: `teams/`
- Hooks: `hooks/`

These assets are maintained as a Claude-facing translation of the project:

- `rlm` explains the shared ReAct plus `dspy.RLM` runtime
- `daytona-runtime` explains the Daytona-backed path
- `rlm-debug` covers runtime and contract debugging
- the packaged agents coordinate that knowledge for Claude Code users

Install to `~/.claude/`:

```bash
# from repo root
uv run fleet-rlm init
```

## Installation Variants

```bash
# list available assets (no install)
uv run fleet-rlm init --list

# install specific categories
uv run fleet-rlm init --skills-only
uv run fleet-rlm init --agents-only
uv run fleet-rlm init --teams-only
uv run fleet-rlm init --hooks-only

# install all but skip categories
uv run fleet-rlm init --no-teams
uv run fleet-rlm init --no-hooks

# overwrite existing files
uv run fleet-rlm init --force
```

## Skills, Sub-Agents, and Teams

The scaffold follows a three-layer model:

1. Skills: reusable domain knowledge and workflows
2. Sub-agents: specialized workers that consume skills
3. Team templates: multi-agent coordination presets

Typical usage examples:

- "Load `rlm` and explain how this should run in `daytona_pilot`."
- "Load `daytona-runtime` and explain how the Daytona workbench path persists memory."
- "Use `rlm-debug` to diagnose websocket or runtime-mode drift."
- "Delegate this fleet-rlm workspace task to `rlm-orchestrator`."

The packaged scaffold is curated project guidance. It is not meant to be a raw
mirror of the local `.claude/` directory.

## Agent Team Workflows (Experimental)

Enable teams:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Then prompt Claude Code to coordinate via installed team templates.

## Verify Installation

```bash
ls -la ~/.claude/skills
ls -la ~/.claude/agents
ls -la ~/.claude/teams
ls -la ~/.claude/hooks
```

If needed:

```bash
uv run fleet-rlm init --list
```
