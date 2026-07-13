# Drift Control

## Primary Commands

```bash
# from repo root
make check
make check-docs
make check-release
make api-check
```

- `make check` is the backend format, lint, type, test, OpenAPI, and structural
  gate.
- `make check-docs` validates active documentation and harness structure.
- `make check-release` validates backend metadata, packaging, and agent guides.
- `make api-check` checks the root backend contract.

## Active Documentation

Current docs must be reachable from `docs/index.md` or `docs/SUMMARY.md`.
Superseded plans and removed-backend documentation remain available through Git
history rather than an excluded in-tree archive.

## Script and Contract Drift

Retained helpers must appear in `scripts/README.md` and support `--help`.
Regenerate the backend contract with:

```bash
make api-sync
make api-check
```

Neither command modifies client source.
