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
- `make api-check` checks root `openapi.yaml` without synchronizing frontend
  contracts.

## Active Documentation

Current docs must be reachable from `docs/index.md` or `docs/SUMMARY.md`.
Documents under `docs/internal/legacy-backend/` are excluded because they
describe the deleted backend and exist only as historical evidence.

## Script and Contract Drift

Retained helpers must appear in `scripts/README.md` and support `--help`.
Regenerate the backend contract with:

```bash
make api-sync
make api-check
```

Neither command modifies `src/frontend/`.
