# Contributing to fleet-rlm

Fleet RLM is a Python 3.11–3.13 backend with a standalone TypeScript terminal
client. Keep changes focused, preserve architecture invariants, and update
documentation in the same change when behavior moves.

## Setup

Ordinary local development:

```bash
uv sync --all-extras --dev
uv run fleet-rlm --help
```

Codex Cloud workspaces use `zsh .codex/workspace-bootstrap.zsh`; do not run that
branch-guarded bootstrap as a generic local setup step.

Node 22.19+ and pnpm are required for `tools/fleet-tui/`:

```bash
pnpm --dir tools/fleet-tui install --frozen-lockfile
```

Use local `.env` files or an authorized secret manager for credentials. Runtime
configuration reads only environment names referenced by the selected TOML policy,
including provider-specific names such as `DATABRICKS_TOKEN`; see
[`docs/reference/configuration.md`](docs/reference/configuration.md). Never
commit credentials or use a Daytona API key as an API bearer token.

## Development workflow

[AGENTS.md](AGENTS.md) owns workflow boundaries and the validation matrix.
Use [ARCHITECTURE.md](ARCHITECTURE.md) when changing component ownership and
[the TUI guide](tools/fleet-tui/AGENTS.md) for terminal-client work.
The [testing strategy](docs/how-to-guides/testing-strategy.md) describes fixtures,
suite selection, and explicit live-test entry points.

## Documentation and generated files

- `docs/index.md` is the documentation reachability root.
- `ARCHITECTURE.md` describes current ownership and dependency direction.
- `scripts/README.md` inventories supported top-level helpers.
- Completed plans belong in Git history or the ignored local `.scratch/archive/`,
  not in the active tracked documentation tree.
- `make api-sync` regenerates both public OpenAPI and generated TUI HTTP types;
  `make api-check` verifies them together.

## Submitting changes

Use focused conventional commits such as `feat:`, `fix:`, `refactor:`, `test:`,
`docs:`, or `chore:`. Pull requests should describe before/after behavior, list
validation results, and call out any credentialed evidence still outstanding.

Report vulnerabilities through `SECURITY.md`, not a public issue.
