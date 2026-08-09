# P7 performance budget decision

Candidate: `9b1067ca` on `dev-0.7`, measured 2026-08-09/10 with
`fleet-rlm-python313-v5`, DSPy 3.3.0, and Daytona 0.202.0. The receipts below
are local ignored evidence; this page records the decision and bounded numbers.

**Decision: KEEP CURRENT DESIGN.** Do not add a global Sandbox pool, shared
recursive interpreter, recursive multi-child Sandbox, or speculative workspace
lease layer.

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
