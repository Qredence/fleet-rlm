# Fleet TUI — Agent Guide

These rules apply to `tools/fleet-tui/`. The root [AGENTS.md](../../AGENTS.md)
sets repository-wide ownership, safety, and validation rules. The TUI is a
TypeScript pi-tui client of Fleet's HTTP/SSE contract; it does not own backend
execution or runtime policy.

## Client boundaries

- `fleet-turn-stream.ts` owns the Turn stream lifecycle. `sse.ts` parses SSE
  frames and validates typed chunks against the generated contract.
- Live and durable adapters project backend information into client state.
  `store.ts` owns state transitions through dispatch and reducers; do not mutate
  shared state directly.
- Transcript, screen, presenter, and rendering modules own presentation, not
  execution semantics. Add slash commands through the command registry and
  facade, without creating a second parsing path.
- Keep equivalent committed information consistent between live and durable
  projections. Use typed backend evidence for recursion, depth, settlement, and
  execution state; never infer those from model text or presentation details.

## Turn stream contract

- A normal stream has one `turn_start`, ordered intermediate chunks, one
  terminal `turn_finish`, and `[DONE]` last. Transient `turn_status` chunks
  (phase/detail/message heartbeats) may precede `turn_start`.
- A claim or preparation failure can end as `turn_error` then `turn_finish`
  (status `error`), followed by `[DONE]`.
- Cancellation emits `turn_cancelled` then `turn_finish`, followed by `[DONE]`,
  without post-terminal usage. Do not accept chunks after the terminal outcome
  or `[DONE]`.
- Preserve backend chunk types and ordering through parsing and projection.
  Update generated validation or fixtures through the root guide's owning
  commands; never hand-edit `src/generated/`.

## Settings and private data

- Settings edit non-secret `config/fleet.toml` policy through the backend API.
  Saved configuration changes require a restart.
  The client does not enable child tools or select execution policy locally.
- Use typed Fleet API errors and bounded public error details. Never expose
  secret environment values, credentials, provider-private paths, or raw
  infrastructure errors. User-authorized Workspace paths remain governed by
  the backend file contract.

## Tooling and checks

- Use the Node and pnpm versions declared by this package and run pnpm commands
  from `tools/fleet-tui/` so the pinned package manager resolves correctly.
- Run root Make targets from the repository root. `make tui-check` runs
  generated API and stream checks plus TUI format, lint, typecheck, and tests.
- For backend contract changes, regenerate from the backend source with
  `make api-sync` or `make stream-sync`; then run the corresponding check lane.
  Documentation-only changes use `make check-docs` and do not need a terminal
  launch or live backend.
