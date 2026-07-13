# fleet-rlm Documentation

The maintained backend is the RLM-native FastAPI application in
`src/fleet_rlm/`. It supports SSE chat, Sessions and ordered Turn history,
Attachments, committed Artifacts, Skill Card discovery, and Run cancellation.

## Quickstart

```bash
uv sync --all-extras --dev
uv run fleet web
```

The equivalent API-only command is:

```bash
uv run fleet-rlm serve-api --port 8000
```

## Current Documentation

- [Documentation home](index.md)
- [Architecture](architecture.md)
- [HTTP API](reference/http-api.md)
- [CLI](reference/cli.md)
- [Database](reference/database.md)
- [Testing strategy](how-to-guides/testing-strategy.md)
- [Agent harness](agent-harness/README.md)

The authoritative backend contract is `openapi.yaml`. Superseded implementation
plans and removed-backend documentation remain available through Git history.
