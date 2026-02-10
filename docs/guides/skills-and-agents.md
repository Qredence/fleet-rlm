# Skills and Agents Guide

This guide explains how to install and use the Claude scaffold assets bundled with `fleet-rlm`.

## Overview

`fleet-rlm` includes **10 specialized skills**, primary and team-support **agent definitions**, **team templates**, and **hook templates** for RLM workflows. These are packaged with the PyPI distribution and can be installed to your user directory (`~/.claude/`) for use across all projects.

## Prerequisites

⚠️ **Important**: Scaffold assets are workflow definitions only—they don't include credentials. Before using RLM features, you must:

1. **Set up Modal authentication** (per user, one-time):

   ```bash
   uv run modal setup
   ```

2. **Create Modal secrets** for LLM access:

   ```bash
   uv run modal secret create LITELLM \
       DSPY_LM_MODEL=... \
       DSPY_LM_API_BASE=... \
       DSPY_LLM_API_KEY=... \
       DSPY_LM_MAX_TOKENS=...
   ```

3. **Optional**: Create Modal volumes for persistent storage:
   ```bash
   uv run modal volume create rlm-volume-dspy
   ```

See the [Getting Started Guide](../getting-started.md) for detailed setup instructions.

**Why separate?** Modal credentials are tied to your account, not the package. Each user needs their own authentication.

## Installation

### Quick Install

Install to the default location (`~/.claude/`):

```bash
uv run fleet-rlm init
```

### Custom Installation

Install to a custom directory:

```bash
uv run fleet-rlm init --target ~/.config/claude
```

### List Available Content

See what's available before installing:

```bash
uv run fleet-rlm init --list
```

### Force Overwrite

Overwrite existing files (useful for updates):

```bash
uv run fleet-rlm init --force
```

## Available Skills

### Core RLM Skills

| Skill         | Purpose                                             | Files |
| ------------- | --------------------------------------------------- | ----- |
| `rlm`         | Run RLM for long-context tasks with Modal sandboxes | 2     |
| `rlm-run`     | Configure and execute RLM tasks                     | 1     |
| `rlm-execute` | Execute Python code in Modal sandboxes              | 1     |
| `rlm-batch`   | Parallel task execution across sandboxes            | 1     |

### Development & Debugging

| Skill            | Purpose                               | Files |
| ---------------- | ------------------------------------- | ----- |
| `rlm-debug`      | Debug RLM execution and sandbox state | 2     |
| `rlm-test-suite` | Test and evaluate RLM workflows       | 1     |
| `modal-sandbox`  | Manage Modal sandboxes and volumes    | 1     |

### Specialized Skills

| Skill              | Purpose                                         | Files |
| ------------------ | ----------------------------------------------- | ----- |
| `dspy-signature`   | Generate and validate DSPy signatures           | 2     |
| `rlm-memory`       | Long-term memory persistence with Modal volumes | 1     |
| `rlm-long-context` | (EXPERIMENTAL) Research implementation for RLM  | 8     |

## Available Agents

`fleet-rlm init` installs primary agents and additional team-support agent definitions under `~/.claude/agents/teams/`.

### Agent Hierarchy

| Agent                     | Model   | Purpose                          | Max Turns |
| ------------------------- | ------- | -------------------------------- | --------- |
| `rlm-orchestrator`        | inherit | Multi-agent RLM coordination     | 30        |
| `rlm-specialist`          | sonnet  | Complex RLM task execution       | 20        |
| `modal-interpreter-agent` | sonnet  | Direct Modal sandbox interaction | 15        |
| `rlm-subcall`             | haiku   | Lightweight sub-LLM calls        | 3         |

### Agent Patterns

**Orchestration Pattern:**

```
User Request → rlm-orchestrator → rlm-subcall (for llm_query)
                              ↘ rlm-specialist (for complex tasks)
```

**Direct Interaction:**

```
User Request → modal-interpreter-agent → Modal Sandbox
```

## Skills Usage

Once installed, skills are automatically available in Claude. To invoke a skill:

1. **Automatic Invocation**: Claude automatically selects the appropriate skill based on your request
2. **Explicit Reference**: You can mention the skill name to guide Claude's selection

**Example requests that trigger skills:**

- "Debug my RLM execution" → Uses `rlm-debug` skill
- "Run this code in a Modal sandbox" → Uses `rlm-execute` skill
- "Extract architecture from these docs" → Uses `rlm` skill
- "Create a DSPy signature for this task" → Uses `dspy-signature` skill

