# Fleet RLM Lite

Fleet RLM Lite is a Python application for working with AI-assisted Sessions.
It combines a Rich terminal client, a FastAPI server with Server-Sent Events
(SSE), DSPy reasoning and evaluation, Daytona Python execution, and a
PostgreSQL-backed virtual filesystem (VFS).

> **Operational status:** live execution needs working Daytona credentials and
> resources, a PostgreSQL database, and credentials for the selected model
> provider. This documentation does not claim those external integrations have
> been verified in your deployment. The server has no caller authentication and binds to loopback
> by default; use only in a trusted environment.

The v2 runtime pins DSPy 3.4.1 and supports strict RLM, Flex, and hybrid modes;
file-backed large context; in-REPL DSPy sub-agents with fresh recursive Daytona
child sandboxes; source-backed reviewed memory; and explicit versioned program
optimization/promotion. Call, depth, sandbox, transfer, and wall-time limits
bound execution. These are not guarantees of correctness or quality improvement.
See [architecture](ARCHITECTURE.md) and the
[implementation and acceptance plan](fleet_rlm_v2_0_architecture_full_implementation_plan_feature_fleet_rlm_lite.md)
for trust boundaries and required validation.

## Install and configure

Requirements: Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), PostgreSQL,
and access to a Daytona organization with a Volume. Daytona execution requires
`FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID`, and
`FLEET_DAYTONA_VOLUME_ID`. Configure the model provider and other settings
through the `FleetSettings` environment variables described in
[Configuration](docs/reference/configuration.md).

```bash
git clone https://github.com/qredence/fleet-rlm.git
cd fleet-rlm
uv sync --dev
cp .env.example .env
${EDITOR:-vi} .env
```

Set the values required by your provider, including `FLEET_DATABASE_URL`,
`FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_ORG_ID`, and
`FLEET_DAYTONA_VOLUME_ID`. See [Configuration](docs/reference/configuration.md)
for the full setting names and defaults. Never commit `.env` or credentials.
Fleet creates its required Postgres tables at application startup; it does not
use database migrations. The configured database itself must already exist
and accept connections.

## Run the server and CLI

In one terminal, start the API:

```bash
uv run fleet serve
```

In another terminal, start the interactive client:

```bash
uv run fleet cli --session research-1
```

Without `--session`, the CLI creates a new short session ID. Useful commands
inside the Rich client include `/help`, `/attach <path>`, `/files`,
`/artifacts`, `/skills`, `/skills pull`, and `/sessions`.

The default listener is `127.0.0.1:8000`. Binding to a non-loopback address
requires setting `FLEET_ALLOW_NON_LOOPBACK_BIND=true`; this does not add
authentication. Do not expose the service to an untrusted network.

## What Fleet provides

- Per-session Daytona workspaces with files mirrored to Postgres VFS storage.
- Progressive Agent Skills in the user-managed `.fleet/skills/` directory.
  Fleet does not sync developer skills under `.agents/skills/`.
- SSE events for model activity and completed Turns.
- MLflow tracing and an opt-in TypeSafe route decision when configured.
- GEPA-based optimization utilities; evaluation requires configured model and
  tracking services and is not part of ordinary local checks.

## API

The current endpoints are `GET /health`, `GET /sessions`, `GET /skills`,
`POST /skills/pull`, `GET /sessions/{session_id}/files`,
`POST /sessions/{session_id}/attach`, and
`POST /sessions/{session_id}/turn` (SSE). See the
[HTTP API reference](docs/reference/http-api.md) for request and event details.

## Development

```bash
make install-dev
make check
```

See [Contributing](CONTRIBUTING.md), [Architecture](ARCHITECTURE.md), and the
[documentation index](docs/index.md) for more.

## License

MIT; see [LICENSE](LICENSE).
