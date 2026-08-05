# Optimizer Lane (`scripts/optimize/`)

Offline Fleet signature optimization with GEPA `optimize_anything`. Root
workflow and safety rules remain authoritative from
[AGENTS.md](../../AGENTS.md); this guide narrows them for the optimizer lane.
One-way dependency: scripts import the installed `fleet_rlm` package; the
backend never imports scripts.

## Hard rules

- The FRONTIER model tier (`rlm/lm_factory.py:156`) is reserved for offline
  reflection/proposal work. It must never appear inside a live Turn path; this
  directory is the only place it is wired.
- `pyproject.toml [tool.uv] override-dependencies` pins gepa to the omni
  commit `0310bb7` (blog, 2026-07-22), a documented deviation from
  dspy==3.3.0's declared `gepa[dspy]==0.1.1`. `make check` must stay green on
  every bump of that commit. Native omni (`optimize_best_of` explore + fresh
  gepa continue) is the default via `--engine auto`; `--engine gepa` forces the
  single-engine fallback composition; `autoresearch`/`meta_harness` shell out
  to the local `claude` CLI.
- Upstream-envelope note: claude CLI >=2.1.219 emits a JSON array envelope;
  `_apply_claude_json_envelope_shim` in `optimize_signature_omni.py`
  normalizes it in place until gepa fixes `_parse_proposer_result` /
  `_extract_claude_cost` upstream. Do not carry the shim into `src/fleet_rlm/`.
- The optimizer stays a scripts lane — no Fleet HTTP surface. Candidate REPL
  execution is in-process and agent engines shell out to the local `claude`
  CLI, so wrapping it in the API would relax the BYOK loopback trust model for
  zero functional gain; receipts + UC datasets + server-side monitoring are the
  canonical outputs. Do not add `/api` routes for optimization jobs.
- Optimized candidates are never auto-applied. Promotion is a human code
  review that edits `src/fleet_rlm/rlm/signature.py` manually; receipts and
  candidate files are evidence, not deployment.
- The default executor runs candidate REPL code through the in-process
  interpreter backend. Run optimization only from trusted hosts; sandboxed
  (Daytona) execution for the evaluator is tracked follow-up work.
- Credentialed runs require explicit `FLEET_LIVE=1` and read secrets from env
  after `dotenv.load_dotenv(..., override=False)`; never log tokens.
- `optimize_signature_gepa.py development-smoke` calls the reusable
  `run_development_smoke` guard, so direct callers cannot construct the
  FRONTIER model or GEPA without the same live opt-in.

## Validation

```bash
uv run pytest tests/unit/optimization tests/unit/scripts/test_optimize_signature_gepa.py tests/unit/scripts/test_optimize_signature_omni.py -q
uv run ruff check scripts/optimize tests/unit/scripts
```
