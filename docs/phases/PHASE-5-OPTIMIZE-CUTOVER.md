# Phase 5 — Offline Optimization (GEPA + ReAnchor) & Legacy Cutover

## Objective
Implement the offline optimization CLI (`optimize.py`) using **Neon branching**, **MLflow 3.18**, **`dspy.GEPA`**, and **`dspy.experimental.ReAnchor`**, then permanently delete all legacy `fleet-rlm` runtime subsystems.

## Files to Create
- `backend/src/fleet_rlm/optimize.py`
- `backend/tests/test_phase5_optimize.py`

## Technical Specifications (`optimize.py`)

Runs strictly out-of-band as a CLI (`uv run python -m fleet_rlm.optimize ...`). Never imported by `api.py`.

1. **Neon Branch Safety:**
   - Require `--database-url` pointing to an ephemeral **Neon branch** (refuse to run if `--database-url` matches production or lacks `--allow-direct` override).
2. **MLflow 3.18 Auto-Tracking:**
   - Enable `mlflow.dspy.autolog(log_compiles=True, log_evals=True, log_traces_from_compile=True)`.
3. **Three Independent Optimization Targets (`--target rlm | skills | memory`):**
   - **`rlm` (`dspy.GEPA`):** Loads labeled evaluation examples from approved Neon `runs` / JSONL dataset, splits train/holdout (e.g., 70/30), compiles `dspy.RLM` (or `dspy.Flex`) with `dspy.GEPA`, compares holdout score against baseline, and writes `artifacts/rlm_program.json` **only** when holdout improves (or `--force`).
   - **`skills` (`dspy.experimental.ReAnchor`):** Fits 5-fold cross-validated thresholds and choice weights for `SkillSelector` from labeled `(task, expected_skill)` pairs; writes `artifacts/skill_selector.json`.
   - **`memory` (`dspy.experimental.ReAnchor`):** Fits calibrated thresholds for `MemoryGate` from labeled `(turn_exchange, should_save, action, scope)` pairs; writes `artifacts/memory_gate.json`.
   - **Important:** When deploying `ReAnchor`-calibrated JSON files, pin the exact TypeSafe Jev model snapshot in `FLEET_DECISION_MODEL` (do not keep floating `typesafe:jev-latest`) so decision probabilities remain aligned with calibrated thresholds.
4. **Deployment:**
   - Set `FLEET_OPTIMIZED_PROGRAM_PATH`, `FLEET_SKILL_SELECTOR_PATH`, and/or `FLEET_MEMORY_GATE_PATH` and roll Uvicorn workers.

## Legacy Deletion Checklist (Hard Cutover)

Remove all of the following from the repository once Phases 1–4 pass their exit gates:
- [ ] In-memory `SessionRuntime` registry, `max_active_sessions` eviction, and stale-runtime trackers.
- [ ] PostgreSQL advisory-lock lease tables, heartbeat reconnect tasks, and lease-loss cancellation handlers.
- [ ] Per-turn remote Daytona probing scripts (installed package checks, workspace SHA-256 crawlers, `/learned` volume readers).
- [ ] Daytona-volume `shared/learned-notes` storage and word-overlap deduplication logic.
- [ ] TOML schema v5 config loaders, 6-tier config precedence resolvers, and the private loopback setup HTTP server.
- [ ] 4-mode execution router (`direct`/`rlm`/`flex`/`hybrid`) and ad-hoc triage layers.
- [ ] Custom 200-turn / 8-turn / 16k-char prompt truncation and custom SSE event vocabularies.
- [ ] Embedded MLflow process supervision and legacy Textual/web-TUI wrappers (unless intentionally archived in a separate repo).
- [ ] Live GEPA candidate/approval tables inside the serving FastAPI database.

## Final Release Exit Gate
```bash
cd backend
uv run ruff check .
uv run lint-imports
uv run pytest
cd ../frontend
pnpm typecheck && pnpm build
```
- **Done criteria:** Backend consists of the 7 clean modules (`config`, `db`, `interpreter`, `skills`, `agent`, `api`, `optimize`), passes `import-linter` with zero violations, scales cleanly across multiple Uvicorn workers against Neon Postgres 18, and streams recursive RLM + Subagent traces directly into the Vite + shadcn Base UI frontend.
