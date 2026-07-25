# Scripts Agent Guide

Standalone maintenance, validation, and benchmark scripts. One-way dependency:
scripts import the installed `fleet_rlm` package; the backend never imports scripts.

## Conventions

- Every script defines `main(argv=None) -> int` with `argparse`, invoked via
  `raise SystemExit(main())`.
- Resolve repo root: `ROOT = Path(__file__).resolve().parents[1]`.
- Load env via `dotenv.load_dotenv(..., override=False)` — explicit process
  exports still win.
- Print errors to `sys.stderr`; return non-zero on failure.

## Validation

```bash
make check-codebase-tree   # AST boundary enforcement (imports check_codebase_tree.py)
make check-docs            # Docs quality (check_docs_quality.py)
make api-sync              # OpenAPI + generated TUI types (openapi_tools.py)
```

## Credential Boundary

- Benchmark and live scripts require explicit `FLEET_LIVE=1`.
- Never hardcode or log credentials; read from env after dotenv load.
- `scripts/benchmarks/` is self-contained (OOLONG, SNIah scorers).

## Quality-Gate Scripts

| Script | Gate |
|--------|------|
| `check_codebase_tree.py` | Daytona SDK isolation + route injection boundary |
| `check_docs_quality.py` | Canonical doc presence and freshness |
| `check_harness_engineering.py` | Agent-harness engineering invariants |
| `check_agents_md_freshness.py` | AGENTS.md drift detection |
| `validate_release.py` | Release hygiene, metadata, wheel |
