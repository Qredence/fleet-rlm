# CLI Reference

This document describes the CLI surfaces for `fleet-rlm`.

## Entrypoints

There are two command entrypoints:

- **`fleet-rlm`**: Typer-based command group with subcommands for server modes, optimization, and terminal chat
- **`fleet`**: Lightweight launcher for terminal chat and Web UI startup

## `fleet-rlm` Commands

The `fleet-rlm` command provides the primary CLI interface.

```text
Usage: fleet-rlm [OPTIONS] COMMAND [ARGS]...

Run fleet-rlm demos and experimental runtimes.

Commands:
  serve-api   Run the FastAPI server surface (used by `fleet web`).
  optimize    Run DSPy optimization workflows.
  chat        Start standalone in-process interactive terminal chat.
  daytona-smoke  Run a native Daytona smoke validation without invoking an LM.
```

### `fleet-rlm serve-api`

Run the FastAPI HTTP/WebSocket server. This is the backend for the Web UI and is invoked by `fleet web`.

```text
Usage: fleet-rlm serve-api [OPTIONS]

Options:
  --host TEXT      Bind host [default: 127.0.0.1]
  --port INTEGER   Bind port [default: 8000]
  --help           Show this message and exit.
```

**Examples:**

```bash
# Start server on default host:port (127.0.0.1:8000)
uv run fleet-rlm serve-api

# Bind to all interfaces on custom port
uv run fleet-rlm serve-api --host 0.0.0.0 --port 8080
```

### `fleet-rlm chat`

Start standalone in-process interactive terminal chat with the RLM agent.

```text
Usage: fleet-rlm chat [OPTIONS]

Options:
  --docs-path PATH         Optional document path to preload as active context
  --trace / --no-trace     Enable verbose thought/status display
  --trace-mode TEXT        Trace display mode: compact, verbose, or off
  --volume-name TEXT       Optional Daytona volume name for persistent storage
  --help                   Show this message and exit.
```

**Trace Modes:**

- `compact`: Condensed trace output
- `verbose`: Detailed thought/status display
- `off`: Disable trace output

**Examples:**

```bash
# Start interactive chat
uv run fleet-rlm chat

# Preload a document as context
uv run fleet-rlm chat --docs-path ./docs/architecture.md

# Enable verbose trace output
uv run fleet-rlm chat --trace --trace-mode verbose

# Persist chat state to a specific Daytona volume
uv run fleet-rlm chat --volume-name my-volume
```

### `fleet-rlm daytona-smoke`

Run a native Daytona smoke validation without invoking an LM.

```text
Usage: fleet-rlm daytona-smoke [OPTIONS]

Options:
  --repo TEXT   Repository URL to clone into the Daytona sandbox.  [required]
  --ref TEXT    Optional branch or commit SHA to checkout after clone.
  --help        Show this message and exit.
```

**Examples:**

```bash
# Validate the experimental Daytona setup against a repository
uv run fleet-rlm daytona-smoke --repo https://github.com/qredence/fleet-rlm.git --ref main
```

## `fleet` Launcher

The `fleet` command is a lightweight launcher that provides quick access to terminal chat and the Web UI.

```text
usage: fleet [-h] [--docs-path DOCS_PATH] [--trace-mode {compact,verbose,off}]
             [--volume-name VOLUME_NAME]
             [{web}]

Start standalone fleet interactive chat. Hydra overrides are supported as
key=value tokens. Use 'fleet web' to launch the Web UI server.

positional arguments:
  {web}                 Optional subcommand (e.g., 'web' to launch the Web UI).

options:
  -h, --help            show this help message and exit
  --docs-path DOCS_PATH
                        Optional document path to preload into the chat session.
  --trace-mode {compact,verbose,off}
                        Trace display mode.
  --volume-name VOLUME_NAME
                        Daytona volume name for persistent storage.
```

### `fleet` (Terminal Chat)

When run without arguments, `fleet` starts standalone terminal chat.

```bash
# Start terminal chat
uv run fleet

# Start with document context
uv run fleet --docs-path ./README.md

# Start with verbose tracing
uv run fleet --trace-mode verbose

# Start with a custom Daytona volume
uv run fleet --volume-name my-volume
```

### `fleet web`

The `web` subcommand launches the Web UI server.

```bash
# Start Web UI on default port (127.0.0.1:8000)
uv run fleet web
```

The Web UI will be available at `http://127.0.0.1:8000`. The launcher delegates to `fleet-rlm serve-api --host 0.0.0.0 --port 8000` while preserving Hydra overrides.

## Hydra Overrides

Both `fleet` and `fleet-rlm serve-api` support Hydra-style configuration overrides as trailing `key=value` tokens. This allows runtime configuration changes:

```bash
# Example: override runtime settings
uv run fleet web dspy_lm_model=gpt-4

# Example: override runtime settings
uv run fleet volume_name=my-custom-volume
```

## See Also

- [Installation](../how-to-guides/installation.md) — Setup and dependency installation
- [Runtime Settings](../how-to-guides/runtime-settings.md) — Configuring LLM models and runtime behavior
