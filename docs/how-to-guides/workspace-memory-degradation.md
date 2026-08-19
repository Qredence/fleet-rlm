# Workspace Memory degradation diagnostics

Workspace Memory preparation is intentionally fail-soft: a Turn proceeds even
when Storage, the mounted Workspace agent, or Memory search degrades, because
Memory context is optional. Fail-soft must not mean invisible. Every degraded
preparation operation records exactly one bounded, sanitized diagnostic so
operators can tell *why* Memory context fell back.

## Where diagnostics appear

- **Logs**: `WARNING` records from `fleet_rlm.daytona.memory_diagnostics`
  with the pattern
  `Workspace Memory degraded: category=… operation=… runtime=… cause_type=… outcome=…`.
- **MLflow Turn traces** (when tracing is enabled by the selected TOML
  profile): the same five fields are attached to the active `fleet_turn` span
  as `fleet.memory_degradation.*` attributes.

Diagnostics are bounded: they contain only the fields above. Memory contents,
search queries, file paths, exception messages, environment values, and other
high-cardinality or sensitive payloads are never attached, and emission is one
record per degraded operation, never per record or token. When tracing is
disabled or MLflow is unavailable, the structured log still appears and the
Turn result is unaffected.

## Categories

| `category` | Likely area |
| --- | --- |
| `normalization` | Turn request could not be normalized into a search query (input preparation). Memory falls back to the recency-only digest. |
| `provider_unavailable` | The mounted Workspace agent / Volume storage failed or is unreachable (Daytona Sandbox, Volume mount, agent process). Memory falls back to recency-only or no injection. |
| `corrupt_record_set` | A mounted-agent Memory payload violated its checked response shape (store corruption or provider-side tampering). |
| `invariant_violation` | The durable store contains duplicate/stable-id rows that fail closed. Repair or dedupe `memory/MEMORIES.md` in the Workspace Volume. |
| `search_failure` | The lexical relevance-search machinery failed after normalization succeeded. Memory falls back to the recency-only digest. |
| `legacy_migration` | The legacy root `MEMORIES.md` → `memory/MEMORIES.md` migration/read sequence failed (for example a non-regular file at the legacy path). |
| `unexpected_internal` | None of the above matched: a programming defect or broken invariant. File a bug; the `cause_type` field names the exception class. |

`operation` identifies the fail-soft seam: `normalize_query`,
`relevance_search` (falls back to the recency-only digest), or
`injection_digest` (falls back to no Memory injection).
`fallback_outcome` is `recency_only_digest` or `no_memory_injection`.

## What stays strict

Degradation observability never loosens existing guarantees: Memory mutations
(`remember`, `edit_memory`, `forget`) and `list_memories` still fail closed on
duplicate ids, invalid records, or unavailable storage, and surface through
the normal Tool error path (`unavailable`/`full`/`invalid_*` codes). Only the
optional read-side Turn preparation is fail-soft.
