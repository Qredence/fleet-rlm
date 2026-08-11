# Scripts Agent Guide

Root workflow and safety rules remain authoritative from
[AGENTS.md](../AGENTS.md); this guide narrows them for `scripts/`.

Standalone maintenance, validation, and benchmark scripts. One-way dependency:
scripts import the installed `fleet_rlm` package; the backend never imports scripts.

## Conventions

- Prefer `main(argv=None) -> int` with `argparse` for new executable entry
  points and invoke it via `raise SystemExit(main())`; reusable scorer/helper
  modules need no CLI entry point.
- Always run scripts via `uv run` (e.g., `uv run python scripts/check_docs_quality.py`).
  Never use bare `python3` or `python`.
- Resolve the repo root from `Path(__file__)`, preserving the local module's
  established constant name.
- Credentialed scripts that load `.env` use
  `dotenv.load_dotenv(..., override=False)` so process exports still win.
- Sibling imports across `scripts/benchmarks/`/`scripts/optimize/` go through the
  established repo-root `sys.path` bootstrap (`openapi_tools.py:16`), keeping both
  direct-file and `-m` module execution working; shared Fleet judge definitions
  live only in `scripts/benchmarks/judges.py`.
- Print errors to `sys.stderr`; return non-zero on failure.

## Validation

```bash
make check-codebase-tree   # AST boundary enforcement (imports check_codebase_tree.py)
make check-docs            # Docs quality (check_docs_quality.py)
make api-sync              # OpenAPI + generated TUI types (openapi_tools.py)
```

## Credential Boundary

- The Phase 1 live proof and Prime Oolong benchmark use the selected TOML
  policy's `runtime.live_enabled` switch (true by default; false fails closed)
  before credentials or models are constructed. The separate Databricks
  quality-loop scripts and GEPA execution retain their explicit
  `FLEET_LIVE=1` gate until that lane is migrated as its own phase.
- Never hardcode or log credentials; read from env after dotenv load.
- `scripts/benchmarks/` owns routing/lifecycle benchmarks and Databricks
  quality-loop helpers (including shared Fleet judge definitions);
  `run_routing_eval.py` measures the Python → Sub-LM → child-RLM delegation
  ladder from public evidence. `scripts/optimize/` remains the trusted-host
  signature-optimization lane.

## Quality-Gate Scripts

| Script | Gate |
|--------|------|
| `check_codebase_tree.py` | Daytona SDK isolation + route injection boundary |
| `check_docs_quality.py` | Canonical doc presence and freshness |
| `check_harness_engineering.py` | Agent-harness engineering invariants |
| `check_agents_md_freshness.py` | AGENTS.md drift detection |
| `validate_release.py` | Release hygiene, metadata, wheel |
