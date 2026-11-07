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
- [Evaluation](reference/evaluation.md): the metric, scorers, and GEPA gates.
- [Sandbox environment](reference/sandbox-environment.md): the Daytona image,
  snapshot, and Volume layout.
- [Turn results](reference/turn-results.md): the canonical completion payload.
- [MLflow trace retention](runbooks/mlflow-trace-retention.md): manual governed
  cleanup for Fleet's Unity Catalog trace tables.
- [Evaluation canary](runbooks/evaluation-canary.md): live promotion evidence.
- [External task benchmarks](runbooks/benchmark-tasks.md): notebook-driven LongCoT
  and OOLONG execution, official scoring, and saved-trace evaluation.
- [Native DSPy integration](runbooks/native-dspy-integration.md): the adapter
  contract and its recorded gateway corpus.
- [Source layout](reference/source-layout.md): current runtime file ownership.
- [Archive](archive/README.md): frozen PRDs and retired benchmark workflows.

## Current behavior is the source of truth

Runtime behavior is implemented under `src/fleet_rlm/`. Store schema changes use
numbered additive migrations.
Non-secret process settings use schema-4 `config/fleet.toml` and validated
environment overrides. It creates its required tables at startup. Live Daytona
and model-provider behavior requires external credentials and resources and is
not certified by local documentation or CI.
