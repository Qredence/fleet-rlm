---
name: analyzing-rlm-performance
description: Analyzes and tunes Fleet's DSPy RLM loop, provider budgets, and Daytona broker latency. Use when reviewing RLM efficiency, reducing prompt or retry cost, instrumenting interpreter cells, or deciding whether Daytona snapshots/prewarming are justified.
compatibility: Requires the Fleet-RLM repository, its pinned DSPy and Daytona dependencies, and local access to the repository's test and benchmark commands. Credentialed Daytona/provider benchmarks require explicit operator authorization.
---

# Analyzing RLM performance

Analyze the complete Fleet execution path before changing a performance knob:

`FastAPI request → Run/Turn lifecycle → DSPy RLM → interpreter cell → Daytona broker → host tool/provider → result`

The goal is to preserve the benefits of recursive language models—delegation,
parallel decomposition, and programmatic context access—without paying for
unproductive root iterations, repeated prompt material, provider retry storms,
or avoidable sandbox/broker overhead.

## Non-negotiable boundaries

- Keep `dspy.RLM` and `dspy.LM` as the native RLM and model abstractions.
- DSPy owns native `REPLHistory` and trajectory semantics. Never compact,
  truncate, reset, or reconstruct that history in Fleet.
- Treat process-scoped LM objects as immutable templates. Turn-specific
  deadlines, retries, callbacks, and adapters must be isolated per Turn.
- Keep Daytona SDK integration under `src/fleet_rlm/daytona/` and keep FastAPI
  routes as transport adapters.
- Preserve Run/Turn ownership, cancellation, settlement, cleanup, recursive
  depth, and public event contracts.
- Do not enable live credentials or claim new provider/Daytona p95 numbers from
  unit tests. Use the repository's explicit live benchmark entry points only
  after the operator authorizes them.

## Investigation workflow

1. Read `AGENTS.md` and `ARCHITECTURE.md`, then inspect the relevant tests and
   call sites before editing.
2. Identify the effective policy in `config/fleet.toml`. Distinguish shipped
   configuration from generic constructor defaults and test fixtures.
3. Build a baseline from existing benchmark receipts or a deterministic local
   harness. Split latency into root model time, interpreter execution, broker
   startup, callback polling, provider calls, recursive child lifecycle, and
   cleanup. Do not optimize a component merely because it is visible in a
   trace; measure its share of the end-to-end budget.
4. Trace prompt and history ownership through `src/fleet_rlm/rlm/`. Look for
   repeated instructions, large pasted values, duplicate verification turns,
   unnecessary package probing, and root-level work that should be delegated.
5. Trace broker work through `src/fleet_rlm/daytona/broker.py` and
   `src/fleet_rlm/daytona/interpreter.py`. Separate requested wait time from
   observed network latency and separate host callback dispatch, tool
   execution, result posting, and `run_code` time.
6. Make one bounded change at a time, add a regression or timing test at the
   real seam, and compare the same baseline dimensions before and after.

## Tuning the DSPy RLM loop

Prefer policy/configuration changes over hardcoded behavior:

- Reduce effective root `max_iters` only after checking that the prompt tells
  the model to verify and submit rather than spending an iteration on a
  redundant restatement. Same-action verification is preferred when practical;
  a genuinely independent later check remains allowed when needed.
- Bound `max_llm_calls` so recursive delegation cannot consume the entire Run
  budget. Preserve atomic admission and the configured recursive child depth.
- Keep `max_output_chars` and execution-output limits bounded, but do not add a
  second Fleet history compaction policy. Output projection/truncation is a
  public-observability concern, not native DSPy history management.
- Set provider `num_retries` deliberately per configured root/sub model. A
  retry is useful for transient failures but multiplies latency and cost; use
  the smallest value supported by the product's reliability requirements and
  ensure cancellation/deadline ownership still bounds it.
- Inspect retry classification before changing it. Do not retry validation,
  authentication, policy, or deterministic request errors as if they were
  transient provider failures.
- Measure root iterations, provider call count, prompt/input characters, output
  characters, retry count, recursive calls, and terminal outcome. A lower
  iteration count is not an improvement if answer quality or verified
  correctness regresses.

## Measuring and reducing Daytona broker overhead

Use per-execution metrics that make each cell's cost attributable:

- callback `poll_count`, empty polls, poll latency, and pending-batch size;
- configured versus observed pending/output wait durations;
- callback dispatch, host tool execution, and result-post durations;
- output poll count/latency, output characters, and release count; and
- total execution wall time versus `run_code` time.

For the HTTP-in-sandbox broker:

- Reuse a broker-owned callback executor and pooled HTTP client across cells;
- use bounded server-side long polling for pending requests and streamed
  output, with a cap that prevents a slow proxy from creating unbounded waits;
- use bounded exponential backoff only when polling is empty or fails, and
  return to immediate polling after useful work;
- prevent completed requests from being dispatched or posted twice;
- build response bodies while holding the condition lock, but perform network
  sends after releasing it; and
- perform a bounded final output-release read rather than an arbitrary drain
  loop after execution completes.

Do not hide broker latency by removing instrumentation or by making polling
intervals globally aggressive. Validate streaming output, callback fulfillment,
poll failure recovery, executor reuse, and cleanup in co-located tests.

## Daytona snapshots and prewarming

Use a snapshot when multiple sandboxes share stable dependencies or startup
files. Verify the snapshot includes the actual pinned runtime/packages and that
rebuilding it is cheaper than repeatedly installing them. Keep per-request
workspace, credentials, and host-tool bindings out of the snapshot.

Use prewarming only when measurements show sandbox creation is a material part
of the target SLO and the lifecycle contract can safely lease, reset, and
scrub the resource. Do not introduce a global warm pool merely because a
snapshot exists: Fleet's volume/mount/context ownership, recursive isolation,
cancellation, and cleanup semantics must remain intact. Prefer the existing
session-scoped root prewarm until a credentialed benchmark proves a stronger
design is safe and beneficial.

Reference current provider behavior before changing SDK usage:

- [DSPy RLM API](https://dspy.ai/api/modules/RLM/)
- [DSPy RLM guide](https://dspy.ai/diving-deeper/rlm)
- [Daytona DSPy RLM guide](https://www.daytona.io/docs/en/guides/rlm/dspy-rlms/)
- [Daytona snapshots](https://www.daytona.io/docs/en/snapshots/)
- [FastAPI lifespan/events](https://fastapi.tiangolo.com/advanced/events/)

## Validation

For focused Python changes, run the relevant tests plus:

```bash
uv run ruff check <changed-paths>
uv run ruff format --check <changed-paths>
uv run ty check src
git diff --check
```

For broker or RLM policy changes, include the focused broker/config/program
tests. For public contracts, run `make api-sync` and `make api-check`. For
cross-cutting lifecycle or dependency changes, run `make check` and report any
live lanes that were intentionally not run.

The final report must state the baseline, changed dimensions, quality/semantic
guardrails, validation commands, and whether any live Daytona/provider result
was actually measured.
