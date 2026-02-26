# Code Quality, Maintainability, Efficiency, and Simplification Audit

Date: 2026-02-25
Scope: changed files + hotspot modules
Validation depth: check-only gates

## Validation Baseline

The following checks were run in check-only mode:

- `uv run ruff check src tests`
- `uv run ruff format --check src tests`
- `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"`
- `bun run type-check` (frontend)
- `bun run lint` (frontend)
- `uv run pytest -q tests/unit/test_config.py tests/unit/test_react_tools.py`

## Findings by Priority

### 1) Correctness Risk

#### 1.1 Config default drift between Hydra and Pydantic
- Why it matters: runtime behavior can differ by entrypoint depending on whether config is loaded from Hydra YAML vs direct model defaults.
- Affected files/functions: `src/fleet_rlm/conf/config.yaml`, `src/fleet_rlm/config.py`, `tests/unit/test_config.py`.
- Recommendation: keep Hydra as source of truth and parity-test shared defaults.
- Risk: High.
- Effort: Small.

#### 1.2 Stream lifecycle rules differ by stream type but were coupled in one implementation path
- Why it matters: chat stream and execution stream have different terminal semantics; coupling increases regression risk.
- Affected files/functions: `src/frontend/src/lib/rlm-api/wsClient.ts` (now split into parser/reconnect/adapter modules).
- Recommendation: keep terminal behavior configurable by stream adapter and test explicitly.
- Risk: High.
- Effort: Medium.

### 2) Maintainability Debt

#### 2.1 Duplicated runner argument plumbing
- Why it matters: parameter forwarding drift is likely when the same option surface is repeated across sync/async wrappers.
- Affected files/functions: `build_react_chat_agent`, `run_react_chat_once`, `arun_react_chat_once` in `src/fleet_rlm/runners.py`.
- Recommendation: centralize construction in an internal options object + shared builder helper while preserving public APIs.
- Risk: Medium.
- Effort: Medium.

#### 2.2 Monolithic websocket client logic
- Why it matters: parsing, reconnect orchestration, and stream-specific behavior in one file increases cognitive load and test friction.
- Affected files/functions: `src/frontend/src/lib/rlm-api/wsClient.ts` and related tests.
- Recommendation: split by concern (`wsTypes`, `wsFrameParser`, `wsReconnecting`) and keep `wsClient.ts` as stable facade.
- Risk: Medium.
- Effort: Medium.

### 3) Efficiency / Performance Risk

#### 3.1 File listing over large trees can become expensive
- Why it matters: `list_files` with wide patterns on large repos can be slow and return many stat calls.
- Affected files/functions: `src/fleet_rlm/react/tools/filesystem.py:list_files`.
- Recommendation: keep ignore-dir filtering, preserve glob semantics, and document benchmark workflow for large trees.
- Risk: Medium.
- Effort: Small.

#### 3.2 Large hotspot modules are maintenance bottlenecks
- Why it matters: high line-count modules (e.g., websocket router, interpreter, agent/streaming) are harder to reason about and optimize.
- Affected files/functions: `src/fleet_rlm/server/routers/ws/api.py`, `src/fleet_rlm/core/interpreter.py`, `src/fleet_rlm/react/streaming.py`, `src/fleet_rlm/react/agent.py`.
- Recommendation: prefer boundary extraction by feature seams (event mapping, persistence hooks, lifecycle orchestration) with no behavior changes.
- Risk: Medium.
- Effort: Large.

### 4) Simplification Opportunities

#### 4.1 Standardize shared option bundles in backend service constructors
- Why it matters: smaller public wrappers + centralized defaults reduce accidental divergence.
- Affected files/functions: `src/fleet_rlm/runners.py`.
- Recommendation: continue adopting internal options objects where repeated argument sets exist.
- Risk: Low.
- Effort: Medium.

#### 4.2 Keep API adapter files as stable facades
- Why it matters: moving logic into internals while preserving external import surface avoids frontend churn.
- Affected files/functions: `src/frontend/src/lib/rlm-api/index.ts`, `src/frontend/src/lib/rlm-api/wsClient.ts`.
- Recommendation: maintain facade pattern and avoid direct imports from internal parser/reconnect modules outside adapter.
- Risk: Low.
- Effort: Small.

## Filesystem Micro-Benchmark Guidance

Use this non-mutating measurement approach for large repositories:

1. Measure current `list_files` with representative patterns:
   - `**/*`
   - `src/**/*.py`
   - `*.md`
2. Record:
   - wall-clock time
   - count returned
   - total bytes reported
3. Compare before/after any performance-oriented refactor using the same path/pattern matrix.
4. Keep semantics checks in place:
   - direct child inclusion for `**`
   - ignored directory exclusion
   - `count` vs capped `files` list behavior.

## Repo Hygiene Observations

- `package-lock.json` is present at repo root while frontend is Bun-managed (`src/frontend/package.json` has `packageManager: bun@...`).
- Recommendation: remove accidental npm lockfiles from this repo unless npm is intentionally introduced for a specific workspace.

## Suggested Next Increments

1. Continue modular extraction in backend hotspot files starting with `ws.py` helper seams.
2. Add a lightweight CI check that fails on lockfile mismatch (for example, root `package-lock.json` in Bun-only projects).
3. Add benchmark snapshots for `list_files` in representative repository sizes.
