# Fleet RLM context map

## Contexts

- [Shared Fleet RLM](./CONTEXT.md) — sandbox, workspace, session, Skill, artifact, memory, and other concepts shared across the product.
- [Backend runtime](./src/fleet_rlm/CONTEXT.md) — execution, compatibility, and runtime-boundary language for the FastAPI backend.
- [Frontend experience](./src/frontend/CONTEXT.md) — transcript and interactive-control language for the Web UI.

## Relationships

- **Frontend experience → Backend runtime:** the frontend submits a turn request and consumes backend-projected transcript events; it does not interpret runtime internals.
- **Backend runtime → Shared Fleet RLM:** backend execution applies the shared session, Skill, file, artifact, and sandbox concepts.
- **Backend runtime → Frontend experience:** the backend projects `RuntimeEvent` data into frontend-safe transcript and control contracts.
