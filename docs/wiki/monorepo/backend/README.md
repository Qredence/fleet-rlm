# Fleet RLM Backend Core

**Source wiki:** `.qoder/repowiki/knowledge/en/Fleet RLM Monorepo — FastAPI Backend, TUI Client & Dev Tooling/Fleet RLM Backend Core`
**Scope:** `src/fleet_rlm/`

The Backend Core subtree mirrors the canonical Python backend package. The module manifest
declares `module_path: fleet_rlm_core` and scope `src/fleet_rlm/`; its own category notes
were generated mostly empty, so navigate the child modules for the substantive
`architecture_design.md` and `coding_conventions.md` fragments.

## Category notes

- [coding_conventions](coding_conventions.md)

## Child modules

Execution and orchestration:

- [DSPy RLM Turn Execution Engine](dspy-rlm-turn-execution/README.md) — `rlm/`
- [Chat Turn Lifecycle & Orchestration](chat-turn-lifecycle/README.md) — `chat/`
- [Closed Session Domain & Committed Turn Model](closed-session-domain/README.md) — `sessions/`

Provider and runtime:

- [Daytona Sandbox Runtime Adapter](daytona-sandbox-runtime/README.md) — `daytona/`
- [Fleet RLM Command-Line Interface & Process Supervisor](cli-supervisor/README.md) — `cli/`

HTTP, skills, files, persistence, observability:

- [FastAPI HTTP Surface & AI SDK UI Projection](fastapi-http-surface/README.md) — `api/`
- [Bundled Skill Catalog & Progressive Tool Host](skill-catalog/README.md) — `skills/`
- [Artifact Catalog & Promotion Pipeline](artifact-catalog/README.md) — `artifacts/`
- [Attachment & Session Workspace File Management](file-management/README.md) — `files/`
- [SQLAlchemy Persistence Layer & Repository Adapters](persistence-layer/README.md) — `persistence/`
- [Observability & Tracing (MLflow, Failure Diagnostics)](observability-tracing/README.md) — `observability/`

## Navigation

- [Monorepo root](../README.md)
- [Repo Wiki index](../../../README.md)
