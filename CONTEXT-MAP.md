# Fleet RLM context map

## Contexts

- [Shared Fleet RLM](./CONTEXT.md) — product-wide User, Workspace, Session,
  Sandbox, Skill, Artifact, Attachment, Runtime Event, and Volume language.
- [Backend runtime](./src/fleet_rlm/CONTEXT.md) — the canonical RLM-native
  FastAPI backend: Turns, Runs, Interpreter Leases, Skill Cards, progressive
  loading, staged Attachments, Artifact Candidates, and Turn Commit.

## Relationships

- The backend specializes shared product terms with execution and isolation
  invariants; it does not redefine Workspace ownership.
- A new frontend may later consume the AI SDK UIMessage SSE contract. No
  frontend implementation is currently part of this repository surface.
- Daytona, DSPy, SQLAlchemy, and FastAPI are infrastructure; domain behavior is
  named in backend terms rather than provider APIs.
