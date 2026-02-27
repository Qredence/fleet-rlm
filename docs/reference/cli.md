# CLI Reference

This document describes the current CLI surfaces for `fleet-rlm`.

## Entrypoints

There are two command entrypoints:

- `fleet-rlm`: Typer-based command group
- `fleet`: lightweight launcher for terminal chat and Web UI startup

## `fleet-rlm` Commands

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

### `fleet-rlm serve-api`

```bash
uv run fleet-rlm serve-api --help
```

Options:

- `--host` (default `127.0.0.1`)
- `--port` (default `8000`)

Hydra-style config overrides are supported as trailing `key=value` tokens.

### `fleet-rlm serve-mcp`

```bash
uv run fleet-rlm serve-mcp --help
```

Options:

- `--transport` (`stdio`, `sse`, `streamable-http`; default `stdio`)
- `--host` (default `127.0.0.1`)
- `--port` (default `8001`)

### `fleet-rlm chat`

```bash
uv run fleet-rlm chat --help
```

Options:

- `--docs-path PATH`
- `--trace / --no-trace`
- `--trace-mode TEXT` (`compact`, `verbose`, `off`)

## `fleet` Launcher

```bash
uv run fleet --help
```

Behavior:

- `fleet` starts standalone terminal chat.
- `fleet web` starts the Web UI/API server on `0.0.0.0:8000` via `fleet-rlm serve-api`.

Options:

- `--docs-path PATH`
- `--trace-mode {compact,verbose,off}`
- `--volume-name TEXT`
- `--secret-name TEXT`

## Notes

If other docs reference commands not shown in current `--help`, treat them as historical.
For MCP setup examples, see [Using the MCP Server](../how-to-guides/using-mcp-server.md).
