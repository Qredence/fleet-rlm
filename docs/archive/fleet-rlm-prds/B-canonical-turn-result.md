# PRD B — Canonical turn result

**Status:** implemented and validated offline, 29 October 2026; see the [acceptance report](B-acceptance-report.md). **Priority:** high. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10). See [index and reconciliation](README.md).

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

## 1. Problem, users and evidence

A client must receive the same outcome when a Turn first completes and when it reconnects using its idempotency key. Operators must be able to distinguish execution failure, uncertain commit and failed delivery.

**current-source (input PRD):** `SessionRuntime.commit_turn` builds a successful result and persists it together with the record, memory and optional replay, but returns only artifact paths. `run_turn` later builds another completion. Between commit and delivery it mutates spans and MLflow traces; either inner or outer exception handling can invoke `_terminal_failure`. [Session result construction](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/session.py#L146-L231), [coordinator](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/engine/turn.py#L279-L373).

Store already protects completed outcomes against downgrade and commits completion atomically with history. [Store guard and transaction](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/store.py#L513-L606). The defect is contradictory coordination after that transaction, not a missing guard against every duplicate write.

**previously-reproduced (not rerun):** a fault-injection probe committed success, raised during tracing, then observed a completed database row and a failed stream/cancellation path. That reproduction was reported in the prior conversation and was not rerun here. Existing `tests/api/test_turns.py` already compares live and replay events; extend it with post-commit faults and comprehensive payload equality.

## 2. Native-framework-first decision and rationale

Keep DSPy `Prediction` as the engine's native computation result. Fleet's durable completion is application data: DSPy does not supply Fleet's transaction, idempotency, lease, history or reconnect policy. FastAPI/Pydantic can validate the transport representation in C; they do not own the Store commit.

Use the existing neutral `contracts.py` for a small completion type if required by both session and coordinator. Build the canonical payload once in SessionRuntime, persist it, and return that value after commit. Do not add a ResultService, DTO inheritance tree or separate persistence adapter. The successful commit is the outcome boundary; subsequent observation/delivery failures cannot reverse it.

## 3. Scope and non-goals

Scope: successful completion assembly, commit return contract, coordinator failure boundaries, idempotent recovery and exact equality between durable/live/replay result content. Preserve failure/cancel/deadline semantics before commit and the existing Store transaction.

Non-goals: a general transaction-recovery subsystem, new recovery services or status hierarchies, distributed exactly-once network delivery, changing historical payloads in place, rebuilding live Python state from history, replacing `dspy.Prediction`, altering budget policy or moving the harness again. F2 owns publication and F3 owns the logical artifact snapshot. B must expose one optional handoff point without depending on their future implementation or claiming filesystem atomicity. B can first pass the existing artifact view through one canonical result; F3 replaces its collection step later.

## 4. Affected code tree

```text
src/fleet_rlm/
  contracts.py               # optional neutral completion typing, no HTTP imports
  session.py                 # commit_turn returns committed payload
  engine/turn.py             # success boundary, observation/delivery handling
  store.py                   # preserve atomic commit/CAS; recovery query if needed
  api/routes/turns.py         # delivery/replay compatibility
tests/api/test_turns.py
tests/lite/test_session.py
tests/store/{test_store,test_history}.py
```

## 5. Concrete requirements and tasks

1. **B1 — Inventory the payload.** Freeze successful/failed live frames, current/replayed results and representative old rows as fixtures before edits. Create exactly one field inventory, including which existing boundary normalizes each legacy variant. Record every existing live and stored field and its absence/null policy: answer, route, note, artifacts, counters, budget breakdown, provenance, deadline source/effective seconds, outcome/duration, trace identity, cleanup and provider lifecycle bounds. Resolve `turn_id` explicitly: current live payload supplies it while the stored result relies partly on its enclosing record. New canonical results must carry it once; historical recovery may add it from the row at the boundary. Do not silently drop historical unknown fields.
2. **B2 — Return one committed result.** Replace the artifact-list return with the canonical completion; use its `artifacts` for diagnostic counts. Build before invoking Store; return only after confirmed commit. Preserve record-size/path checks, replay digest checks, memory bounds and the single transaction. Freeze or copy at ownership boundaries so downstream telemetry cannot mutate durable content.
3. **B3 — Fence the failure boundary.** Retain a local committed-result sentinel assigned immediately when `commit_turn` returns, before `phase.set_outputs` or phase-context exit. Account for exceptions from context-manager exit, final span writes and `emit`, not only explicit MLflow calls. Only pre-commit execution failures may enter ordinary `_terminal_failure`.
4. **B4 — Reuse existing ambiguous-commit recovery.** A lost database acknowledgment is not proof of failure. First trace and exercise the existing idempotency lookup, terminal-row readback and lease checks using the claimed Turn identity. If those facilities return a completed row, use its stored result; if they cannot establish the outcome, preserve existing uncertain/in-progress policy and block reexecution. Use a bounded readback at the existing coordinator boundary, not a new retry engine or reconciler. Do not add a new persisted status merely to distinguish an internal control-flow branch. If a failure-injection test proves existing facilities insufficient, record the gap and smallest separately reviewed change; never manufacture success or reexecute generated code to resolve uncertainty.
5. **B5 — Align replay and clients.** Remove duplicate completion assembly. Emit the canonical payload through existing envelope/SSE logic; retain one terminal outcome and final `done`. After a real disconnect, durable success may exist without delivery; reconnect returns success without another DSPy invocation. Post-commit note/telemetry errors are diagnostics, not cancellation requests.

Proposed coordinator shape, intentionally omitting existing scope setup:

```python
committed = None
try:
    prediction = execute_existing_native_engine()
    record = prepare_existing_turn_record(prediction)
    with phase_span("turn.commit", turn_id=turn_id) as phase:
        committed = runtime.commit_turn(
            session_id, record, new_memory_note=learned_note,
            replay_snapshot=replay_snapshot,
        )
        phase.set_outputs({"artifacts": len(committed["artifacts"])})
    observe_completion(committed)  # existing telemetry, best effort after commit
except Exception as exc:
    if committed is None:
        # Include existing idempotency recovery for uncertain Store outcomes.
        terminal = resolve_precommit_or_uncertain_failure(exc)
    else:
        record_observation_failure(exc)  # bounded/sanitized, no failed Turn write
        terminal = committed
else:
    terminal = committed

# Delivery errors are handled as delivery errors outside execution-failure logic.
emit_terminal_with_existing_transport(terminal)
```

Helper names above describe responsibilities; reuse existing functions or local blocks rather than creating a service for each line. The actual surrounding context-manager exceptions must also respect `committed`.

## 6. Before / after flow

Before: native Prediction → record → session constructs/stores result → returns artifacts → telemetry → coordinator constructs result → SSE. Any later exception can enter failure reporting.

After: native Prediction → record and one artifact view (F3 later strengthens its capture) → session constructs canonical result → one transaction → returns canonical result → best-effort observation → native transport. Reconnect reads the same result. Before commit, existing teardown/failure policy applies; after commit, delivery or observation has its own diagnostics.

## 7. Acceptance tests and measurable outcomes

| ID | Required scenario | Passing condition |
|---|---|---|
| B-T1 | Successful live Turn then same idempotency request | Canonical content equals persisted result and replay content; one engine execution and one commit |
| B-T2 | Fault after Store return, in phase output/context exit, span updates, trace update | Durable outcome remains completed; no cancellation or failed terminal; success delivered when channel remains usable |
| B-T3 | Terminal `emit` raises / client disconnects after commit | No downgrade or reexecution; reconnect recovers success; delivery diagnostic is sanitized |
| B-T4 | Failure before commit and rejected lease | Existing stop/cleanup acknowledgment semantics remain; no false completion |
| B-T5 | Store commits then loses acknowledgment; separately make readback unavailable | Existing identity/terminal lookup recovers the committed result when provable; otherwise existing uncertainty policy blocks reexecution without new recovery infrastructure |
| B-T6 | Rich payload plus old historical payload | Every counter, provenance, timeout-null, root ID and provider bound survives; documented legacy normalization only |

Run `uv run pytest tests/api/test_turns.py tests/lite/test_session.py tests/store/test_store.py tests/store/test_history.py` with existing offline fakes, then required gates. Metrics: one successful payload assembly path; zero success→failure contradictions in fault matrix; one durable completion per idempotency key. Object identity need not survive JSON encoding: test semantic equality excluding transport-only timestamp/sequence, rather than Python `is`.

## 8. Migration and rollback

Prefer no SQL migration: result JSON already exists. Additive inclusion of `turn_id` must be documented and tested for old rows. If a version/discriminator is introduced for C, supply read-time legacy compatibility and avoid rewriting all history. Deploy reader compatibility before stricter validation of existing rows.

Rollback coordinator/session return-shape changes together with every caller. Preserve newly stored additive fields and completed rows. Never roll back by rewriting a committed success as a failed Turn. If C depends on the new shape, roll back C's strict writer/reader together or retain its legacy reader. Approval fingerprint changes follow existing evidence rules.

## 9. Risks, dependencies and gates

The largest risk is an exception after the transaction but before the coordinator records its sentinel; ambiguous database outcomes require existing Store recovery rather than broad exception suppression. Phase/context-manager exit is a frequent overlooked boundary. F must settle whether inventory/digests form one snapshot; until then B makes result ownership consistent without claiming remote files cannot mutate. C depends on this field/legacy inventory. Telemetry failure diagnostics must not contain raw prompts, credentials or entire artifacts.

## 10. Codex-ready checklist

- [x] Scan/read baseline, rules, Store transaction and all commit_turn consumers.
- [x] Freeze wire/history fixtures, create one B1 field/legacy inventory, and write B-T1/B-T2 regressions first.
- [x] Return the committed payload and remove the second construction.
- [x] Handle inner/outer scopes, phase exit and delivery faults; prove existing readback behavior for uncertain commit before proposing any new recovery mechanism.
- [x] Pass B-T1–B-T6 and repository gates; report actual outcomes.
- [x] Coordinate C/F field handoffs and document rollback; no runtime-state recovery claims from history.

Use the [common acceptance report](README.md#acceptance-report). Expected structural result: one payload assembly and one existing transaction path, with no new recovery service, duplicated canonical model hierarchy, or unnecessary durable field.
