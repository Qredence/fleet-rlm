# Architecture Review

## Executive Assessment

The current architecture is directionally sound for the product: a web-first adaptive RLM workbench with Daytona-backed execution, DSPy/ReAct runtime logic, local and Postgres persistence, and an optimization surface. The codebase has clear architectural intentions and many of the right boundaries.

The main problem is not the top-level architecture. The main problem is responsibility accretion inside a small number of central files and partially duplicated product paths around optimization, GEPA, benchmarks, and runtime delegation.

The architecture is appropriate if the project remains centered on these product surfaces:

- Workbench
- Volumes
- Optimization
- Settings
- History

The architecture becomes harder to maintain if every new benchmark, recursive runtime mode, GEPA workflow, and data export path is added directly into existing central modules instead of being isolated behind registry, runner, adapter, and persistence boundaries.

## What Is Well Designed

### Transport Shell Is Mostly Thin

Evidence:

- `src/fleet_rlm/api/main.py`
- `src/fleet_rlm/api/bootstrap.py`
- `src/fleet_rlm/api/routers/ws/endpoint.py`
- `src/fleet_rlm/api/routers/ws/stream.py`

`create_app()` is mostly responsible for FastAPI assembly, route registration, lifespan wiring, docs, and SPA mounting. Runtime bootstrap is isolated in `startup_server_state()`. WebSocket frame handling is separated from runtime construction through `prepare_chat_runtime()`.

This is a good boundary because FastAPI routing does not need to know the details of recursive RLM execution, Daytona evidence sinks, or DSPy module construction.

### Runtime And Integration Layers Are Conceptually Separate

Evidence:

- Runtime: `src/fleet_rlm/runtime/`
- Daytona integration: `src/fleet_rlm/integrations/daytona/`
- Persistence integration: `src/fleet_rlm/integrations/database/`
- Observability integration: `src/fleet_rlm/integrations/observability/`

The project has the right high-level split: runtime logic lives under `runtime`, provider-specific logic lives under `integrations`, and transport logic lives under `api`.

This makes the codebase extensible in principle. For example, `EvidenceSink` in `src/fleet_rlm/runtime/models/evidence.py` gives runtime code a provider-neutral interface for evidence persistence.

### Daytona Child Isolation Policy Is Centralized

Evidence:

- `src/fleet_rlm/integrations/daytona/child_isolation.py`
- `src/fleet_rlm/integrations/daytona/interpreter.py`
- `src/fleet_rlm/runtime/tools/rlm_delegate.py`

Child sandbox behavior is not scattered randomly through the runtime. The policy module defines how `auto`, `fork`, `clean`, and `context` isolation modes work. This matters because recursive execution is one of the system's most failure-prone and security-sensitive areas.

The current policy also distinguishes volume-mounted parents from no-volume parents, which is the correct kind of substrate-aware decision to keep inside the Daytona integration layer.

### Lazy Imports In Quality Package Are A Good Correction

Evidence:

- `src/fleet_rlm/runtime/quality/__init__.py`

The quality package now uses a lazy `__getattr__` export map. This avoids importing DSPy, MLflow, or GEPA-heavy modules just because the quality package is imported.

This is important because the repository's own instructions warn against import-time provider side effects in config and package-root modules. The note in `src/fleet_rlm/AGENTS.md` that `runtime/quality/__init__.py` imports DSPy at top level appears stale and should be updated.

### Optimization Registry Is The Right Direction

Evidence:

- `src/fleet_rlm/runtime/quality/module_registry.py`
- `src/fleet_rlm/runtime/quality/optimization_runner.py`
- `src/fleet_rlm/runtime/quality/optimize_longcot.py`

`ModuleOptimizationSpec` gives GEPA modules a clean registration contract: slug, display name, description, program factory, metric factory, dataset requirements, and output field. `run_module_optimization()` can then load examples, split train/validation data, run GEPA, evaluate results, capture prompt snapshots, and write artifacts.

This is a better long-term architecture than hardcoding each optimization workflow in an API route or CLI command.

## Questionable Or Fragile Areas

### `runtime/models/builders.py` Has Too Many Responsibilities

Evidence:

- `create_runtime_rlm()`
- `build_recursive_subquery_rlm()`
- `RLMVariableExecutionModule`
- `GroundedAnswerSynthesisModule`
- `RecursiveWorkspaceModule`
- `_SUBQUERY_FAILURE_MARKERS`
- memory extraction and summarization helpers

This file currently mixes:

- DSPy LM configuration and RLM construction.
- Context window estimation.
- Core memory parsing and synthesis.
- Grounded answer synthesis.
- Recursive workspace planning.
- Subquery execution.
- Aggregation verification.
- Repair planning.
- Failure marker classification.
- Evidence reference formatting.

