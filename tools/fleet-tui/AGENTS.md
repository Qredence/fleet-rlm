# Fleet TUI Agent Guide

Root workflow and safety rules remain authoritative from
[AGENTS.md](../../AGENTS.md); this guide narrows them for `tools/fleet-tui/`.

pi-tui-only terminal client. No web frontend, no React, no browser runtime.

## Ownership

| File | Responsibility |
|------|---------------|
| `src/fleet-turn-stream.ts` | Strict SSE stream lifecycle (start → … → terminal → [DONE]) |
| `src/sse.ts` | Frame/chunk validation against `FleetUIMessageChunk` union |
| `src/tui/projection.ts` | Live and reload durable-turn projection |
| `src/tui/store.ts` | Atomic hydration; all state via `dispatch` + pure `reduce` |
| `src/tui/commands.ts` | Slash commands, including loopback-only TOML policy editing |
| `src/tui/draft-store.ts` | Debounced per-Session draft/selection persistence (`FLEET_TUI_STATE_DIR`) |
| `src/generated/openapi.ts` | **Generated** — do not hand-edit; use `make api-sync` |
| `src/generated/fleet-ui-chunk-validation.ts` | **Generated** — do not hand-edit; use `make api-sync` (`scripts/generate_tui_chunk_validation.py`) |

## Validation

```bash
make tui-check        # api-check + format:check + lint + typecheck + test (Vitest)
```

Individual lanes: `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm format:check`.

## Constraints

- Monochrome operator timeline; no mouse capture, no transcript viewport.
- Single client protocol: the AI SDK UI v1 stream projected by
  `src/fleet_rlm/api/sse.py`, with HTTP types owned by `make api-sync`. Do not
  design a second client protocol unless a second client exists.
- State mutations exclusively through `store.dispatch`; no direct mutation.
- SSE ordering invariants are enforced in `streamFleetTurn` — one start, one terminal, [DONE] last.
- API errors use `FleetApiError` with `status`, `correlationId`, `code`.
- Keep focused tests beside their source level under `src/tests/` or
  `src/tui/tests/`; shared behavior may be covered through its owning feature.
- `/settings` reads and edits non-secret `config/fleet.toml` policy only; it
  never displays `.env` values and saved changes require a Fleet restart.
- `/profiles` switches the active Fleet profile by PATCHing
  `config.default_profile` to the same loopback policy; it opens a dropdown of
  available profiles and requires a Fleet restart to take effect.
- Requires Node ≥ 22.19.0 and pnpm.
