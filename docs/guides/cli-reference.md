# CLI Reference

The `fleet-rlm` application uses [Typer](https://typer.tiangolo.com/) to provide a rich CLI experience.

## General Usage

```bash
uv run fleet-rlm [COMMAND] [OPTIONS]
```

## Commands

### Setup Commands

| Command | Description                                                 | Key Options                                                                                                                                                                                                                                                    |
| :------ | :---------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `init`  | Install custom Claude scaffold assets (skills/agents/teams/hooks). | `--target`: Install directory (default: ~/.claude)<br>`--force`: Overwrite existing files<br>`--list`: List available content<br>`--skills-only`/`--agents-only`/`--teams-only`/`--hooks-only`: Install one asset class<br>`--no-teams`/`--no-hooks`: Skip optional classes |

### RLM Execution Commands

| Command              | Description                                                    | Key Options                                                                                         |
| :------------------- | :------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------- |
| `run-basic`          | Run a simple Q&A task with code generation.                    | `--question`: The prompt<br>`--volume-name`: Optional persistent volume                             |
| `run-architecture`   | Extract architecture/concepts from a document.                 | `--docs-path`: Path to text file<br>`--query`: Info to extract                                      |
| `run-api-endpoints`  | Extract API endpoints from documentation.                      | `--docs-path`: Path to text file                                                                    |
| `run-error-patterns` | Analyze error patterns in documentation.                       | `--docs-path`: Path to text file                                                                    |
| `run-trajectory`     | Inspect the full execution history (trajectory) of an RLM run. | `--docs-path`: Path to text file<br>`--chars`: Limit input size                                     |
| `run-custom-tool`    | Demonstrate usage of custom tools (e.g., Regex).               | `--docs-path`: Path to text file                                                                    |
| `run-long-context`   | Analyze or summarize long documents with sandbox helpers.      | `--docs-path`: Path to text file<br>`--query`: Analysis query<br>`--mode`: `analyze` or `summarize` |

### Utility Commands

| Command            | Description                                      | Key Options                        |
| :----------------- | :----------------------------------------------- | :--------------------------------- |
| `check-secret`     | Check if Modal secrets are correctly configured. |                                    |
| `check-secret-key` | Inspect a specific secret key value.             | `--key`: The env var name to check |

## Common Options

### `--volume-name`

All `run-*` commands support this option.

- **Usage**: `--volume-name my-data-vol`
- **Effect**: Mounts the specified Modal Volume to `/data/` inside the sandbox.
- **Requirement**: The volume must be created first via `modal volume create`.

### `--docs-path`

Required for analysis commands.

- **Usage**: `--docs-path path/to/file.txt`
- **Effect**: Reads the local file and uploads/makes it available to the sandbox context.

## `init` Command Details

The `init` command installs bundled Claude scaffold assets from `fleet-rlm` to your file system.

### Basic Usage

```bash
# Install to default location (~/.claude/)
uv run fleet-rlm init

# List available content without installing
uv run fleet-rlm init --list

# Install to custom directory
uv run fleet-rlm init --target ~/.config/claude

# Force overwrite existing files
uv run fleet-rlm init --force
```

### What Gets Installed

- **10 Skills**: Workflow patterns, debugging, testing, execution helpers
- **Agent definitions**: Primary agents plus supporting team agent definitions under `agents/teams/`
- **1 Team template**: Team config and inbox templates under `teams/`
- **5 Hook templates**: Prompt hooks under `hooks/`

### Installation Structure

```
~/.claude/
├── skills/
│   ├── dspy-signature/
│   ├── modal-sandbox/
│   ├── rlm/
│   ├── rlm-batch/
│   ├── rlm-debug/
│   ├── rlm-execute/
│   ├── rlm-long-context/
│   ├── rlm-memory/
│   ├── rlm-run/
│   └── rlm-test-suite/
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

See the [Skills and Agents Guide](skills-and-agents.md) for detailed usage information.

## Help

For any command, you can append `--help` to see the full list of arguments and options.

```bash
uv run fleet-rlm run-architecture --help
uv run fleet-rlm init --help
```

## `run-long-context` Details

The `run-long-context` command loads a document into the sandbox and leverages injected helpers so the RLM can explore it programmatically.

### Modes

- **`analyze`** (default): Uses the `AnalyzeLongDocument` signature. The RLM navigates, queries, and synthesizes findings.
- **`summarize`**: Uses the `SummarizeLongDocument` signature. The RLM chunks the document, queries each chunk, and merges summaries.

### Examples

```bash
# Analyze a large document
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "What are the main design decisions?" \
    --mode analyze

# Summarize with a specific focus
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "DSPy optimizers" \
    --mode summarize

# With persistent volume
uv run fleet-rlm run-long-context \
    --docs-path rlm_content/dspy-knowledge/dspy-doc.txt \
    --query "Architecture overview" \
    --mode analyze \
    --volume-name rlm-volume-dspy
```

### Available Sandbox Helpers

Inside the sandbox, the Planner's generated code has access to:

| Helper                                    | Description                                               |
| :---------------------------------------- | :-------------------------------------------------------- |
| `peek(text, start=0, length=2000)`        | Inspect a slice of a large document.                      |
| `grep(text, pattern, context=0)`          | Case-insensitive line search with optional context lines. |
| `chunk_by_size(text, size=200_000, overlap=0)` | Fixed-size chunking.                                 |
| `chunk_by_headers(text, pattern=r"^#{1,3} ", flags=re.MULTILINE)` | Header-based section splitting.             |
| `add_buffer(name, value)`                 | Accumulate values across iterations.                      |
| `get_buffer(name)` / `clear_buffer(name)` | Retrieve or clear accumulated values.                     |
| `save_to_volume(path, content)`           | Persist data to a mounted volume.                         |
| `load_from_volume(path)`                  | Load persisted data from a mounted volume.                |
