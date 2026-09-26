# Contributing to Fleet RLM

Fleet is a Python 3.11–3.13 backend with a standalone TypeScript terminal
client. Start with the [README](README.md) for a short system overview, the
[architecture](ARCHITECTURE.md) for ownership boundaries, and
[AGENTS.md](AGENTS.md) for repository operating rules.

## Set up a local checkout

Install uv and a supported Python version. Node 22.19+ and pnpm are needed for
the maintained client and the full check. The setup and checks below do not
require provider, Daytona, or database credentials:

```bash
uv sync --all-extras --dev
pnpm --dir tools/fleet-tui install --frozen-lockfile
uv run fleet --help
make check
```

For a focused change, run its relevant `uv run pytest -q` tests and the
matching validation lane from [AGENTS.md](AGENTS.md#work-and-validate-safely).
`make check-docs` covers authored documentation, repository guidance, and the
generated profile-matrix check. The [testing strategy](docs/how-to-guides/testing-strategy.md)
explains suite selection and what the non-live gate proves.

To run a real Session, follow the [optional live setup](README.md#run-a-live-local-session).
The selected profile names its secrets in `config/fleet.toml`; the
[profile matrix](docs/reference/profile-matrix.md) lists the exact environment
variables. Fleet's database must be migrated explicitly before serving. Use
local `.env` files or an authorized secret manager; never commit credentials
or use a Daytona API key as an API bearer token.

Codex Cloud workspaces use `.codex/environments/environment.toml` to run
`zsh .codex/workspace-bootstrap.zsh` automatically. That bootstrap requires a
feature branch based on `origin/main`; ordinary local setup should use the
commands above.

## Make a change

1. Read the affected code, tests, policy, and generated contracts. Start with
   the [source layout](docs/reference/source-layout.md) for file ownership and
   the [TUI agent guide](tools/fleet-tui/AGENTS.md) for terminal-client work.
2. Keep behavior in its owning package. API routes validate and project
   transport; `TurnRuntime` coordinates a Run; Daytona owns provider resources.
   Update architecture guidance when a durable owner or trust boundary moves.
3. Add or update regression tests in the existing behavior-owning suite. Keep
   documentation aligned with the behavior being changed.
4. Run the focused checks, inspect the final diff, and run `git diff --check`.
   Credentialed provider, Daytona, database, and benchmark lanes need an
   explicit operator decision and their documented entry points.

## Documentation and generated files

- `docs/index.md` is the documentation navigation root. Keep current guidance
  separate from dated plans, measurements, and receipts.
- `ARCHITECTURE.md` owns durable boundaries; `docs/reference/source-layout.md`
  owns the literal package map; `scripts/README.md` inventories supported
  top-level helpers.
- Regenerate OpenAPI and TUI HTTP types with `make api-sync`, stream fixtures
  with `make stream-sync`, and the profile matrix with `make profile-matrix`.
  Do not hand-edit their outputs.
- Bundled Skill Markdown is shipped runtime content. Check its catalog,
  manifests, resources, and contract tests when changing it.

## Submit work

Use focused conventional commits such as `feat:`, `fix:`, `refactor:`, `test:`,
`docs:`, or `chore:`. A pull request should describe the behavior change,
validation results, and any credentialed evidence still outstanding. Report
vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue.
