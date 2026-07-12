# Fleet RLM context map

## Contexts

- [Shared Fleet RLM](./CONTEXT.md) — User, Workspace, Session, Sandbox, Sandbox Workspace, Skill, Artifact, Attachment, Runtime Event, and other product-wide language.
- [Backend runtime](./src/fleet_rlm/CONTEXT.md) — execution, compatibility, and runtime-boundary language for the live FastAPI backend.
- [Clean Backend](./src/fleet_rlm_clean/CONTEXT.md) — parallel RLM-native backend language (Turn, Run, Interpreter Lease, Skill Card, Progressive Load) until cutover.
- [Frontend experience](./src/frontend/CONTEXT.md) — transcript and interactive-control language for the Web UI.

## Relationships

- **Frontend experience → Backend runtime:** the frontend submits a turn request and consumes backend-projected transcript events; it does not interpret runtime internals.
- **Frontend experience → Clean Backend (target):** the same Runtime Event → transcript projection idea applies when the UI talks to the clean package.
- **Backend runtime → Shared Fleet RLM:** live execution applies shared Session, Skill, file, Artifact, and Sandbox concepts.
- **Clean Backend → Shared Fleet RLM:** clean execution applies the same shared Workspace, Session, Skill, Attachment, Artifact, Sandbox, and Volume concepts with stricter isolation language.
- **Clean Backend → Backend runtime:** clean is the parallel RLM-native package until cutover; it is not the Compatibility Runtime.
- **Backend runtime → Clean Backend:** live remains the compatibility and migration path until promotion evidence justifies cutover.
- **Backend runtime → Frontend experience:** the backend projects Runtime Event data into frontend-safe transcript and control contracts.
