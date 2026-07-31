# fleet-rlm Documentation

The maintained backend is the RLM-native FastAPI application under
`src/fleet_rlm/`. It supports Session-first SSE Turns, ordered committed history,
Attachments, committed Artifacts, instruction Skills, private Daytona Session
Workspace files, and durable Run cancellation. The maintained client is pi-tui
under `tools/fleet-tui/`.

## Quickstart

Install Python dependencies, then select a configured profile:

```bash
uv sync --all-extras --dev
uv run fleet cli    # Daytona + pi-tui; requires an LLM key, a Daytona key, and an upgraded database
```

Backend-only launch:

```bash
uv run fleet-rlm serve-api --port 8000
```

See [configuration](reference/configuration.md) for profile prerequisites.

## Current documentation

- [Documentation home](index.md)
- [Architecture](architecture.md)
- [Configuration](reference/configuration.md)
- [HTTP API](reference/http-api.md)
- [CLI](reference/cli.md)
- [Terminal UI](how-to-guides/terminal-tui.md)
- [Database](reference/database.md)
- [Testing strategy](how-to-guides/testing-strategy.md)
- [Agent harness](agent-harness/README.md)

`openapi.yaml` and `tools/fleet-tui/src/generated/openapi.ts` are generated
together. No tracked implementation plan is active; completed local evidence may
be retained under ignored `.scratch/archive/`.
