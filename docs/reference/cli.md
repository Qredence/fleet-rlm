# CLI Reference

This document describes the current CLI surfaces for `fleet-rlm` `v0.4.8`.

## Entrypoints

There are two command entrypoints:

- `fleet-rlm`: Typer-based command group
- `fleet`: lightweight launcher for terminal chat and Web UI startup

## `fleet-rlm` Commands

From repo root (source install):

```bash
uv run fleet-rlm --help
```

Current command set:

- `init`: install scaffold assets to a Claude directory
- `serve-api`: run FastAPI server
- `serve-mcp`: run FastMCP server
- `chat`: run standalone in-process terminal chat

### `fleet-rlm init`

```bash
uv run fleet-rlm init --help
```

Key options:

- `--target PATH` (default `~/.claude`)
- `--force`
- `--skills-only`
- `--agents-only`
- `--teams-only`
- `--hooks-only`
- `--no-teams`
- `--no-hooks`
- `--list`

Examples:

```bash
# Install all scaffold assets
uv run fleet-rlm init

# List available packaged assets without writing files
uv run fleet-rlm init --list

# Install only skills
uv run fleet-rlm init --skills-only
```

### `fleet-rlm serve-api`

```bash
uv run fleet-rlm serve-api --help
```

Options:

- `--host` (default `127.0.0.1`)
- `--port` (default `8000`)

Example:

```bash
uv run fleet-rlm serve-api --host 0.0.0.0 --port 8000
```

Hydra-style config overrides are supported as trailing `key=value` tokens:

```bash
uv run fleet-rlm serve-api --port 8000 \
  interpreter.async_execute=true \
  agent.guardrail_mode=warn
```

### `fleet-rlm serve-mcp`

```bash
uv run fleet-rlm serve-mcp --help
```

Options:

- `--transport` (`stdio`, `sse`, `streamable-http`; default `stdio`)
- `--host` (default `127.0.0.1`)
- `--port` (default `8001`)

Examples:

```bash
# stdio transport (Claude Desktop / Codex MCP style)
uv run fleet-rlm serve-mcp --transport stdio

# HTTP transport
uv run fleet-rlm serve-mcp --transport streamable-http --host 0.0.0.0 --port 8001
```

### `fleet-rlm chat`

```bash
uv run fleet-rlm chat --help
```

Options:

- `--docs-path PATH`
- `--trace / --no-trace`
- `--trace-mode TEXT` (`compact`, `verbose`, `off`)

Example:

```bash
uv run fleet-rlm chat --docs-path README.md --trace-mode compact
```

## `fleet` Launcher

Inspect:

```bash
uv run fleet --help
```

Behavior:

- `fleet` starts standalone terminal chat.
- `fleet web` starts the Web UI/API server on `0.0.0.0:8000` by delegating to `fleet-rlm serve-api`.

Options:

- `--docs-path PATH`
- `--trace-mode {compact,verbose,off}`
- `--volume-name TEXT`
- `--secret-name TEXT`

Examples:

```bash
# Terminal chat
fleet

# Launch web surface
fleet web

# Chat with explicit runtime hints
fleet --docs-path README.md --trace-mode verbose --volume-name rlm-volume-dspy
```

## Notes

- If other documents reference commands that are not shown in current `fleet-rlm --help`, treat those references as historical.
- For MCP setup examples, see [Using the MCP Server](../how-to-guides/using-mcp-server.md).
