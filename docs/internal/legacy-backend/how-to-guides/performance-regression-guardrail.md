# Performance Regression Guardrail

Use this guide when runtime changes are large enough that you want a consistent
before/after validation pass.

## Baseline Lane

From the repo root:

```bash
make quality-gate
```

This runs the shared backend/frontend confidence lane, including:

- Python lint, format check, and type check
- non-live pytest coverage
- docs and metadata checks
- frontend API drift, type check, lint, unit tests, and production build

## Daytona Runtime Spot Check

For runtime-heavy changes, also run:

```bash
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

This exercises the live Daytona sandbox path without requiring a full LLM turn.

## Guardrail Workflow

1. Run `make quality-gate` on a known-good baseline.
2. Capture notable timings or runtime observations if the change is
   performance-sensitive.
3. Re-run the same lane on your branch.
4. Investigate regressions in:
   - runtime duration
   - repeated fallback/degraded paths
   - build size changes
   - unexpected test skips or failures

## When To Use Extra Scrutiny

Apply this guardrail for changes touching:

- `src/fleet_rlm/runtime/*`
- `src/fleet_rlm/integrations/daytona/*`
- `src/fleet_rlm/api/routers/ws/*`
- `src/frontend/src/lib/rlm-api/*`
- `src/frontend/src/lib/workspace/*`
- shared request/response or websocket event contracts
