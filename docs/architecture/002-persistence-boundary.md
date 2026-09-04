# ADR 002: Persistence boundary

**Status:** accepted
**Decision date:** 2026-09-04

Database rows and Workspace Volume bytes are durable Fleet state. The
interpreter namespace, `REPLHistory`, live broker process, and in-memory
registries are ephemeral execution state.

A sandbox may be stopped, deleted, or recreated without losing committed
Session correctness. Restart/recreation restores only durable inputs: committed
Turn history, authorized Workspace files, attachments, artifacts, and explicit
Workspace Memory. It must not depend on Python globals, broker state, or an
interpreter namespace being present.

## Consequences

- Turn Commit remains the only transition that makes a conversational result
  durable.
- Artifact candidates are private until their bytes validate and their Turn
  commits.
- A lost SessionSandbox taints the active execution resources, not the durable
  Session; the next eligible Run rehydrates from durable state.
- Fleet does not serialize or reconstruct DSPy's native `REPLHistory`.
