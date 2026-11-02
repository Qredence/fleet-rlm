# F1 acceptance: durable sandbox ownership

## Status

**Offline fault matrix passes. Live cleanup evidence is partial; F1 is not fully accepted.**

## Implemented evidence

- Store schema migration 8 adds nullable `fleet_lite_sessions.sandbox_ownership`. New Session rows start with explicit empty ownership; rows predating migration 8 remain `NULL` and are refused for writable admission.
- Under the Session advisory lease, runtime reconstruction advances the ownership epoch. Resource writes are row-locked and compare the current epoch before changing bounded resource entries.
- Daytona create intent is persisted before allocation, the provider ID is persisted after create acknowledgment, and stop/delete intent is persisted before cleanup. Children inherit the Session ownership callback.
- Reconstruction looks up known provider IDs first and uses allocation labels for creates without an acknowledged ID. A resource record is removed only after the pinned SDK cleanup call returns successfully. Empty or ambiguous label lookup remains fenced.
- Turn claim, completion and failure writes carry the runtime's ownership epoch. After a takeover reconciles prior resources, remaining `running` Turns are marked failed with an explicit no-replay result; stale-owner Turn writes are rejected.
- Legacy refusal, epoch writes, reconciliation by provider ID, and unresolved allocation lookup have offline regression coverage.
- Controlled subprocesses exit after remote create but before its acknowledgment, and after remote stop but before confirmation. A new owner reconciles each persisted intent.
- Additional regressions show that lease loss prevents create, a stale provider response cannot replace a newer epoch, and label reconciliation remains fenced if the new owner loses its epoch during cleanup.
- The subprocess harness uses a test-only atomic file-backed ownership callback and a deterministic provider double. Store row-lock and epoch-CAS behavior is covered separately by Store unit tests.

## Validation

- Focused Store and Session suite after the admission-order regression: 69 passed; the preceding Store/Session/API Turn run passed 192.
- Final `make check`: 1,844 passed, 1 skipped; Ruff, formatting, type checking, documentation links, OpenAPI contract, and package build all passed.
- Live cancellation T7 returned durable `cleanup=stopped`. Daytona provider lookup confirmed its root and the recovery root were `STOPPED` after canary teardown.
- During the API restart case, the old root was confirmed `STOPPED` before a fresh root was admitted. The same Session rebuilt with ownership epoch 2 and an empty resource list. The interrupted T9 Turn was subsequently terminalized as `failed` by the new epoch-fenced Store recovery method; readback confirmed the no-replay error result.
- The same Store recovery method was exercised against the configured PostgreSQL service with that canary Session's epoch and empty resource list. Readback confirmed T9 is `failed`. A focused Session test verifies startup does not terminalize stale Turns when provider reconciliation fails.
- T11 cancellation showed durable status `cancelled`; after service shutdown, Daytona label lookup confirmed all five roots across the two canary Sessions were `STOPPED`.

## Implementation coordinates and identities

- Ownership schema: migration 8 adds nullable `sandbox_ownership` to `fleet_lite_sessions` in `src/fleet_rlm/store.py`. New Sessions receive an explicit empty record; legacy `NULL` ownership remains fenced.
- Runtime coordinates: Store ownership and Turn fencing at `src/fleet_rlm/store.py:584-669`; lease takeover and resource reconciliation at `src/fleet_rlm/session.py:468-488`; Daytona ownership reconciliation at `src/fleet_rlm/sandbox/manager.py:838-872`.
- Dependency identity from `uv.lock`: Daytona 0.224.0, psycopg 3.3.6, psycopg-pool 3.3.3.
- Current working-tree source delta for F1 touchpoints is Store +157/-5, Session +62/-16, and manager +140/-68 lines. Manager and Session also contain preserved F0 edits, so these are not isolated F1-only counts. Current combined coverage report: Store 79% statements/71% branches, Session 89%/81%, manager 92%/86%.

## Remediation update (30 October 2026, evening)

- Provider-confirmed absence now counts as confirmation: a not-found answer from
  `client.get`, `client.stop`, or `client.delete` removes the recorded resource instead of
  fencing the Session forever. Recognition is the shared `sandbox.repl.provider_not_found`
  helper that `snapshots._is_not_found` now delegates to; every other provider error still
  fences. Regression: `test_provider_confirmed_absence_reconciles_as_confirmed_cleanup`,
  `test_other_provider_failures_still_fence_admission`.
- Admission with no outstanding resources no longer constructs a Daytona client, so a cold
  Session does not depend on provider reachability. Regression:
  `test_empty_ownership_never_builds_a_provider_client`.
- The ownership callback resolves the epoch at call time, so a manager that outlives a newer
  owner is fenced by the database rather than writing under a captured epoch.
- `Store.write_sandbox_ownership` was removed: it had no caller and no test.
- F1-T3 now has the coverage it previously lacked.
  `test_build_refuses_a_session_without_reconciled_sandbox_ownership` asserts both refusals
  (unknown ownership, unresolved cleanup), that the lease is closed, and that no provider
  reconciliation or Turn terminalization ran.
- F1-T1 gained the "crash after create intent, before the provider call" case in the existing
  subprocess harness; it asserts an empty label lookup still fences admission.
- Live: a pre-migration row answered `503` while an owned row answered `200`; a seeded
  nonexistent provider resource was reconciled by absence and cleared; an invalid record still
  answered `503`. See the remediation canary in [the ledger](F-live-canary-ledger.md).

The gap below stands: the crash-window semantics are still proved against a deterministic
provider double, not a real Daytona call interrupted mid-flight.

## Not yet accepted

- Daytona calls have not been tested under an actual API process crash while a provider operation is unresolved. The subprocess harness exercises the manager against a deterministic provider double, not Daytona or PostgreSQL.
- The new startup recovery path was covered offline and its Store method was exercised against PostgreSQL, but the API was not restarted again after this code change. Live cleanup of an operation interrupted inside an actual Daytona provider call was not exercised. The full F1 live acceptance matrix therefore remains incomplete.
