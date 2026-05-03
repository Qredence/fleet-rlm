# Fleet RLM Code Analysis

This directory contains a repository-specific technical audit of the current `fleet-rlm` codebase on branch `feat/continuous-longcot-feedback-metric`.

The analysis focuses on three questions:

1. How the repository is organized today, and whether that organization fits the product.
2. What the current branch changes imply for LongCoT Mini benchmarking and result interpretation.
3. How GEPA is integrated into `fleet-rlm`, and what should change before the optimization path becomes a long-term product surface.

## Recommended Reading Order

1. `codebase-map.md` - repository structure, entry points, dependency flow, and ownership map.
2. `architecture-review.md` - design review of the current backend, runtime, frontend, persistence, and optimization architecture.
3. `benchmark-analysis.md` - current branch benchmark review, with special attention to LongCoT Mini artifacts and claims.
4. `gepa-integration-review.md` - GEPA-specific implementation review, including the LongCoT module and optimization APIs.
5. `refactor-and-fix-suggestions.md` - concrete recommendations with priority, affected files, benefits, and tradeoffs.
6. `risks-and-open-questions.md` - unresolved assumptions and decisions to settle before a rewrite or major refactor.

## Scope And Evidence Base

The report is based on repository inspection, branch diff analysis against `main`, and local benchmark artifacts already present in the working tree. No runtime code was changed, and no new benchmark or test run was executed as part of this audit.

Primary evidence sources include:

- `AGENTS.md`, `.github/copilot-instructions.md`, `src/fleet_rlm/AGENTS.md`, and `src/frontend/AGENTS.md`
- `pyproject.toml`, `Makefile`, `src/frontend/package.json`, and `openapi.yaml`
- Backend entry points in `src/fleet_rlm/api/`, `src/fleet_rlm/runtime/`, and `src/fleet_rlm/integrations/`
- GEPA and optimization modules in `src/fleet_rlm/runtime/quality/`
- Optimization API routes in `src/fleet_rlm/api/routers/optimization/`
- Frontend optimization surface under `src/frontend/src/features/optimization/`
- Branch benchmark scripts and artifacts under `scripts/`, `scripts/benchmarks/`, `output/longcot-eval/`, `research/`, and `mlflow_verification_report.md`
- Tests under `tests/unit/`, `tests/ui/`, and `tests/integration/`

The repository had uncommitted and untracked files during analysis. Those files are treated as part of the current working branch because the requested task explicitly targets the current codebase and branch state.

## High-Level Findings

The project has a coherent intended architecture: a thin FastAPI/WebSocket transport shell, a shared DSPy/ReAct runtime, Daytona-backed execution and persistence integrations, and a React frontend organized by product surface. The main product boundaries are documented and mostly reflected in code.

The strongest design elements are the transport/runtime/integration layering, the lazy app bootstrap, the canonical WebSocket split, the Daytona child isolation policy, the local and Postgres persistence abstractions, and the emerging registry-based optimization path.

The highest-complexity areas are concentrated in a few files:

- `src/fleet_rlm/runtime/models/builders.py`
- `src/fleet_rlm/runtime/tools/rlm_delegate.py`
- `src/fleet_rlm/api/routers/optimization/runs.py`
- `src/fleet_rlm/api/routers/optimization/background.py`
- `src/fleet_rlm/integrations/local_store.py`

The branch's LongCoT Mini benchmark work is promising, but the current artifacts do not yet support strong claims about benchmark superiority or GEPA-driven improvement. The reported RLM result improves overall accuracy from 13 percent to 33 percent in the final artifacts, but the methodology has important caveats: incomplete transport coverage, hardcoded local artifacts, evaluator/metric mismatch, domain regressions, unsupported statistical claims, and stale or contradictory formatting interpretations.

GEPA integration is structurally close to the right direction: module registry, artifact manifests, async run persistence, prompt snapshots, and API/UI surfaces are all in place. The main issue is that the system currently has two optimization paths with different assumptions: a generic MLflow-coupled path and a registry-based offline module path. The API and UI still hard-gate optimization availability on MLflow even though the registry runner is written to work without MLflow.

## Most Important Recommendations

1. Make the optimization product contract explicit: either GEPA module runs require MLflow everywhere, or module runs are offline-first and MLflow logging is optional.
2. Replace static frontend optimization module slugs with the `/api/v1/optimization/modules` registry response.
3. Fix LongCoT benchmark reproducibility before using it as a headline result: track exact dataset slice, prompt, evaluator commit, model config, run IDs, and per-task traces.
4. Do not use the current LongCoT GEPA dataset as proof of benchmark improvement. It excludes the `logic` domain and validates against a different metric from the official evaluator.
5. Split `runtime/models/builders.py` and `runtime/tools/rlm_delegate.py` along responsibility boundaries before adding more recursive runtime or GEPA behavior.
6. Consolidate optimization run execution paths so blocking API, async API, CLI, and background workers use one runner contract.

## Context7 Note

The repository source, local docs, installed workflow files, and test coverage were sufficient to evaluate the implementation choices. No additional external library or API context was required to make the repository-specific findings in these reports.
