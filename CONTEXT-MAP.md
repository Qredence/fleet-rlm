# Fleet RLM context map

## Contexts

- [Shared Fleet RLM](./CONTEXT.md) — product-wide User, Workspace, Session,
  Sandbox, Skill, Artifact, Attachment, Runtime Event, and Volume language.
- [Backend runtime](./src/fleet_rlm/CONTEXT.md) — the canonical RLM-native
  FastAPI backend: Turns, Runs, Interpreter Leases, Skill Cards, progressive
  loading, staged Attachments, Artifact Candidates, and Turn Commit.
- [Frontend experience](./src/frontend/CONTEXT.md) — the deferred Web UI
  integration context; it is not part of the backend hard cutover.

## Relationships

- The backend specializes shared product terms with execution and isolation
  invariants; it does not redefine Workspace ownership.
- The frontend may consume backend Runtime Events after a separate SSE contract
  adaptation. Its legacy WebSocket contract is not a backend compatibility
  requirement.
- Daytona, DSPy, SQLAlchemy, and FastAPI are infrastructure; domain behavior is
  named in backend terms rather than provider APIs.
