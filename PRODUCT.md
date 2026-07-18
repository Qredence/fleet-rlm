# Product

## Users

Fleet RLM is for developers and AI engineers who need a durable, inspectable
recursive-agent backend. They interact through the standalone terminal client
or directly through the HTTP/SSE contract.

## Product purpose

Fleet RLM turns a user message plus optional Attachments and Skills into a fresh
DSPy RLM Turn. It preserves Session history, streams sanitized Runtime Events,
and publishes generated Artifacts only after durable promotion and Turn Commit.

Success means a user can run a complex task, observe progress, resume the
durable conversation, and inspect committed outputs without implementing
provider lifecycle, persistence, or stream handling.

## Product surfaces

Canonical Run Environment set: `deno`, `daytona`.

- The FastAPI backend and generated `openapi.yaml` contract.
- Generated TUI HTTP types at `tools/fleet-tui/src/generated/openapi.ts`.
- The AI SDK UI v1 stream exposed by
  `POST /api/sessions/{session_id}/turns`.
- Session, Attachment, committed Artifact, Skill, and cancellation routes.
- The standalone pi-tui client under `tools/fleet-tui/`.

The two profiles differ by capability:

- `deno` is local native `dspy.RLM` with a real LM and DSPy's default
  Deno/Pyodide interpreter. It supports Attachment reads and instruction Skills
  but not durable Artifact promotion or Daytona resources.
- `daytona` is the full Fleet path with Sandbox execution, Workspace Volume
  Scope, private Session Workspace files, and commit-gated Artifact promotion.

Private tests install deterministic composition explicitly without defining a
third public profile.

The terminal client uses pi-tui as its only renderer. Its achromatic operator
timeline shows sanitized reasoning, code, interpreter output, tools, Skills,
recoverable errors, usage, Artifacts, and typed results in chronological order.
Evidence is static, complete, and expanded in native terminal scrollback; Fleet
does not capture the mouse or maintain transcript viewport state.

Bundled Skills provide versioned instructions and resources. Trusted executable
host capabilities are a separate registry seam and are empty in the default
production composition.

A future graphical client is a separate product effort. There is no maintained
Web frontend, WebSocket execution path, optimization UI, or runtime-settings UI
in this repository.

## Principles

1. **Transparency over magic.** Surface sanitized progress and terminal outcomes
   without exposing secrets or hidden model reasoning.
2. **Durability over process state.** Session correctness depends on committed
   metadata and Workspace Volume Scope, not a surviving Sandbox.
3. **Explicit trust.** Authorization, path safety, Skill visibility, and
   Artifact publication remain deterministic host responsibilities.
4. **Small public surface.** Keep one backend, one SSE transcript path, and
   narrowly owned domain modules.
5. **Provider isolation.** Provider details and raw failures do not cross the
   `daytona/` seam.
