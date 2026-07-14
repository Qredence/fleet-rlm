# Product

## Users

Fleet RLM is for developers and AI engineers who need a durable, inspectable
recursive-agent backend. They interact through the standalone terminal client
or directly through the HTTP/SSE contract.

## Product purpose

Fleet RLM turns a user message plus optional Attachments and Skills into a
Daytona-backed DSPy RLM Turn. It preserves Session history, streams sanitized
Runtime Events, and commits generated Artifacts only after durable promotion
succeeds.

Success means a user can run a complex task, observe progress, recover the
durable conversation, and inspect committed outputs without implementing
sandbox lifecycle, persistence, or streaming themselves.

## Product surfaces

- The FastAPI backend and generated `openapi.yaml` contract.
- The AI SDK UI v1 stream exposed by `POST /api/sessions/{session_id}/turns`.
- Session, Attachment, Artifact, Skill, and cancellation HTTP routes.
- The standalone terminal client under `tools/fleet-tui/`.

Canonical Run Environment set: `hermetic`, `deno`, `daytona`.

Run Environments split by intent: `hermetic` is the deterministic offline and
test profile; `deno` is vanilla local `dspy.RLM` with a real LLM and DSPy's
default Deno/Pyodide interpreter; `daytona` is the full Fleet solution with
Sandbox, Workspace Volume Scope, and Turn Commit. Deno is intentionally
reduced-capability: it supports Attachment reads and Skills, but not
`create_artifact`, durable Artifact promotion, or Daytona resources.

The terminal client uses Ink as its only renderer and one strict stream and
projection path for both live Turns and reload. Its white-and-gray operator
timeline displays sanitized reasoning, code, interpreter output, tools,
recoverable errors, usage, and durable structured results as chronological
typed cards. Execution cards start expanded and remain individually
collapsible.

A future graphical client is a separate product effort. There is no maintained
Web frontend, WebSocket execution path, optimization UI, or runtime-settings UI
in this repository.

## Principles

1. **Transparency over magic.** Surface sanitized progress, tool activity, and
   terminal outcomes without exposing secrets or hidden model reasoning.
2. **Durability over process state.** Session correctness depends on committed
   metadata and Workspace Volume Scope, not a surviving Sandbox.
3. **Explicit trust.** Authorization, path safety, Skill visibility, and
   Artifact publication remain deterministic host responsibilities.
4. **Small public surface.** Keep one backend, one SSE transcript path, and
   narrowly owned domain modules.
5. **Provider isolation.** Generated code executes in Daytona; provider details
   do not leak across the `daytona/` seam.
