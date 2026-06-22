# Architecture Invariants

These rules are the fast path for agent review. If a change violates one of them, either remediate
the code or update this document and the matching checks in the same patch.

## Backend Layers

Keep the backend layered from transport to runtime to substrate:

- `src/fleet_rlm/api/` owns FastAPI app assembly, auth, HTTP routers, websocket endpoints, runtime
  services, and SPA serving.
- `src/fleet_rlm/runtime/` owns the DSPy ReAct agent, chat orchestration, execution event assembly,
  tool registry, session state, and module construction.
- `src/fleet_rlm/integrations/daytona/` owns Daytona interpreter lifecycle, sandbox execution,
  volumes, diagnostics, and substrate-specific cleanup.
- `src/fleet_rlm/integrations/database/` and `src/fleet_rlm/integrations/local_store.py` own
  persistence.
- `src/fleet_rlm/quality/` owns offline DSPy evaluation and optimization machinery.

Transport code may call runtime services and schemas. Runtime code should not import frontend,
FastAPI route modules, or test-only helpers. Configuration/package-root modules must not pull in
heavy runtime providers such as DSPy, MLflow, PostHog, or Daytona at import time.

Observability callback registration is the exception only after an explicit runtime setup call.
Register MLflow and PostHog DSPy callbacks through
`integrations/observability/callback_registry.py` so callback registration stays lazy,
deduplicated by callback type, and safe when `dspy.configure(...)` rejects non-owner
threads or async tasks. The registry must preserve active thread-local callback overrides;
otherwise worker-thread warmup can report success while immediately following DSPy calls miss
the callbacks.

## Async Execution Boundary

The sandbox interpreters (Daytona, Modal) expose a synchronous, blocking `execute(...)` that
performs a network round-trip per code iteration. `dspy.RLM.aforward` only awaits the LM predictor
calls — it still runs sandbox code through the **synchronous** `repl.execute(...)` (verified in
dspy 3.3.0b1). Therefore the heavy RLM turn is driven sync-in-a-thread via
`asyncio.to_thread(self.agent, ...)` in `runtime/agent/runtime.py`, which offloads both the LM
calls and the blocking sandbox I/O to a worker thread and keeps the event loop free.

Do not replace this `asyncio.to_thread` wrapping with a direct `await agent.acall(...)`/`aforward`
on the RLM heavy path while the interpreter's `execute` stays synchronous — doing so would block the
event loop on every code-execution iteration and regress server concurrency. Lightweight branches
are the exception: the unified streaming path wraps the whole turn in `dspy.streamify`, and the
direct/tools branches use `acall` so their `response` predictors stream tokens natively. Worker
threads spawned for blocking work must disable token streaming (`dspy.context(send_stream=None)`),
because sync LM calls cannot forward stream chunks from plain `asyncio.to_thread` workers.

See also [docs/reference/dspy-daytona-interpreter-boundary.md](../reference/dspy-daytona-interpreter-boundary.md)
for Daytona snapshot/volume lifecycle notes and RLM budget knobs.

MCP-backed ReAct tools are the other async exception. Tools converted with
`dspy.Tool.from_mcp_tool(session, tool)` are bound to a live MCP `ClientSession` and must be invoked
through an async ReAct path (`acall`) while that session remains open. Keep MCP tools out of sync
ReAct calls, close the provider when the runtime shuts down, and rebuild the agent from base tools
plus the current MCP attachment when servers are reattached.

## Frontend Boundaries

Keep shared UI primitives reusable:

- `src/frontend/src/components/{ui,agent-elements,product}/*` must
  not import from route files or feature implementation modules.
- `src/frontend/src/lib/workspace/*` must stay UI-independent.
- `src/frontend/src/features/layout/*` should import product surfaces through feature contracts
  rather than reaching into deep implementation files.
- New handwritten feature files use `kebab-case`; React components use `PascalCase`.
- Use the canonical `cn()` import path: `@/lib/utils`.

## Generated And Synced Artifacts

Do not hand-edit:

- `openapi.yaml`
- `src/frontend/src/lib/rlm-api/generated/openapi.ts`
- `src/frontend/openapi/fleet-rlm.openapi.yaml`
- `src/frontend/src/routeTree.gen.ts`
- `src/frontend/dist`
- `src/fleet_rlm/ui/dist`

Use these commands:

```bash
# from repo root
make api-sync
make api-check
make build-ui
```

Backend API shape changes require:

```bash
# from repo root
uv run python scripts/openapi_tools.py generate
make api-sync
make api-check
```

## Script Boundary

`scripts/README.md` is the retained helper inventory. Top-level Python scripts under `scripts/`
must be listed there and support:

```bash
# from repo root
uv run python scripts/<name>.py --help
```

Daily workflows should remain on `make`, `fleet`, `fleet-rlm`, or documented `.codex` actions.

## Remediation

When a boundary check fails:

1. Move the code back to the owning layer when possible.
2. Prefer an existing service or feature contract over a new cross-layer import.
3. If the invariant is obsolete, update this document, the root `AGENTS.md` map, and
   `scripts/check_harness_engineering.py` in the same change.
4. Run `make check-docs` before finishing.
