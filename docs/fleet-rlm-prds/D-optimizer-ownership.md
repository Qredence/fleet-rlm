# PRD D — Optimizer ownership

**Status:** proposed. **Priority:** high. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10), DSPy 3.4.1 / GEPA 0.1.4. [Index](README.md).

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

## 1. Problem, users and evidence

An experiment's progress should describe what the optimizer observed, regardless of client connection or stdout delay. API supervision must not replay old observations into the durable ledger.

**current-source (input PRD):** `GEPAProgressReporter._persist` writes search counters through `_update_run`, then emits a structured event. API `_read_output` parses the emitted event and writes progress again. Both use ownership/status fences, but those fences do not prevent an older observation from overwriting a newer one while the same owner remains running. [Worker reporter](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/optimize/gepa.py#L51-L123), [API ingestion](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/experiments.py#L386-L447).

**previously-reproduced (not rerun):** a delayed event containing 10 overwrote a worker observation of 20; a stage-only event erased numeric counters. These were reported probe results, not rerun in this documentation task. Terminal-state fencing already exists and must survive the change.

**current-source (framework inspection reported by input PRD):** GEPA's callback dispatcher catches ordinary callback exceptions and logs a warning. Raising from a progress callback is therefore not a reliable stop mechanism. [Pinned callback dispatcher](https://github.com/gepa-ai/gepa/blob/8b0ce6cd99a234f6b74daf37558a2ac0ce18f975/src/gepa/core/callbacks.py#L527-L551). Fleet already wires native stop callbacks for a file and database state in `_compile`; preserve both purposes rather than replacing them with an exception-only policy.

## 2. Native-framework-first decision and rationale

Keep native `dspy.GEPA.compile`, documented GEPA callbacks and stop callbacks. **The optimizer worker is the sole durable writer of running stage/progress observations**, for both managed and standalone runs. This is the ownership recommended in the prior main review. The API owns admission, process startup, bounded pipe draining, cancellation intent, exit confirmation and managed-run terminal arbitration. Worker-only progress avoids making stdout delivery part of database correctness and serves standalone CLI identically.

Choosing the API as a managed-only progress writer would create two mode-dependent persistence policies and make pipe delivery authoritative. That alternative is rejected for this slice. Native GEPA supplies search and callback hooks; it does not supply Fleet's PostgreSQL ledger/process leases or installation approval. Fleet glue remains small and in existing modules.

## 3. Scope and non-goals

Scope: remove duplicate progress ingestion writes, make worker observations durable once, preserve counters across stage changes, integrate mandatory persistence failure with native stop checks and retain existing process/cancellation ownership.

Non-goals: replace GEPA, invent a new event bus, move optimizer imports into API, unify all worker/API writes under a generic repository layer, promise immediate interruption of in-flight provider calls, change promotion gates or install candidates automatically. Progress counters are observations; separately identify which existing Store writes are required for ownership, durable run correctness or finalization. Loss of those required writes makes a run ineligible for successful publication. Optional MLflow, stdout and best-effort telemetry failures alone do not acquire that authority.

## 4. Affected code tree

```text
src/fleet_rlm/
  optimize/gepa.py            # reporter, stage writes, stop wiring, safe-boundary checks
  experiments.py             # process supervision + bounded drain; delete progress writer
  optimization_runs.py       # retain neutral contracts and stop signaling
  store.py                   # preserve owner/status CAS; partial progress update if needed
tests/optimize/test_optimization.py
tests/api/test_experiments.py
tests/store/test_optimization_runs.py
tests/lite/test_optimization_contracts.py
```

## 5. Concrete requirements and tasks

1. **D1 — Map authority by operation.** Freeze representative run records and enumerate every write as required Store/ownership persistence, optional observation, API process/cancel intent, or terminal arbitration. Do not silently upgrade every diagnostic to a release-blocking requirement. Worker owns search/validation/finalization stages and counters; API owns process admission and confirms managed exit before exposing its terminal outcome; existing worker-outcome handoff and owner/status CAS stay intact. Cancellation intent may be written by the API, while the worker observes it. Document this as one writer per responsibility, not a claim that one process writes every run field.
2. **D2 — Remove the second progress write.** Delete API parsing/persistence of `FLEET_EVENT progress` and stage snapshots. Reuse the existing bounded `_discard_output` drain for stdout/stderr; adding diagnostic-tail storage is outside this slice. Preserve any terminal worker outcome handling that process supervision requires; audit `_read_output` and its callers before replacing its progress-reading path.
3. **D3 — Make progress snapshots coherent.** Worker reporter updates known counters; stage-only events must not replace counters with `{}` or null. At optimization end preserve existing values not present in the final callback and update the observed total. Counter monotonicity applies to cumulative metric calls, not necessarily every delta/remaining field. Preserve unknown remaining values as unknown.
4. **D4 — Bridge required persistence failure to native stopping.** Put the first required-write failure/lost-ownership indicator on the existing reporter, protected only as needed by callback concurrency. Do not create a failure-state service. Add its predicate to existing native `stop_callbacks`. Recheck immediately after compile and before validation, artifact publication and success finalization. A stop callback returning true alone must not allow a partial result to become successful. Optional MLflow/stdout/telemetry exceptions remain bounded diagnostics under existing policy and must not set this latch. Failures in required writes after compile, outside callbacks, must still block success; do not rely on callbacks as the only durability boundary.
5. **D5 — Retain cancellation fallbacks.** Keep database stop intent checks because a stop-file write may fail after the database update. Keep file signaling for the normal cooperative path. Preserve owner/status fencing, process exit checks, interrupted-owner reconciliation and lease release order. Distinguish cancellation from persistence failure in terminal diagnostics.
6. **D6 — Update tests/docs.** Existing API tests that demand event-driven durable progress writes must become assertions that delayed events cannot mutate the ledger. Retain standalone callback persistence coverage and add managed behavior to the same contract.

Illustrative local failure latch, not a new framework:

```python
# Add to the existing reporter; protect access if callbacks can be concurrent.
def on_budget_updated(self, event):
    try:
        self._persist_complete_snapshot(event)
    except Exception as exc:
        self._record_first_failure(exc)
        return
    if self.emit_events:
        try:
            emit_event(self._safe_progress_event(event))  # optional diagnostic only
        except Exception as exc:
            self._record_optional_diagnostic(exc)  # existing bounded/sanitized path
            # Do not set the required-persistence failure latch.

def should_stop(self, _state):
    return self.failure is not None

# Existing native GEPA configuration:
stop_callbacks = [file_stopper, database_stopper, reporter.should_stop]
optimized = dspy.GEPA(..., gepa_kwargs={"callbacks": [reporter],
                                     "stop_callbacks": stop_callbacks}).compile(...)
reporter.raise_if_failed()  # immediately after compile
# Preserve equivalent required-write/ownership checks before validation,
# publication and success finalization, including non-callback failure paths.
```

Adapt the stop-callback signature to the locked GEPA protocol. Preserve the original cause internally but serialize a sanitized failure. Do not make stderr/pipe backpressure capable of blocking mandatory persistence or cancellation indefinitely.

## 6. Before / after flow

Before: GEPA callback → worker DB write + stdout event → API reads event → second DB write. Callback write failure may be swallowed by native dispatch. API then supervises exit.

After: native callback → worker durable coherent snapshot → optional diagnostic event; API drains without writing progress. A required worker write failure latches a stop request → native safe-point stop → boundary failure prevents candidate publication. API still waits for process/cleanup before exposing the managed terminal outcome.

## 7. Acceptance tests and measurable outcomes

| ID | Required scenario | Passing condition |
|---|---|---|
| D-T1 | Managed worker emits a progress callback | One durable progress update; API drain performs zero progress writes |
| D-T2 | Persist 20 then deliver old stdout 10; deliver stage-only event | Durable cumulative count remains 20 and retains known counters |
| D-T3 | Standalone CLI callback and optimization-end callback | Same worker ownership; final total retained without erasing other fields |
| D-T4 | Required write raises inside a callback caught by native GEPA | Native stop hook observes latch; after compile no validation/candidate publication; failed run with sanitized reason |
| D-T5 | Database cancel commits but stop-file creation fails | Database stop callback still prevents further stages; cancellation cannot become completed |
| D-T6 | Exit failure, owner restart, stale callback and terminal races | Existing CAS and managed outcome arbitration remain correct; no terminal downgrade |
| D-T7 | Full/noisy stdout and disconnected client | Pipes drain within configured bounds; experiment continues unless explicitly cancelled |
| D-T8 | Optional MLflow/diagnostic emission fails | Bounded sanitized diagnostic; no required-persistence latch or false search failure |
| D-T9 | Required Store write/ownership loss after compile and before finalization | No candidate is finalized as eligible success; managed and standalone terminal paths agree |

Run `uv run pytest tests/optimize/test_optimization.py tests/api/test_experiments.py tests/store/test_optimization_runs.py tests/lite/test_optimization_contracts.py`, then required gates. Use a fake native dispatcher that catches exceptions, plus one integration test against locked GEPA with mocked external boundaries. Metrics: managed callback durable writes 2→1; stale stdout overwrites 0; required persistence failures reaching successful publication 0. Search quality is unchanged by design and must not be claimed improved without measurement.

## 8. Migration and rollback

Prefer no SQL migration: change writer ownership and partial JSON merge semantics within existing fields. If Store changes are necessary, preserve historical records and CAS filters. Mixed-version rollout is a risk: drain old API/worker pairs before adopting worker-only progress so an old API cannot continue replaying stdout. CLI and API must use the same released code.

Rollback API/worker changes together. Do not roll back by allowing stale events to overwrite counters: if reverting the old ingestion path is unavoidable, guard it from concurrent worker writes and record the temporary policy. Retain ownership fences and native stop callbacks. A failed persistence run cannot later be approved merely because a candidate file happens to exist.

## 9. Risks, dependencies and gates

Native callbacks may run at safe points, so stop latency is bounded by native execution and in-flight provider work rather than instantaneous. Local failure state must be visible across callback threads. D must not accidentally remove the API's managed terminal finalizer while deleting progress parsing. E is independent; G should build on this single worker observation path. C exposes the resulting stable progress fields without interpreting logs.

## 10. Codex-ready checklist

- [ ] Inventory stage/progress, cancellation, worker-outcome and terminal writers separately.
- [ ] Remove only duplicate API progress writes; retain bounded pipe drains and process ownership.
- [ ] Preserve counters on partial updates; add only a local reporter failure field and native stop integration for required writes, not optional telemetry.
- [ ] Test locked native exception swallowing and safe-boundary failure behavior.
- [ ] Pass D-T1–D-T9 and repository gates; document mixed-version migration/rollback.
- [ ] Report observed write count reduction; keep GEPA/search/approval algorithms native and unchanged.

Use the [common acceptance report](README.md#acceptance-report). Report the removal of API progress ingestion separately from retained API process/cancellation/terminal writes; a single progress writer does not mean a single writer for the whole run record.
