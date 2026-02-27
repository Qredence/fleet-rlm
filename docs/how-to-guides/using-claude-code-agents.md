# Using with Claude Code

This guide covers using `fleet-rlm` scaffold assets with Claude Code.

## What Gets Installed

`fleet-rlm` ships reusable assets under `src/fleet_rlm/_scaffold/`:

- Skills: `skills/`
- Agent definitions: `agents/`
- Team templates: `teams/`
- Hooks: `hooks/`

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

- "Use the `rlm` skill to analyze this large document."
- "Use `rlm-debug` to diagnose Modal auth and timeout failures."
- "Use `dspy-signature` to design a new extraction signature."
- "Delegate this long-context extraction to `rlm-orchestrator`."

The bundled `rlm` skill includes PDF/document ingestion guidance via
`load_document` and `read_file_slice` (MarkItDown first, `pypdf` fallback).
Scanned/image-only PDFs require OCR before analysis.

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
uv run python scripts/validate_agents.py
```
