# Phase 17 recursive repair module note

Preserve the fleet-rlm worker boundary, Agent Framework hosted orchestration, Daytona Sandbox/Volume behavior, and the current websocket/frontend contract. Phase 17 adds one more DSPy-native recursive worker module for bounded repair planning, while keeping execution ownership inside the Daytona-backed worker loop.

## What moved into DSPy

Phase 17 promotes one more worker-side recursive sub-decision into typed DSPy code:

- `PlanRecursiveRepair` in `src/fleet_rlm/runtime/agent/signatures.py`
- `PlanRecursiveRepairModule` in `src/fleet_rlm/runtime/agent/recursive_repair.py`

This module decides:

- whether a bounded repair attempt is appropriate after verification/reflection say the current result is insufficient
- what narrow repair target should be addressed first
- which bounded repair steps or repair subqueries should guide the next Daytona-backed pass
- when the worker should stay narrow versus fall back to more recursion or human review

The live adapter stays inside the worker/runtime layer at the recursive delegate seam in `src/fleet_rlm/runtime/agent/recursive_runtime.py`.

## How this complements context assembly, decomposition, verification, and reflection

The earlier worker-native recursive modules keep their roles:

- `AssembleRecursiveWorkspaceContextModule` still selects bounded Daytona-backed handles and evidence
- `PlanRecursiveSubqueriesModule` still decides whether and how to fan out recursive subqueries
- `VerifyRecursiveAggregationModule` still determines whether a bounded aggregate is sufficient and which gaps remain
- `ReflectAndReviseWorkspaceStepModule` still decides whether the worker should recurse, repair, finalize, or request human review

The new repair module sits between “the current recursive result is insufficient” and “the runtime retries generically.” Reflection still owns the high-level recurse/repair/finalize choice. Repair planning now converts that insufficiency signal into a bounded semantic repair shape that Python/runtime can execute through the existing Daytona-backed child delegation path.

## How Daytona-backed ownership was preserved

The repair module does not execute code, launch sandboxes, manage batching, or move durable state into orchestration metadata.

It only consumes bounded worker-prepared inputs such as:

- the current delegated request
- the already assembled recursive context
- compact verification and reflection summaries
- compact Daytona handle and sandbox evidence summaries
- bounded failure signals such as missing evidence, contradictions, and runtime failure markers

Python/runtime still owns child execution, delegate budgeting, batching, retry orchestration, and result aggregation. Daytona still owns sandbox lifecycle, interpreter state, and durable memory/volume storage.

## How GEPA is used offline

Offline optimization lives in `src/fleet_rlm/runtime/quality/optimize_recursive_repair.py`.

That entrypoint:

- loads representative JSON or JSONL recursive repair traces
- converts them into typed DSPy examples for the repair signature
- applies an explicit GEPA feedback metric centered on repair usefulness, boundedness, and success potential
- saves optimized DSPy artifacts plus a manifest

Artifact output defaults to Daytona-backed quality storage under `/home/daytona/memory/artifacts/quality/recursive-repair/` when available, and otherwise falls back to local `.data/quality-artifacts/recursive-repair/` storage for offline development.

## What stays outside DSPy on purpose

These boundaries remain unchanged:

- Agent Framework still owns hosted orchestration, resumability, checkpoints, and HITL
- FastAPI and websocket layers still own transport, auth, and envelope serialization
- Daytona still owns sandbox creation, process execution, interpreter state, and durable memory
- GEPA still stays out of the live websocket request path

## Phase 18

Phase 18 should keep the same worker/orchestration/transport boundaries and improve how bounded repair execution feeds back into later recursive verification/reflection, rather than widening orchestration or websocket ownership.
