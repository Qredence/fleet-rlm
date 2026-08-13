# Drift Control

## Primary commands

```bash
make check
make check-release
make check-security
make build-release
git diff --check
```

- `make check` runs Python lint/format/type checks, the isolated non-live test
  suites, OpenAPI/TUI type drift, all pi-tui checks, codebase boundaries, and
  documentation/harness checks.
- `make check-release` validates hygiene, package metadata, and `AGENTS.md`.
- `make check-security` runs the configured dependency audit and Bandit lane.
- `make build-release` builds and validates the Python distributions.
- `git diff --check` is a separate required worktree check; it is not part of
  `make check`.

## Active documentation

Current docs must be reachable from `docs/index.md` or `docs/SUMMARY.md`.
Completed local plans and receipts may be retained under ignored
`.scratch/archive/`; they are evidence, not current specification.

## Script and contract drift

Retained helpers must appear in `scripts/README.md` and support `--help` where
applicable. `make api-sync` owns three checked-in artifacts:

```bash
make api-sync   # openapi.yaml + tools/fleet-tui/src/generated/openapi.ts
                # + tools/fleet-tui/src/generated/fleet-ui-chunk-validation.ts
make api-check  # verifies all three
```

Any generated diff must be reviewed as a public contract change.
