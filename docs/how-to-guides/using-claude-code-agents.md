# Using with Claude Code (Skills, Sub-Agents, Teams)

This guide covers the "other way" to run `fleet-rlm`: through Claude Code using the bundled scaffold assets in `src/fleet_rlm/_scaffold/`.

## What Gets Installed

`fleet-rlm` ships reusable Claude Code assets:

- Skills: `src/fleet_rlm/_scaffold/skills/`
- Agent definitions: `src/fleet_rlm/_scaffold/agents/`
- Team templates: `src/fleet_rlm/_scaffold/teams/`
- Hooks: `src/fleet_rlm/_scaffold/hooks/`

Install them into `~/.claude/`:

```bash
# from repo root
uv run fleet-rlm init
```

## Install Variants

```bash
# list what is available (no install)
uv run fleet-rlm init --list

# install one category
uv run fleet-rlm init --skills-only
uv run fleet-rlm init --agents-only
uv run fleet-rlm init --teams-only
uv run fleet-rlm init --hooks-only

# install all but skip some categories
uv run fleet-rlm init --no-teams
uv run fleet-rlm init --no-hooks

# overwrite existing files
uv run fleet-rlm init --force
```

## Running via Skills

Once installed, ask Claude Code to use a skill directly in task prompts.

Examples:

- "Use the `rlm` skill to analyze this large document."
- "Use `rlm-debug` to diagnose Modal auth and timeout failures."
- "Use `dspy-signature` to design a new extraction signature."

## Running via Sub-Agents

The packaged sub-agents include:

- `rlm-orchestrator`
- `rlm-specialist`
- `rlm-subcall`
- `modal-interpreter-agent`

Example prompts:

- "Delegate this long-context extraction to `rlm-orchestrator`."
- "Use `rlm-specialist` to optimize this RLM workflow."

## Running via Agent Teams

Team templates are installed under `~/.claude/teams/`.

To use team workflows, enable Claude Code experimental teams:

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

Then run prompts that ask Claude Code to coordinate work through the installed team templates (for example the `fleet-rlm` team).

## Where These Assets Come From

The installation logic is implemented in `src/fleet_rlm/utils/scaffold.py` and exposed through the CLI command `fleet-rlm init`.

## Verify Installation

```bash
ls -la ~/.claude/skills
ls -la ~/.claude/agents
ls -la ~/.claude/teams
ls -la ~/.claude/hooks
```

If needed, run:

```bash
uv run python scripts/validate_agents.py
```
