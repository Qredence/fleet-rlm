# Fleet RLM Lite documentation

Current user and developer documentation for Fleet RLM Lite.

## Pages

- [README](../README.md): install, configure, and start Fleet.
- [Architecture](../ARCHITECTURE.md): runtime components and trust boundaries.
- [Contributing](../CONTRIBUTING.md): development setup and contribution steps.
- [Configuration](reference/configuration.md): settings, defaults, and
  environment variables.
- [HTTP API](reference/http-api.md): routes and SSE behavior.
- [CLI](reference/cli.md): server and Rich client commands.
- [Database](reference/database.md): Postgres tables and persistence model.
- [Source layout](reference/source-layout.md): current runtime file ownership.

## Current behavior is the source of truth

Runtime behavior is implemented under `src/fleet_rlm/`. This Lite runtime does
not include the legacy terminal package, Alembic migrations, or `fleet.toml`
configuration system. It creates its required tables at startup. Live Daytona
and model-provider behavior requires external credentials and resources and is
not certified by local documentation or CI.
