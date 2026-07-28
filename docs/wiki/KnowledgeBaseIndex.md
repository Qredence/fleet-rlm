# Knowledge Base Index

**Last verified against commit:** `cfc464c93765e06866279ce998d575d31cefce3a` (`dev-0.7`)

**Purpose:** Navigation hub for the mirrored Qoder-generated knowledge base.

> **Note:** The original generated index catalogued a 46-page `droid-wiki/` tree that does
> **not** exist in this repository. Those dead links have been removed. This index now
> reflects the *actual* `.qoder/repowiki/` tree as mirrored into `docs/wiki/`.

## System / content pages

| Article | Description | Location |
|---------|-------------|----------|
| QoderWiki System | What the Qoder generator produced for this repo | [content/QoderWiki.md](QoderWiki.md) |
| QoderWiki Reference | Qoder knowledge-model reference | [content/QoderWikiReference.md](QoderWikiReference.md) |
| Fleet RLM Backend | Backend subsystem architecture | [content/FleetRLMBackend.md](FleetRLMBackend.md) |
| Fleet Terminal Client | pi-tui TUI application architecture | [content/FleetTerminalClient.md](FleetTerminalClient.md) |

## Knowledge modules

Mirrored under `knowledge/` with kebab-case directory names.

### Monorepo root

- [Fleet RLM Monorepo](../knowledge/fleet-rlm-monorepo/README.md) — root manifest (repo-level scope files)

### Fleet RLM Backend Core (`src/fleet_rlm/`)

- [Backend Core](../knowledge/fleet-rlm-monorepo/backend-core/README.md)
  - [DSPy RLM Turn Execution Engine](../knowledge/fleet-rlm-monorepo/backend-core/dspy-rlm-turn-execution/README.md)
  - [Daytona Sandbox Runtime Adapter](../knowledge/fleet-rlm-monorepo/backend-core/daytona-sandbox-runtime/README.md)
  - [Chat Turn Lifecycle & Orchestration](../knowledge/fleet-rlm-monorepo/backend-core/chat-turn-lifecycle/README.md)
  - [Closed Session Domain & Committed Turn Model](../knowledge/fleet-rlm-monorepo/backend-core/closed-session-domain/README.md)
  - [FastAPI HTTP Surface & AI SDK UI Projection](../knowledge/fleet-rlm-monorepo/backend-core/fastapi-http-surface/README.md)
  - [Bundled Skill Catalog & Progressive Tool Host](../knowledge/fleet-rlm-monorepo/backend-core/skill-catalog/README.md)
  - [Artifact Catalog & Promotion Pipeline](../knowledge/fleet-rlm-monorepo/backend-core/artifact-catalog/README.md)
  - [Attachment & Session Workspace File Management](../knowledge/fleet-rlm-monorepo/backend-core/file-management/README.md)
  - [SQLAlchemy Persistence Layer & Repository Adapters](../knowledge/fleet-rlm-monorepo/backend-core/persistence-layer/README.md)
  - [Observability & Tracing (MLflow, Failure Diagnostics)](../knowledge/fleet-rlm-monorepo/backend-core/observability-tracing/README.md)
  - [Fleet RLM Command-Line Interface & Process Supervisor](../knowledge/fleet-rlm-monorepo/backend-core/cli-supervisor/README.md)

### Sibling subtrees

- [Fleet TUI](../knowledge/fleet-rlm-monorepo/fleet-tui/README.md) — `tools/fleet-tui/`
- [Maintenance, Validation & Benchmark Scripts](../knowledge/fleet-rlm-monorepo/scripts/README.md) — `scripts/`
- [Pytest Test Suite](../knowledge/fleet-rlm-monorepo/test-suite/README.md) — `tests/`
- [Project Documentation & Agent Harness](../knowledge/fleet-rlm-monorepo/project-docs/README.md) — `docs/`
- [Database Schema Migrations (Alembic)](../knowledge/fleet-rlm-monorepo/database-migrations/README.md) — `migrations/`

### Standalone technology pages

- [Build & Release Pipeline](../knowledge/build-release-pipeline.md)
- [CircleCI Continuous Integration](../knowledge/circleci-pipeline.md)
- [Configuration System (TOML Profiles + Pydantic Settings)](../knowledge/configuration-system.md)
- [Structured Domain Error Hierarchy](../knowledge/error-hierarchy.md)
- [Python stdlib logging with MLflow tracing](../knowledge/logging-mlflow.md)
- [Terminal TUI Theme System](../knowledge/tui-theme-system.md)
- [uv + pnpm Monorepo Dependency Management](../knowledge/uv-pnpm-monorepo.md)
- [Ruff Code Quality and Formatting](../knowledge/ruff-code-quality.md)
- [Ty Static Type Checker](../knowledge/ty-type-checker.md)
- [Pytest Testing Framework with Async Support](../knowledge/pytest-async.md)
- [Business Glossary](../knowledge/business-glossary.md)

## How this maps to canonical docs

- [Repo Wiki index](../README.md) — how this mirror is organized and verified
- [Docs home](../../index.md) — canonical documentation entry point
- [Reference index](../../reference/index.md) — configuration, HTTP API, CLI, database
