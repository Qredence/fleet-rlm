# Fleet TUI — Agent Instructions

Applies to `tools/fleet-tui/`; repository rules and validation selection come from [AGENTS.md](../../AGENTS.md).
The maintained client is a pi-tui TypeScript terminal application consuming backend HTTP/SSE contracts.

## Tooling

- Use workspace-defined Node/pnpm versions and run pnpm from this package.
- Run root Make targets from the repository root; TUI code uses the root guide's TUI lane.
- Never hand-edit `src/generated/`; regenerate changed backend contracts using the root guide's source commands.
- Documentation-only changes use the documentation lane without a terminal launch or live backend.

## Ownership

- `fleet-turn-stream.ts` owns strict Turn stream lifecycle; `sse.ts` owns frame/chunk validation.
- Live and durable projections convert typed backend information into client state.
- `store.ts` owns transitions through dispatch/reducers; do not directly mutate shared state.
- Transcript/screen/presenter modules own presentation, not execution semantics.
- Slash commands extend the command registry/facade, without parallel parsing paths.

## Stream contract

Preserve one stream start, ordered intermediate chunks, one terminal outcome, and `[DONE]` last.
Cancellation emits `abort` then `[DONE]`, without `finish` or post-terminal usage.
Transient preparation heartbeats may precede `start`.

Use typed backend evidence for recursion, depth, settlement, and execution state;
do not infer these from model text or presentation details.
Live and durable projections must converge for equivalent committed information.

## Settings and errors

Settings edit non-secret `config/fleet.toml` policy through the backend contract.
Profile changes target a restart unless that contract explicitly says otherwise;
the client does not independently enable child tools or switch execution policy.

Use typed Fleet API errors and bounded public error information.
Never read or display secret environment values, credentials, or private infrastructure errors.
