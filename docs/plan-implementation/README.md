# RLM-Native Backend Foundation

The greenfield backend was built in parallel, earned its attachment, artifact,
persistence, and workspace-isolation exit bar, then replaced the legacy Python
backend atomically. `src/fleet_rlm/` is now the sole backend package.

## Current Destination

- FastAPI application at `fleet_rlm.main:app`.
- SSE chat at `POST /api/chat`.
- DSPy `RLM` with one fresh custom interpreter per Turn.
- Daytona Run Sandboxes and workspace-scoped durable Volume storage.
- committed Turn, Run, Checkpoint, Attachment, Artifact, and Skill metadata.
- one fresh Alembic baseline for an empty target database.
- backend-only CLI, OpenAPI, test, and release gates.

See [target architecture](target-architecture.md) for module ownership and the
[clean backend dossier](clean-backend/README.md) for the pre-cutover design and
evidence lineage.

## Historical Material

The removed backend's phase dossiers, ADRs, guides, references, and release
notes are preserved under `docs/internal/legacy-backend/`. They do not describe
supported APIs, configuration, commands, or compatibility behavior.

Frontend adaptation and production deployment remain separately authorized.
