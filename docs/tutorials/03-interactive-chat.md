# Tutorial 03: Terminal Chat

This tutorial covers the terminal chat interface for Fleet-RLM. You'll learn how to start interactive sessions, use command-line options, and work with the built-in slash commands.

## Prerequisites

Before starting this tutorial, ensure you have:

- Fleet-RLM installed (see [Quick Start Guide](01-basic-usage.md))
- Daytona runtime configured (`DAYTONA_API_KEY`, `DAYTONA_API_URL`)
- LLM provider configured in `.env`

## Starting Terminal Chat

The `fleet` command is the primary entry point for terminal-based interactive chat:

```bash
fleet
```

This launches an interactive session where you can converse with the Fleet-RLM agent.

### Alternative: Using fleet-rlm chat

You can also use the explicit chat subcommand:

```bash
fleet-rlm chat
```

Both commands start a terminal chat session. The `fleet` launcher is recommended as the primary entry point.

## Command-Line Options

### Preloading Documents with --docs-path

The `--docs-path` option loads a document into the agent's context before starting the session:

```bash
# Load a single document
fleet --docs-path README.md

# Load documentation for context
fleet --docs-path docs/architecture.md
```

This is useful when you want to ask questions about a specific file or document throughout your session.

### Controlling Trace Output with --trace-mode

The `--trace-mode` option controls how much internal reasoning is displayed:

```bash
# Condensed trace output (default)
fleet --trace-mode compact

# Full thought and status display
fleet --trace-mode verbose

# Disable trace output entirely
fleet --trace-mode off
```

| Mode | Description |
|------|-------------|
| `compact` | Condensed trace output (default). Shows key steps without full detail. |
| `verbose` | Full thought/status display. Shows detailed reasoning process. |
| `off` | Disable trace output. Shows only final responses. |

### Persistent Volume Configuration

For persistence across sessions, you can specify a Daytona volume name:

```bash
fleet --volume-name my-volume
```

These options are useful when:
- Running multiple sessions with shared state
- Targeting a specific Daytona-backed workspace volume
- Persisting data between sessions

## Slash Commands

During a terminal chat session, use these slash commands for additional functionality:

| Command | Description |
|---------|-------------|
| `/help` | Display available commands and usage information |
| `/status` | Show current session status and configuration |
| `/settings` | View or modify runtime settings |
| `/docs <path> [alias]` | Load a document into context with an optional alias |
| `/analyze <query>` | Run an analysis query on loaded documents |
| `/summarize <focus>` | Generate a summary focused on a specific topic |
| `/exit` | End the chat session |

### Example Session

```bash
$ fleet --docs-path README.md --trace-mode compact
```

```text
> What is the main architecture of this project?

[trace: loading README.md into context...]
[trace: analyzing structure...]

The Fleet-RLM project uses a modular architecture with:
- Core execution layer (Modal sandbox)
- ReAct agent orchestration (DSPy)
- WebSocket streaming for real-time updates

> /docs docs/architecture.md

[Loaded: docs/architecture.md]

> How does the sandbox execution work?

[trace: searching documentation...]

The sandbox uses Modal for isolated Python execution...
```

## Starting the Web UI

For a graphical interface, use the `web` subcommand:

```bash
fleet web
```

This starts the web server at `http://localhost:8000`. See the [Quick Start Guide](01-basic-usage.md) for more details on the Web UI.

## Common Workflows

### Document Analysis Session

Start a session with a document preloaded and analyze it:

```bash
fleet --docs-path docs/architecture.md --trace-mode verbose

> /analyze What are the main components?

> /summarize data flow
```

### Quick Question Session

For quick questions without trace noise:

```bash
fleet --trace-mode off

> What is Fleet-RLM?

> How do I configure Modal?
```

### Debugging Session

When troubleshooting or learning the system:

```bash
fleet --trace-mode verbose --docs-path README.md

> Why is my agent not finding the file?
```

## Troubleshooting

### "Daytona configuration missing"

Set your Daytona API key before starting chat:

```bash
export DAYTONA_API_KEY=your-daytona-api-key
```

### "DSPY_LM_MODEL not set"

Create or update your `.env` file:

```bash
echo "DSPY_LM_MODEL=openai/gpt-4o-mini" >> .env
echo "DSPY_LLM_API_KEY=your-api-key" >> .env
```

### Trace Output Too Verbose

Switch to compact mode or disable entirely:

```bash
fleet --trace-mode compact  # Condensed output
fleet --trace-mode off      # No trace output
```

### Document Not Loading

Verify the file path exists and is readable:

```bash
ls -la path/to/your/document.md
```

Use absolute paths if relative paths don't work:

```bash
fleet --docs-path /full/path/to/document.md
```

### Session Hangs on Startup

Check your Daytona and LLM configuration:

1. Verify `DAYTONA_API_KEY` is set
2. Verify LLM API key is valid
3. Check network connectivity

## Next Steps

- [Document Analysis](02-doc-analysis.md) — Analyze documents programmatically
- [Installation Guide](../how-to-guides/installation.md) — Full installation details
- [CLI Reference](../reference/cli.md) — Complete CLI command reference
- [Troubleshooting](../how-to-guides/troubleshooting.md) — Common issues and solutions