The result is a high-complexity module where changes to one runtime behavior can have surprising effects on unrelated runtime behavior. This is the clearest candidate for structural refactoring.

Recommended decomposition:

- `runtime/models/rlm_factory.py` for LM and RLM builders.
- `runtime/models/workspace/context.py` for context assembly.
- `runtime/models/workspace/planning.py` for subquery planning.
- `runtime/models/workspace/execution.py` for subquery execution loops.
- `runtime/models/workspace/verification.py` for verification and repair.
- `runtime/models/failure_markers.py` for shared failure classification.

### `runtime/tools/rlm_delegate.py` Is A Second Complexity Hot Spot

Evidence:

- `delegate_to_rlm()`
- `delegate_to_rlm_batched()`
- `_run_delegate_child()`
- `_persist_child_trace()`
- `_collect_host_workspace_snapshot()`
- `_stage_host_workspace_snapshot()`

This module owns tool registration, child runtime execution, budget leasing, context propagation, local workspace snapshotting, child persistence, and trace payload shaping. Those are related, but not the same responsibility.

This module is central to reliability. It should be easier to audit and test in smaller pieces.

Recommended decomposition:

- Keep public tool wrappers in `rlm_delegate.py`.
- Move child execution orchestration into `runtime/tools/delegate_execution.py`.
- Move snapshot staging into `runtime/tools/workspace_snapshot.py`.
- Move child trace persistence into a persistence adapter or repository-facing helper.
- Move failure extraction and response normalization into a shared runtime helper.

### Optimization API Has Two Partially Divergent Execution Paths

Evidence:

- `src/fleet_rlm/api/routers/optimization/runs.py`
- `src/fleet_rlm/api/routers/optimization/background.py`
- `src/fleet_rlm/runtime/quality/gepa_optimization.py`
- `src/fleet_rlm/runtime/quality/optimization_runner.py`

The blocking run endpoint and async run endpoint both validate paths, resolve datasets, prepare GEPA runtime, execute optimization, and persist results. The background path has its own run lifecycle, while the blocking path calls `run_gepa_optimization()` directly.

This creates risk that fixes are applied to one path but not the other. For example, path containment checks and artifact behavior are not obviously centralized.

The repository should have one internal optimization execution contract that both blocking and async paths call.

### API/UI Hard-Require MLflow Even For Registry Module Runs

Evidence:

- `src/fleet_rlm/api/routers/optimization/status.py`
- `_ensure_gepa_runtime_available()` in `src/fleet_rlm/api/routers/optimization/runs.py`
- `src/frontend/src/features/optimization/components/optimization-form.tsx`
- `src/fleet_rlm/runtime/quality/optimization_runner.py`

`optimization_runner.py` is designed to run GEPA module optimization without MLflow as a hard dependency. It writes artifacts and returns structured results. However, the API status and run endpoints currently require MLflow availability before module runs are allowed. The frontend then disables the optimization form when status is unavailable.

This is a product contract mismatch. Either MLflow is mandatory for optimization, or MLflow is optional telemetry layered on top of offline optimization. The code currently says both.

### Frontend Optimization Module List Is Stale In One Path

Evidence:

- `src/frontend/src/features/optimization/components/optimization-form.tsx`
- `src/frontend/src/features/optimization/components/datasets-tab.tsx`
- `src/fleet_rlm/runtime/quality/module_registry.py`

The optimization form queries backend module metadata, but `datasets-tab.tsx` keeps a static `MODULE_SLUGS` list with older module names and does not include `longcot-reasoner`. Current backend registry registration is centered on `optimize_longcot`.

This can make session-to-dataset export incompatible with the actual module users can optimize.

### Documentation Has Product Surface Drift

Evidence:

- `README.md`
- `AGENTS.md`
- `src/frontend/AGENTS.md`
- `src/frontend/src/routes/app/history.tsx`

The root README describes supported surfaces as Workbench, Volumes, Optimization, and Settings. Frontend instructions and routes include History as a supported surface. The root `AGENTS.md` also still says the supported surfaces include History, while `.github/copilot-instructions.md` lists only Workbench, Volumes, and Settings in one section.

This matters because retired routes are explicitly part of the product contract. Supported surface drift can lead to accidental route removal or stale documentation.

## Dead, Redundant, Or Outdated Areas

### Older Optimization Module Names Persist In Docs And Frontend

Evidence:

- `docs/how-to-guides/dspy-optimization-and-evaluation.md`
- `src/frontend/src/features/optimization/components/datasets-tab.tsx`
- `src/fleet_rlm/runtime/quality/module_registry.py`

