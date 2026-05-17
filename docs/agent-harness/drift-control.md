# Drift Control

Drift control is deliberately built on existing project checks. The harness check adds repo-specific
structure without replacing the current validation lane.

## Primary Commands

```bash
# from repo root
make check-docs
make check-release
make api-check
```

`make check-docs` runs:

- `uv run python scripts/check_docs_quality.py`
- `uv run python scripts/check_harness_engineering.py`

`make check-release` runs:

- `uv run python scripts/validate_release.py hygiene`
- `uv run python scripts/validate_release.py metadata`
- `uv run python scripts/check_agents_md_freshness.py`

`make api-check` runs:

- `uv run python scripts/openapi_tools.py validate`
- `cd src/frontend && pnpm run api:check`

## Harness Check Coverage

`uv run python scripts/check_harness_engineering.py` fails when:

- root `AGENTS.md` exceeds the line budget,
- required `docs/agent-harness/` files are missing,
- docs indexes do not link the harness hub,
- `.codex` TOML/JSON files are not parseable,
- `.codex` bootstrap or hook scripts are missing,
- generated artifacts are documented without matching `make api-sync`, `make api-check`, and
  `make build-ui` commands,
- top-level `scripts/*.py` helpers are missing from `scripts/README.md`,
- retained top-level Python helpers do not respond to `--help`,
- backend or frontend structural boundaries drift in ways that agents can remediate.

## Generated Contract Drift

Generated artifacts stay honest through:

```bash
# from repo root
make api-sync
make api-check
make build-ui
```

Use `make api-sync` when API schemas or route metadata change. Use `make api-check` before finishing
contract changes. Use `make build-ui` only when packaged frontend assets must be refreshed.

## Docs Drift

Docs must be reachable from the indexes:

- `docs/README.md`
- `docs/index.md`
- `docs/SUMMARY.md`

When adding durable docs, link them from one of those indexes and keep `docs/SUMMARY.md` as the
complete navigation surface.

## Script Drift

`scripts/README.md` is the source of truth for retained helper scripts. New scripts need:

- an inventory row,
- a canonical invocation,
- `--help` behavior that exits without performing work,
- a `make` target or durable doc reference when they are part of routine workflows.
