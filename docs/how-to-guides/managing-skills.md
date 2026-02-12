# Managing Skills & Agents

`fleet-rlm` comes bundled with a comprehensive suite of **Claude Skills, Agents, and Team Templates**. These "scaffold assets" provide your AI assistant with the domain knowledge needed to operate the RLM system effectively.

## The Hierarchy

Crucially, these assets are not just a flat list. They form a three-tier architecture that works in synergy:

1.  **Skills (Tier 1)**: Specialized knowledge modules (e.g., "How to debug Modal", "How to design DSPy signatures"). These are the foundation.
2.  **Subagents (Tier 2)**: Specialized workers with isolated context that _use_ the skills (e.g., `rlm-specialist` loads `rlm-debug`).
3.  **Agent Teams (Tier 3)**: Collaborative groups of agents working together (experimental).

## Installation

The assets are distributed with the Python package but must be "installed" into your local Claude environment (`~/.claude/`) to be visible to the AI.

To install or update them:

```bash
uv run fleet-rlm init
```

Use `--force` to overwrite existing files if you've upgraded the package.

## Available Skills

| Skill                | Purpose           | When to Use                                 |
| :------------------- | :---------------- | :------------------------------------------ |
| **`rlm`**            | Core RLM Workflow | Processing large files, long-context tasks. |
| **`rlm-debug`**      | Diagnostics       | Fix sandbox timeouts, auth errors.          |
| **`rlm-run`**        | Execution Config  | Setting up `ModalInterpreter`.              |
| **`rlm-memory`**     | Persistence       | Saving data to `/data/memory`.              |
| **`dspy-signature`** | Schema Design     | Writing new DSPy signatures.                |
| **`modal-sandbox`**  | Cloud Admin       | Managing the sandbox environment.           |

> Note: The `rlm` skill includes PDF/document ingestion defaults via ReAct
> tools (`load_document`, `read_file_slice`) using MarkItDown with pypdf
> fallback. Scanned PDFs require OCR before analysis.

## Workflow Patterns

### 1. Automatic Skill Invocation

You generally don't need to ask for a skill explicitly.

> **User**: "My sandbox is timing out."
> **Claude**: _Detects intent_ -> _Loads `rlm-debug`_ -> _Runs diagnostics_.

### 2. Using Subagents

For complex tasks, delegate to a specialized agent.

```text
@rlm-orchestrator Analyze this repository structure.
```

The orchestrator will:

1.  Load the `rlm` and `rlm-execute` skills.
2.  Spawn `rlm-subcall` workers to analyze individual files.
3.  Synthesize the results.

### 3. Agent Teams (Experimental)

Enable teams with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.

> "Create a team to review this PR. One security expert, one performance analyst."

The `fleet-rlm` team templates ensure that these teammates automatically inherit the relevant skills (like `rlm-test-suite` for the reviewer).

## Troubleshooting

### Skills not loading?

Check if the files exist in your home directory:

```bash
ls -l ~/.claude/skills/
```

### Agents not found?

If `@rlm-orchestrator` doesn't work, validate your agent definitions:

```bash
uv run python scripts/validate_agents.py
```
