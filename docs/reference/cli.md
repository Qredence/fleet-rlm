# CLI Reference

This document describes the CLI surfaces for `fleet-rlm`.

## Entrypoints

There are two command entrypoints:

- **`fleet-rlm`**: Typer-based command group with subcommands for initialization, server modes, and terminal chat
- **`fleet`**: Lightweight launcher for terminal chat and Web UI startup

## `fleet-rlm` Commands

The `fleet-rlm` command provides the primary CLI interface.

```text
Usage: fleet-rlm [OPTIONS] COMMAND [ARGS]...

Run fleet-rlm demos and experimental runtimes.

Commands:
  init        Bootstrap Claude Code scaffold assets to user-level directory.
  serve-api   Run the FastAPI server surface (used by `fleet web`).
  serve-mcp   Run optional FastMCP server surface (requires `--extra mcp`).
  chat        Start standalone in-process interactive terminal chat.
  daytona-smoke  Run a native Daytona smoke validation without invoking an LM.
```

### `fleet-rlm init`

Bootstrap Claude Code scaffold assets to a user-level directory. Copies bundled RLM skills, agents, teams, and hooks from the installed `fleet-rlm` package to `~/.claude/` (or a custom target).

```text
Usage: fleet-rlm init [OPTIONS]

Options:
  --target PATH     Target directory (defaults to ~/.claude)
  --force           Overwrite existing files
  --skills-only     Install only skills, not agents
  --agents-only     Install only agents, not skills
  --teams-only      Install only team templates
  --hooks-only      Install only hook templates
  --no-teams        Skip installing team templates
  --no-hooks        Skip installing hook templates
  --list            List available scaffold assets (no install)
  --help            Show this message and exit.
```

**Examples:**

```bash
# Install all scaffold assets to default location
uv run fleet-rlm init

# List available assets without installing
uv run fleet-rlm init --list

# Install only skills to custom location
uv run fleet-rlm init --target ~/my-project/.claude --skills-only

# Force overwrite existing files
uv run fleet-rlm init --force
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

### `fleet-rlm serve-mcp`

Run the FastMCP server for Model Context Protocol integration. Requires the `mcp` extra to be installed.

For a package install, add the extra with:

```bash
uv add "fleet-rlm[mcp]"
```

```text
Usage: fleet-rlm serve-mcp [OPTIONS]

Options:
  --transport TEXT    FastMCP transport: stdio, sse, streamable-http [default: stdio]
  --host TEXT         Host for HTTP transports [default: 127.0.0.1]
  --port INTEGER      Port for HTTP transports [default: 8001]
  --help              Show this message and exit.
```

**Transport Modes:**

- `stdio`: Standard input/output (default, for CLI tools like Claude Desktop)
- `sse`: Server-Sent Events over HTTP
- `streamable-http`: Streamable HTTP transport

**Examples:**

```bash
# Start MCP server with stdio transport (for Claude Desktop)
uv run fleet-rlm serve-mcp

# Start MCP server with SSE transport
uv run fleet-rlm serve-mcp --transport sse --port 8001

# Start with streamable-http transport
uv run fleet-rlm serve-mcp --transport streamable-http --host 0.0.0.0 --port 8001
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

- [Using the MCP Server](../how-to-guides/using-mcp-server.md) — MCP setup and Claude Desktop integration
- [Installation](../how-to-guides/installation.md) — Setup and dependency installation
- [Runtime Settings](../how-to-guides/runtime-settings.md) — Configuring LLM models and runtime behavior
