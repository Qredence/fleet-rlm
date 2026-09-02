# Fleet TUI — Agent Instructions

This file adds rules specific to `tools/fleet-tui/`.

Repository-wide rules from [AGENTS.md](../../AGENTS.md) still apply.

The maintained Fleet client is a pi-tui TypeScript terminal application. It consumes the backend's public stream/API contracts; it does not define a second execution protocol.

## Tooling

- Node and pnpm versions are defined by the workspace.
- Use pnpm from `tools/fleet-tui/`.
- Run the complete TUI validation lane with `make tui-check`.

Do not hand-edit generated files under `src/generated/`.

When the backend API or stream source contract changes, regenerate through the repository's root generation commands.

## Architecture

- `fleet-turn-stream.ts` owns strict Turn stream lifecycle.
- `sse.ts` owns SSE/frame/chunk validation.
- Live and durable projections convert backend contracts into client state.
- `store.ts` owns state transitions; mutate state through the established dispatch/reducer path.
- Transcript/screen/presenter modules own presentation, not backend execution semantics.
- Slash commands use the established command registry/facade rather than independent parsing paths.

Prefer extending these ownership boundaries instead of creating parallel state or protocol mechanisms.

## Stream invariants

The backend owns the public stream contract.

Preserve:

- one stream start;
- ordered intermediate chunks;
- one terminal outcome;
- `[DONE]` last.

Do not infer RLM recursion, depth, settlement, or execution state from model text or presentation details when the backend exposes typed evidence.

Live and durable projections should converge on equivalent user-visible state for equivalent committed information.

## State and errors

Use the established state reducer/dispatch model.

Do not directly mutate shared TUI state.

Use typed Fleet API errors and preserve bounded public error information.

Do not display `.env`, provider credentials, or backend-private infrastructure details.

## Settings

Settings/profile UI edits non-secret `config/fleet.toml` policy through the backend settings contract.

Do not read or expose secret environment values in the TUI.

Treat profile changes as restart-target policy unless the backend contract explicitly says otherwise.

## Validation

For focused TypeScript changes, run the relevant workspace checks/tests.

Before completing substantial TUI work, run:

```bash
make tui-check
```

Run root `make api-check` / generation commands when public backend contracts changed.

Always run:

```bash
git diff --check
```

before completion.