## Agents Usage

Agents can be invoked via the `@agent` syntax in compatible Claude interfaces:

```
@rlm-orchestrator Analyze this large codebase and extract all API endpoints
```

Or through the Task tool:

```
Task(agent="rlm-specialist", prompt="Extract error patterns from logs")
```

## Package Data Structure

When installed, the structure looks like:

```
~/.claude/
├── skills/
│   ├── dspy-signature/
│   │   ├── SKILL.md
│   │   └── references/
│   ├── modal-sandbox/
│   │   └── SKILL.md
│   ├── rlm/
│   │   ├── SKILL.md
│   │   └── references/
│   ├── rlm-batch/
│   │   └── SKILL.md
│   ├── rlm-debug/
│   │   ├── SKILL.md
│   │   └── scripts/
│   ├── rlm-execute/
│   │   └── SKILL.md
│   ├── rlm-long-context/
│   │   ├── SKILL.md
│   │   ├── references/
│   │   └── scripts/
│   ├── rlm-memory/
│   │   └── SKILL.md
│   ├── rlm-run/
│   │   └── SKILL.md
│   └── rlm-test-suite/
│       └── SKILL.md
├── agents/
│   ├── modal-interpreter-agent.md
│   ├── rlm-orchestrator.md
│   ├── rlm-specialist.md
│   ├── rlm-subcall.md
│   └── teams/
│       ├── agent-designer.md
│       ├── architect-explorer.md
│       ├── fleet-rlm-explorer-team.md
│       ├── testing-analyst.md
│       └── ux-reviewer.md
├── teams/
│   └── fleet-rlm/
│       ├── config.json
│       └── inboxes/
└── hooks/
    ├── README.md
    ├── hookify.fleet-rlm-document-process.local.md
    ├── hookify.fleet-rlm-large-file.local.md
    ├── hookify.fleet-rlm-llm-query-error.local.md
    └── hookify.fleet-rlm-modal-error.local.md
```

## Updating Skills and Agents

When `fleet-rlm` releases new versions with updated scaffold assets:

1. **Upgrade the package:**

   ```bash
   uv sync --upgrade-package fleet-rlm
   ```

2. **Reinstall scaffold assets:**
   ```bash
   uv run fleet-rlm init --force
   ```

The `--force` flag overwrites existing files with the new versions.

## Cross-Project Usage

Once installed to `~/.claude/`, scaffold assets are available in **all your projects**, not just `fleet-rlm`. This enables:

- Using RLM patterns in other codebases
- Reusing debugging and testing workflows
- Applying DSPy signature generation knowledge anywhere
- Leveraging Modal sandbox management across projects
- Reusing team templates and hooks across workflows

## Skill Development

To add new skills or modify existing ones:

1. Edit files in `.claude/skills/` or `.claude/agents/`
2. Sync to the scaffold directory:
   ```bash
   make sync-scaffold
   ```
3. Test locally
4. Commit changes

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for full development guidelines.

## Troubleshooting

### Skills Not Appearing

If skills don't appear in Claude:

1. Verify installation:

   ```bash
   ls -la ~/.claude/skills/
   ```

2. Check permissions:

   ```bash
   chmod -R u+r ~/.claude/
   ```

3. Restart Claude interface

### Agents Not Available

If agents aren't recognized:

1. Verify YAML frontmatter:

   ```bash
   uv run python scripts/validate_agents.py
   ```

2. Check agent file syntax:
   ```bash
   head -20 ~/.claude/agents/rlm-orchestrator.md
   ```

### Modal Authentication Errors

If you get "Modal authentication failed" when running RLM tasks:

1. **Check Modal setup**:

   ```bash
   modal token set
   # Follow the interactive prompts
   ```

2. **Verify secrets exist**:

   ```bash
   modal secret list | grep LITELLM
   ```

3. **Test connection**:
   ```bash
   uv run fleet-rlm check-secret
   ```

**Remember**: Scaffold assets are just files. Modal credentials must be configured separately per user.

### Reinstall from Scratch

```bash
rm -rf ~/.claude/skills ~/.claude/agents ~/.claude/teams ~/.claude/hooks
uv run fleet-rlm init
```

## References

- [Skills Specification](https://docs.anthropic.com/en/docs/build-with-claude/skills)
- [Agent Documentation](https://docs.anthropic.com/en/docs/build-with-claude/agents)
- [CLI Reference](cli-reference.md)
- [Troubleshooting Guide](troubleshooting.md)
