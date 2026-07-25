# Fleet TUI Agent Guide

pi-tui-only terminal client. No web frontend, no React, no browser runtime.

## Ownership

| File | Responsibility |
|------|---------------|
| `src/fleet-turn-stream.ts` | Strict SSE stream lifecycle (start → … → terminal → [DONE]) |
| `src/sse.ts` | Frame/chunk validation against `FleetUIMessageChunk` union |
| `src/tui/projection.ts` | Live and reload durable-turn projection |
| `src/tui/store.ts` | Atomic hydration; all state via `dispatch` + pure `reduce` |
| `src/generated/openapi.ts` | **Generated** — do not hand-edit; use `make api-sync` |

## Validation

```bash
make tui-check        # api-check + format:check + lint + typecheck + test (Vitest)
```

Individual lanes: `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm format:check`.

## Constraints

- Monochrome operator timeline; no mouse capture, no transcript viewport.
- State mutations exclusively through `store.dispatch`; no direct mutation.
- SSE ordering invariants are enforced in `streamFleetTurn` — one start, one terminal, [DONE] last.
- API errors use `FleetApiError` with `status`, `correlationId`, `code`.
- Each feature file pairs with `src/tui/tests/<file>.test.ts`.
- Requires Node ≥ 22.19.0 and pnpm.
