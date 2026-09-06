# ADR 005: One execution-architecture selector

Status: implemented for the legacy runtime.

`runtime.variant` is the sole migration selector. Its default and only supported
value is `legacy`. `runtime.environment` identifies the provider environment
(`daytona`), not an alternate execution architecture. `runtime.live_enabled`
controls operator admission, not architecture selection.

Settings validation rejects unsupported variants before runtime composition.
The schema-derived settings editor exposes only implemented choices. Native and
capsule are not selectable until their owning implementation and validation gates
land. Unknown legacy selector keys remain rejected by the policy schema.

Omitted variant values resolve to `legacy` for existing policy compatibility;
the committed policy names it explicitly. Tests pin both defaults and editor
rejection without writes. Lifecycle benchmark receipts record `runtime_variant`.
Future benchmark formats must carry the same identity for comparisons.
