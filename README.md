# fleet-rlm

[![PyPI version](https://img.shields.io/pypi/v/fleet-rlm.svg)](https://pypi.org/project/fleet-rlm/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml/badge.svg)](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/fleet-rlm?period=monthly&units=INTERNATIONAL_SYSTEM&left_color=MAGENTA&right_color=BLACK&left_text=downloads%2Fmonth)](https://pepy.tech/projects/fleet-rlm)

Secure Recursive Language Models with DSPy, Modal sandbox execution, an integrated Web UI, and an experimental Daytona workbench runtime.

`fleet-rlm` is built around a Web UI-first workflow for long-context code and document reasoning. The default runtime stays Modal-backed and chat-oriented. The Daytona path is experimental, but it now plugs into the same shared websocket workspace and dedicated run-workbench experience instead of living as a completely separate product surface.

[Docs](docs/) | [Contributing](CONTRIBUTING.md) | [Changelog](CHANGELOG.md)

## Quick Start

Install the published package and launch the Web UI:

```bash
# Option 1: install the tool
uv tool install fleet-rlm
fleet web
```

Or run it in your current environment:

```bash
# Option 2: install in the active environment
uv pip install fleet-rlm
fleet web
```

Open `http://localhost:8000`.

Published installs already include built frontend assets, so end users do not need `pnpm`, `vp`, or a separate frontend build step.

## What You Get

- A focused Web UI with `RLM Workspace`, `Volumes`, and `Settings`
- A default Modal-backed chat/runtime path over `/api/v1/ws/chat`
- An experimental Daytona workbench path exposed through the same workspace and websocket contract
- Runtime settings and diagnostics APIs for model, Modal, and Daytona configuration
- Optional FastMCP server support for external tool clients
- PostHog and MLflow integration for runtime telemetry, tracing, feedback, and evaluation workflows

Legacy `taxonomy`, `skills`, `memory`, and `analytics` routes are no longer first-class product surfaces; the current app redirects those URLs to supported pages.

## Core Commands

```bash
# Primary local Web UI + API server
fleet web

# Standalone terminal chat
fleet-rlm chat --trace-mode compact

# Explicit FastAPI server
fleet-rlm serve-api --port 8000

# Optional MCP server
fleet-rlm serve-mcp --transport stdio

# Experimental Daytona setup validation
fleet-rlm daytona-smoke --repo https://github.com/qredence/fleet-rlm.git --ref main

# Experimental Daytona rollout runner
fleet-rlm daytona-rlm \
  --repo https://github.com/qredence/fleet-rlm.git \
  --task "Summarize the tracing architecture" \
  --batch-concurrency 4

# Scaffold bundled Claude Code assets
fleet-rlm init --list
```

## Runtime Model

- `Modal chat` is the default runtime in `RLM Workspace`.
- `Daytona pilot` is experimental and uses the same websocket chat surface with `runtime_mode="daytona_pilot"`.
- In Modal mode, the chat composer can send `execution_mode`.
- In Daytona mode, the chat flow can send optional `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`.
- Daytona websocket requests do not accept request-side `max_depth`; the CLI still carries `--max-depth` as a deprecated compatibility flag for `fleet-rlm daytona-rlm`.
- The right rail stays mode-aware: standard sessions use the message inspector, while Daytona sessions hydrate the dedicated run workbench for iterations, callbacks, prompts, evidence, and final output.

## Auth and Runtime APIs

The current frontend/backend contract centers on:

- `/health` and `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `/api/v1/runtime/settings`
- `/api/v1/runtime/status`
- `/api/v1/runtime/tests/modal`
- `/api/v1/runtime/tests/lm`
- `/api/v1/runtime/volume/tree`
- `/api/v1/runtime/volume/file`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`

When `AUTH_MODE=entra`, HTTP and WebSocket access use real Entra bearer token validation plus Neon-backed tenant admission. Runtime settings writes are intentionally limited to `APP_ENV=local`.

## Running From Source

From the repo root:

```bash
uv sync --all-extras --dev
uv run fleet web
```

Frontend contributors should use `pnpm` scripts from `src/frontend`:

```bash
cd src/frontend
pnpm install --frozen-lockfile
pnpm run dev
pnpm run build
pnpm run check
```

The frontend uses Vite+ internally through the `vp` toolchain, but the canonical contributor workflow in this repo is `pnpm run ...`.

## Contributor Checks

Common repo-level validation commands:

```bash
make test-fast
make quality-gate
make release-check
```

Focused docs validation:

```bash
uv run python scripts/check_docs_quality.py
uv run python scripts/check_release_hygiene.py
uv run python scripts/check_release_metadata.py
```

## Experimental Daytona Notes

The Daytona runtime is analysis-first and remains opt-in. Use this order:

1. Set `DAYTONA_API_KEY`, `DAYTONA_API_URL`, and optional `DAYTONA_TARGET`.
2. Run `fleet-rlm daytona-smoke --repo <url> [--ref <branch-or-sha>]`.
3. Only then run `fleet-rlm daytona-rlm [--repo <url>] [--context-path <path> ...] --task <text> ...`.

This repo treats `DAYTONA_API_BASE_URL` as a misconfiguration. Use `DAYTONA_API_URL` instead.

## More Docs

- [Documentation index](docs/index.md)
- [Developer setup](docs/how-to-guides/developer-setup.md)
- [CLI reference](docs/reference/cli.md)
- [HTTP API reference](docs/reference/http-api.md)
- [Auth reference](docs/reference/auth.md)
- [Frontend/backend integration](docs/reference/frontend-backend-integration.md)
- [MLflow workflows](docs/how-to-guides/mlflow-workflows.md)
- [Runtime settings](docs/how-to-guides/runtime-settings.md)
- [Using the MCP server](docs/how-to-guides/using-mcp-server.md)
