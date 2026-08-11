# P7 performance budget decision

Candidate: `9b1067ca` on `dev-0.7`, measured 2026-08-09/10 with
`fleet-rlm-python313-v5`, DSPy 3.3.0, and Daytona 0.202.0. The receipts below
are local ignored evidence; this page records the decision and bounded numbers.

**Decision: KEEP ISOLATED CHILD DESIGN.** Bounded sibling fan-out is now
supported without adding a global Sandbox pool, shared recursive interpreter,
or speculative workspace lease layer. Each logical child still owns its own
Sandbox, interpreter, LM runtimes, admission permit, and cleanup.

## Current bounded fan-out policy

`recursion_max_parallel_children = 2` is the shipped concurrency cap. The Root
selects `rlm_query_batched`; Fleet atomically reserves the shared recursive call
budget, preserves input ordering, and settles the batch all-or-nothing. The
existing single-child lifecycle measurements remain the per-child cost basis;
the routing benchmark records observed peak sibling concurrency and latency for
batch workloads.

## Measurements

| Area | Metric | p50 | p95 |
|---|---|---:|---:|
| Root fresh Sandbox | create-to-running seconds | 1.731 | 6.096 |
| Root full create→first execution | seconds | 14.997 | 49.162 |
| Interpreter broker startup | seconds | 2.036 | 3.746 |
| Broker first interpreter call | seconds | 0.643 | 1.284 |
| Root shutdown + deletion | seconds | 0.125 | 0.198 |
| Recursive admission | seconds | 0.00005 | 0.00024 |
| Recursive child create | seconds | 1.671 | 6.286 |
| Recursive interpreter construction | seconds | 0.00012 | 0.0015 |
| Recursive simple interpreter execution | seconds | 2.140 | 4.043 |
| Recursive cleanup total | seconds | 0.806 | 1.662 |
| Recursive provider deletion | seconds | 0.230 | 0.368 |
| Workspace I/O sandbox create | seconds | 4.817 | 5.893 |
| Workspace I/O write operation | seconds | 1.479 | 1.689 |
| Workspace I/O shutdown/deletion | seconds | 0.122 | 0.138 |
| Run claim | milliseconds | 873.095 | 1389.790 |
| Run preparation | seconds | 12.434 | 17.454 |
| Root RLM execution | seconds | 51.025 | 72.049 |
| Turn settlement | seconds | 1.449 | 2.110 |
| Turn cleanup | seconds | 0.258 | 1.131 |
| **Total Run** | seconds | **66.661** | **97.740** |

Root cold/warm signal: the first measured Sandbox create was 5.540s, matching
the cold create distribution tail; measured warm-capacity Sandbox creation had
p50 1.731s / p95 6.096s. The measured cold/warm delta is within provider queue
variance, not a structural defect.

## Decision gate

The dedicated recursive child lifecycle (create + cleanup) costs:

- p95: `6.286 + 1.662 = 7.948s`
- versus total Run p95: `97.740s`
- ratio: **8.1%**

The workspace I/O lifecycle (create + write + sandbox deletion) costs:

- p95: `5.893 + 1.689 + 0.138 = 7.720s`
- versus total Run p95: `97.740s`
- ratio: **7.9%**

The broker startup boundary costs 3.746s p95 (3.8% of total Run p95), and the
first actual brokered interpreter call costs 1.284s p95 (1.3%). Both remain
small next to model/RLM execution (72.049s p95).

The live latency workload did not issue a recursive LLM call in this run
(`recursive_calls = 0`), so it cannot provide child-model p95. The decision
therefore uses the stricter product boundary: actual recursive child
infrastructure lifecycle as a percentage of the whole Run p95. It is below the
10–15% decision threshold, and preserving one dedicated interpreter/Sandbox per
recursive child retains the proven isolation and cleanup contract.

## Evidence retention

Local ignored receipts:

- `.scratch/p7/daytona-lifecycle-9b1067ca.json`
- `.scratch/p7/rlm-latency-9b1067ca.json`
- `.scratch/p7/run-phase-breakdown-9b1067ca.json`
- `.scratch/p7/phase7-targeted-lifecycle-9b1067ca.json`

Re-run with committed commands when credentials and `FLEET_LIVE=1` are
explicitly available:

```bash
uv run python scripts/benchmark_daytona_lifecycle.py --output <receipt.json>
uv run python scripts/benchmarks/run_rlm_latency.py benchmark   --api-url http://127.0.0.1:8000   --mlflow-url http://127.0.0.1:5001   --experiment-id 1 --variant p7-refactor   --runs 20 --warmups 3 --output <receipt.json>
```

The phase breakdown command is a local ignored helper because it joins MLflow
trace timings to `fleet_runs` timestamps without retaining private payloads.
The benchmarks keep typed-submit and trace identity evidence; they do not print
credentials, provider error bodies, or model payloads.

## Follow-up trigger

Reconsider bootstrap/snapshot optimization before any isolation change only if
a future refactored-capacity benchmark puts recursive create + cleanup at or
above 15% of total Run p95, or if workspace I/O lifecycle violates an explicit
product SLO. Do not change recursive isolation or share child interpreters as
the default optimization.

## Phase 8 architecture freeze certification

Certified at commit `5e2e257b`, after the P1–P7 structural changes and the
startup orphan-cleanup race fix:

- Phase 1 stream: `.scratch/phase8/daytona-phase1-stream-5e2e257b.json`
  passed with native root DSPy RLM, Attachment access, one single semantic
  call plus one batched call, broker cleanup, `typed SUBMIT`, and terminal
  ordering in 102 seconds.
- Phase 2 recursion: `.scratch/fleet-rlm-recursive-runtime/evidence/daytona-dspy-recursive-5e2e257b.json`
  passed with one dedicated native DSPy child interpreter, no grandchild,
  sibling-scope isolation, and strict cleanup. Turn: 59.6s; child: 13.9s.
- Complete Daytona MVP: `.scratch/phase8/daytona-mvp-5e2e257b.json` passed
  with Attachment preparation, Artifact publication, stateful RLM iterations,
  fresh interpreter replacement, Workspace reload, secret audit, and cleanup
  in 197 seconds.
- `tests/live/backend/test_attachment_artifact_durability.py` and
  `tests/live/backend/test_url_cache_durability.py` passed after loading the
  repository environment.
- `uv run fleet doctor daytona` passed repeatedly, proving policy, database,
  Volume, snapshot, native RLM construction, scoped interpreter, and cleanup.
- `make check` passed after the last live and composition fixes: API/stream
  sync, Alembic, codebase tree, docs, harness, backend tests, and 192 TUI
  tests (84.60% coverage).

The only intentional public/config schema delta remains removal of the fake
`rlm.recursion_max_depth` setting. The native child boundary is `RLM_NATIVE_CHILD_DEPTH = 1`. No temporary Turn/Run compatibility alias,
global Sandbox pool, distributed lock, event bus, repository factory, policy
engine, RLM abstraction framework, or MLflow abstraction framework remains.

One narrow production fix was required before freeze: the startup orphan sweep
must not provision an ephemeral Daytona sandbox when the Workspace has no
committed artifacts, completed Runs, or active Runs. That sweep is skipped in
that empty case because it cannot improve correctness and can race the first
live Volume acquisition.
