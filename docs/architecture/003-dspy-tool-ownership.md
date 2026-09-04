# ADR 003: DSPy tool ownership

**Status:** accepted
**Decision date:** 2026-09-04

The repository-pinned DSPy RLM owns its native semantic-query built-ins:

- `llm_query`
- `llm_query_batched`

Fleet owns host capabilities and recursive execution tools, including
`rlm_query`, `rlm_query_batched`, Workspace/Attachment/Artifact tools, skill
loading, and the caller-owned interpreter boundary. Fleet projects observation
evidence and enforces Run authority, but it does not replace DSPy's native RLM
history or trajectory semantics.

Fleet must not define, register, wrap as a Fleet implementation, or introduce
an application function named `llm_query` or `llm_query_batched`. Those names
are reserved for DSPy native built-ins. Test doubles may expose the names only
at the DSPy interpreter seam to emulate the native contract.

## Consequences

- `max_llm_calls` remains DSPy's native call budget; recursive depth is a
  distinct Fleet policy.
- Fleet custom tools have distinct, capability-specific names.
- Any migration that changes the native built-in contract requires an explicit
  DSPy version/behavior review and public event contract review.
