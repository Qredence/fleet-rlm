# Fleet RLM structural reduction

**Status:** local implementation and validation complete; the authorized live pilot ran one arm and stopped because provider-reported cost was unavailable. The comparative gate remains open.

**Starting candidate:** clean `38a5028c6` on `feat/large-source-broker-safety`.

**Assessment baseline:** `b0764d4b4`.

## Goal

Reduce duplicate production owners while preserving native DSPy by default,
bounded child execution, storage authority, cleanup, and durable settlement.
This plan covers the remaining structural work in the current candidate.

## Owner changes

| Responsibility | Current owner |
| --- | --- |
| Retained roots, disposable children, temporary I/O Sandboxes, provider operations | `DaytonaRuntime` is the sole registry and lifecycle coordinator. Each root record owns its root lease; each child cleanup record owns its child lease, admission permit, and close task. Pending provider operations stay with the runtime. Resource-specific lease state remains inside these records, not in separate registries. |
| Turn environment acquisition | A callable bound into `TurnPreparationPlan`; no stateful environment-provider wrapper. Session idle drain calls `DaytonaRuntime` directly. |
| Run attachment, artifact, and result storage | `workspace/host_io.py` owns `DaytonaRunStorage`; its small synchronous `volume_fs` view bridges interpreter tools. |
| Session history, tasks, memory, and Skills | Existing Session and capability owners. Workspace layout access and Session-root mount authority remain separate. |

## Measured source reduction

Counts are Python files and lines in the clean starting candidate versus this
worktree. Lines include comments and blanks.

| Scope | Before | After | Change |
| --- | ---: | ---: | ---: |
| `src/fleet_rlm/` | 131 / 52,988 | 130 / 52,919 | -1 file / -69 lines |
| `daytona/` | 7 / 9,817 | 6 / 9,471 | -1 / -346 |
| `rlm/` | 10 / 9,703 | 10 / 9,717 | 0 / +14 |
| `workspace/` | 10 / 6,177 | 10 / 6,296 | 0 / +119 |
| `sessions/` | 19 / 3,571 | 19 / 3,571 | 0 / 0 |

`daytona/turn_environment.py` (328 lines) was deleted. Its acquisition logic
and Run storage moved into existing composition and host-I/O owners (286 net
lines added there); simplifications elsewhere offset that move, leaving 69
fewer production lines overall. The runtime also dropped separate workspace-I/O,
child-close, late-cleanup, and release-task registries; pending operations now
use the owning record/lease and the runtime provider-task set. The required
sync `volume_fs` view remains as a narrow bridge to the interpreter storage
contract; there is no stateful environment-provider wrapper.

## Tasks

- [x] Record the clean candidate and before/after production counts.
- [x] Consolidate Daytona cleanup task ownership; reuse pending child closes.
- [x] Remove `turn_environment.py` and the provider object. Preserve distinct
  Workspace and Session authorities and the required sync/async storage bridge.
- [x] Add a private adapter injection seam; keep default `FleetJSONAdapter`,
  compatibility removal, and shared Turn budget enforcement.
- [x] Run the credential-free scripted adapter replay: two repetitions passed
  all protocol, attempt-accounting, and sync/async parity gates. Results measure
  protocol behavior only; they do not measure semantic quality.
- [ ] Run one live exhaustive-aggregation Turn per adapter in clean separate
  Sessions, sequentially: reserve at most $2 total/$1 per Turn, two admissions,
  one active Sandbox, and 20 minutes including cleanup. **Partial:** the
  FleetJSONAdapter arm completed; admission stopped before the stock-adapter
  arm because provider-reported cost was unavailable. See the pilot record
  below. The reservation is accounting, not a provider price cap.
- [ ] Close the structural comparison gate. Local checks pass, but the stock
  adapter arm did not run and the provider cost is unknown. Report deleted
  versus moved code and remaining lifecycle owners; keep custom adapter
  behavior until repeatable quality evidence exists. A later, separately
  authorized campaign must cover sparse retrieval, exhaustive aggregation,
  and cross-document reconciliation before claiming repeatable adapter
  quality.

## Local evidence

- Focused lifecycle, cancellation, child isolation, storage, preparation, and
  DSPy contract tests passed.
- `make check-codebase-tree`, `make check-dependency-boundaries`,
  `make check-docs`, `uv run ty check src`, and `make check` passed. The
  aggregate run included backend coverage at 84.04% and 555 passing TUI tests.
- The two-repetition scripted adapter replay passed protocol, attempt-accounting,
  sync/async parity, and valid-output gates (56 protocol samples per adapter).
  Stock `dspy.JSONAdapter` produced 16 correct scripted outputs in 64 attempts;
  `FleetJSONAdapter` produced 56 in 116 attempts. This is protocol-fixture
  evidence only; it does not establish semantic quality or Daytona behavior.
- `git diff --check` passed after the implementation and pilot evidence updates.

## Authorized live pilot — partial (2026-09-25)

- **Case and arm:** frozen exhaustive-aggregation case, `daytona-native`,
  `FleetJSONAdapter`, one fresh Session. One admission; no child or sub-LM calls.
- **Answer quality:** passed the case rubric. Answer was 230, included L1/L2/L3/L6,
  excluded draft records L4/L5, showed arithmetic, and cited every record.
- **Attempts and usage:** one observed root LM call and one iteration; provider
  retry count was not reported. 5,168 input tokens, 1,371 output tokens, 6,539
  total. RLM execution was 21.9 s; the root LM call was 16.25 s. The preparation
  trace took 31.7 s and the execution trace 36.8 s.
- **Cost and stop:** no provider-reported cost appeared in Turn usage, MLflow
  trace metadata, or model-call spans. Actual spend is unknown; no estimate is
  substituted. The first $1 reservation remains booked in campaign accounting;
  actual provider spend is unknown. The stock adapter arm was not admitted
  under the stop rule.
- **Cleanup:** the durable settlement and Turn cleanup spans were `OK`. CLI
  shutdown completed, and a read-only provider lookup for this Session's
  Sandbox returned absent. The persisted binding still said `running` at lookup
  time; record this as stale binding metadata, not as a live Sandbox.
- **MLflow:** local MLflow health returned 200 on port 5001. Execution trace:
  `tr-eb27d5811c377ab0e026ac7ba8d2d9e4`; preparation trace:
  `tr-ea2c75914c1a47e5d39e06e54ea0914e`. The standalone local MLflow server is
  left running so these traces remain viewable.

This single successful answer establishes live-path feasibility for this case
only. It is not an adapter comparison, repeatable quality result, provider-cost
receipt, or basis for removing custom adapter behavior.

## Validation and acceptance

Local acceptance requires the focused Daytona lifecycle, cancellation,
child-isolation, storage, and DSPy contract checks; `make check-codebase-tree`,
`make check-dependency-boundaries`, `make check-docs`, `uv run ty check src`,
and `make check`; plus a reviewed diff and `git diff --check`. The live
comparison gate additionally requires known provider cost for each admitted
Turn and a completed stock-adapter arm.

Accept the local reduction only with fewer production owners and less production
code, preserved settlement and cleanup behavior, and no replacement forwarding
layer. Local checks and the scripted replay do not certify provider containment,
live comparative quality, release readiness, or promotion.