Docs still describe registered modules such as `reflect-and-revise`, `context-selection`, `decomposition`, `repair`, and `verification`. The current registry imports `fleet_rlm.quality.optimize_longcot` and exposes `longcot-reasoner`.

Either the older modules were intentionally removed and docs/frontend need updating, or the registry is incomplete.

### Repository Instructions Contain Stale Known-Issue Notes

Evidence:

- `src/fleet_rlm/AGENTS.md`
- `src/fleet_rlm/runtime/quality/__init__.py`

`src/fleet_rlm/AGENTS.md` says the quality package imports DSPy-heavy modules at top level. The current `__init__.py` uses lazy imports. The instruction should be updated so future agents do not preserve a fixed issue that has already been addressed.

### Branch Benchmark Support Files Are Not Yet Productized

Evidence:

- `scripts/run_longcot_eval.py`
- `scripts/log_benchmark_to_mlflow.py`
- `scripts/setup_longcot_mlflow.py`
- `vendor/longcot/`
- `output/longcot-eval/`
- `mlflow_verification_report.md`

The LongCoT benchmark work includes local scripts, local artifacts, and vendored benchmark content. Some paths and IDs are hardcoded. This is acceptable for exploration, but not as a durable benchmark workflow.

If LongCoT Mini becomes a supported evaluation lane, it needs the same treatment as `scripts/evaluate_rlm_capabilities.py` and `scripts/oolong_official_eval.py`: explicit setup, pinned inputs, validation, output schema, and reproducible run instructions.

## Data Flow And Control Flow Concerns

### Optimization Dataset Resolution Can Scan Too Much

Evidence:

- `OPTIMIZATION_DATA_ROOT` and `_find_dataset_under_root()` in `src/fleet_rlm/api/routers/optimization/_deps.py`

The default optimization data root is `os.getcwd()`. `_find_dataset_under_root()` recursively walks that root when resolving dataset paths. In this repository, the working tree can include `output/`, `mlartifacts/`, `vendor/`, and other large local artifact directories.

This has performance and predictability risk. Optimization data should live under a narrower explicit root, such as `.data/optimization-datasets`, or the API should require registered dataset IDs for most workflows.

### Prefix Train/Validation Splits Are Risky For Ordered Benchmark Data

Evidence:

- `src/fleet_rlm/runtime/quality/datasets.py`
- `src/fleet_rlm/runtime/quality/optimization_runner.py`
- `scripts/generate_longcot_gepa_dataset.py`

The dataset helper splits examples by prefix. The LongCoT GEPA dataset generator writes examples grouped by domain order. For the observed 80-row dataset, the omitted `logic` domain means rows are ordered across the remaining domains. With an 80/20 prefix split, validation can be dominated by the last domain rather than being representative.

This weakens GEPA validation and holdout comparison claims.

### Runtime Factory Ignores Many Arguments

Evidence:

- `build_chat_agent()` in `src/fleet_rlm/runtime/factory.py`

`build_chat_agent()` accepts many configuration and dependency arguments, but when no interpreter is present it assigns many to `_` and does not use them. This creates a misleading interface and makes it hard to know which runtime knobs are actually honored.

Some ignored arguments may exist for compatibility, but this should be explicit in code and docs.

## Maintainability Assessment

The codebase is maintainable if future changes respect the intended layers. It will become difficult to evolve if changes keep expanding central files.

Best-maintained areas:

- FastAPI app assembly and bootstrap.
- Frontend feature organization.
- Daytona child isolation policy.
- Registry-based optimization module shape.
- Test coverage around quality modules and API contracts.

Highest-maintenance-risk areas:

- Recursive workspace execution in `runtime/models/builders.py`.
- RLM delegation in `runtime/tools/rlm_delegate.py`.
- Optimization API route duplication.
- Local SQLite store migrations and optimization persistence.
- Benchmark scripts and artifacts that are not yet reproducible workflows.

## Architectural Fit For Project Goals

The current architecture fits the goal of a web UI-first adaptive RLM workspace. The runtime is sufficiently general, the transport shell is thin enough, and Daytona-only execution is treated as a first-class substrate rather than an incidental plugin.

The architecture is less ready for benchmark-driven product claims and GEPA-as-a-product until the optimization and evaluation paths are made more uniform. The branch shows that `fleet-rlm` can run interesting benchmarks, but the evidence pipeline is still more exploratory than product-grade.

The most important strategic decision is whether optimization is:

1. An internal/offline developer workflow that can optionally log to MLflow, or
2. A fully productized UI/API feature that always requires MLflow and persisted datasets.

The current code straddles both positions.
