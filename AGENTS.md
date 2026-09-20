# Fleet RLM — Agent Guide

Fleet is a Session-first FastAPI/SSE backend using DSPy and Daytona, with a
pi-tui client under `tools/fleet-tui/`. This file is an operating map, not a
roadmap: improve or replace an implementation when evidence supports it.

## Start with the code

- Inspect the affected implementation, tests, configuration, and generated
  contracts before deciding on a design. Current executable behavior outranks
  plans, historical receipts, and this guide.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for ownership and dependency seams;
  update it when a durable boundary changes.
- Use [CONTRIBUTING.md](CONTRIBUTING.md) for setup, [docs/index.md](docs/index.md)
  for task-specific guidance, and [tools/fleet-tui/AGENTS.md](tools/fleet-tui/AGENTS.md)
  for terminal-client work.
- Treat ADRs and status ledgers as context and evidence, not as permanent
  restrictions. Revise or supersede them deliberately when implementation
  changes their assumptions.

## Work safely and decisively

- Complete authorized local work and its relevant checks. Fix failures caused
  by the change without waiting for another approval.
- Preserve unrelated staged, unstaged, and untracked work. Do not reset,
  clean, stash, overwrite, or revert someone else's changes.
- Commits, pushes, pull requests, deployments, publication, and shared
  infrastructure changes need an explicit request.
- Provider, Daytona, database, and benchmark runs use their documented entry
  points and need explicit operator authorization. Report precisely what their
  evidence does and does not establish.
- Use `uv run` for Python. Keep secrets in configured environment references;
  never expose credentials, private paths, or raw infrastructure failures.

## Durable design rules

- `src/fleet_rlm/` is canonical. API routes are transport adapters and obtain
  services through composition rather than constructing infrastructure.
- Prefer the pinned native `dspy.RLM` and `dspy.LM`; do not add application
  LiteLLM coupling. Let DSPy own its `REPLHistory` and trajectory semantics.
- Scope mutable execution state to a Turn or child invocation. Settled work
  cannot be mutated by late provider, tool, interpreter, or recursive work.
- Keep SDK integration in `src/fleet_rlm/daytona/` and internal Runtime Events
  transport-neutral until the API projects them to clients.
- Let services that own lifecycle, persistence, settlement, cleanup, and
  schema evolution remain the source of truth; Alembic owns live migrations.
- Keep policy in `config/fleet.toml`, not application code. Generated contracts
  and client types must be regenerated from their owning sources.

| Generated artifact | Regenerate / verify |
| --- | --- |
| `openapi.yaml`, `tools/fleet-tui/src/generated/openapi.ts` | `make api-sync` / `make api-check` |
| TUI stream fixtures | `make stream-sync` / `make stream-check` |
| `docs/reference/profile-matrix.md` | `make profile-matrix` / `make check-docs` |

Bundled Skill Markdown is shipped runtime content; inspect its catalog and
contract tests when changing it.

## Validate proportionally

| Change | Minimum validation |
| --- | --- |
| Documentation or guidance | `make check-docs` |
| Focused Python | Relevant `uv run pytest -q`, `uv run ruff check`, and `uv run ruff format --check` |
| Typed Python interfaces | Also `uv run ty check src` |
| TUI | `make tui-check` |
| API, settings, streams, generated interfaces | Regenerate then `make api-sync` and `make api-check` |
| Boundaries, ownership, integrations | `make check-codebase-tree` and `make check-dependency-boundaries` |
| Cross-cutting lifecycle or configuration | `make check` |
| Release or security scope | `make check-security`, `make build-release`, and `make check-release` |

Before finishing, review the diff, run `git diff --check`, and state the
checks run and their evidence limits. Local checks do not certify a live,
release, security, or provider outcome.
