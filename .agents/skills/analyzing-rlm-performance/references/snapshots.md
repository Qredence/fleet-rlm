# Sandbox startup regressions

Separate sandbox creation, dependency preparation, mount setup, interpreter
readiness, and cleanup before attributing a regression to cold starts.
Compare equivalent pinned images and workload requirements.

A snapshot is useful when stable dependencies or startup files account for
material repeated cost. Verify its actual package/runtime contents and rebuild
cost. Exclude request workspaces, credentials, and host-tool bindings.

Consider prewarming only when creation materially affects the target SLO.
Inspect the current policy and lease/reset/scrub implementation; snapshot
availability does not establish that a warm pool is safe or enabled. Preserve
volume ownership, child isolation, cancellation, and observed cleanup.

Use the existing [snapshot guide](../../../../docs/how-to-guides/daytona-snapshot.md)
for verification procedures and the [ADR 006 ledger](../../../../docs/decisions/006-implementation-status.md)
for promotion evidence. Consult [Daytona snapshots](https://www.daytona.io/docs/en/snapshots/)
when SDK behavior changes. Local measurements cannot certify provider startup,
containment, or cleanup; label historical receipts by revision and workload.
