# ADR 005: Retired execution-architecture selector

Status: superseded by Phase 6 consolidation.

This ADR records the temporary migration selector that was removed after Fleet
consolidated on retained broker execution. `runtime.environment` identifies the
provider environment (`daytona`), while `runtime.live_enabled` controls operator
admission. Neither selects an execution architecture.

Settings and the schema-derived editor no longer expose `runtime.variant`.
Policies containing that key are rejected before composition, avoiding a silent
fallback during promotion or rollback.

Historical receipts may retain `runtime_variant` as an identity field. New
receipts bind exact code, lock, profile, images, database head, and quality
identities through the Phase 6 promotion-bundle contract. The
[ADR 006 ledger](006-implementation-status.md) records live cutover and
rollback evidence separately.
