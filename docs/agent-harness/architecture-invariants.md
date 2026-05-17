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

## Frontend Boundaries

Keep shared UI primitives reusable:

- `src/frontend/src/components/ui/*`, `components/ai-elements/*`, and `components/product/*` must
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
