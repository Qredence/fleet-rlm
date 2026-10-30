# Turn result inventory and ownership

`SessionRuntime.commit_turn` assembles the successful result once, before calling
Store, and returns it only after Store confirms its existing transaction. The
coordinator emits that value through the SSE envelope. A repeated idempotency key
reads the same stored result without invoking DSPy again. This result is Fleet
application data; the engine continues to return native DSPy `Prediction` values.

## Field inventory

This is the field and legacy-normalization inventory for PRD B. The baseline is
`45795ffe26ef1d61ce671a6b5beb917a74b9d1c0`; representative baseline frames and old
rows are retained in `tests/fixtures/turn_results.json`. Successful baseline live
frames already included `turn_id`; baseline stored results obtained it from their
enclosing row. New successful results include it in their JSON as well.

| Field | Successful result: stored, live and idempotent replay | Failure/cancellation and historical policy |
|---|---|---|
| `event` | `turn_complete` | `error` or `turn_cancelled` for new failures; prior non-completed requests become the existing generic `error` on idempotent replay |
| `turn_id` | Required; copied from the claimed Turn record | New failures include it; recovery of old completed results takes it from the row |
| `answer`, `route`, `learned_skill_note` | Present; Session defaults missing record values to empty strings | Absent on new failures; old completed results retain their original missing/null values |
| `artifacts` | Present, sorted unique `artifacts/` paths from the existing workspace listing | Absent on failures; old results retain their original artifact value or absence |
| `lm_calls`, `sandbox_errors`, `submit_rejections`, `budget_breakdown` | Copied when present in the record, preserving explicit null; coordinator records all four (null when the Prediction has no value) | Absent on new failures; historical keys/values remain unchanged |
| `snapshot_name`, `snapshot_id`, `snapshot_cpu`, `snapshot_mem`, `snapshot_disk`, `snapshot_definition_digest`, `snapshot_build_digest` | Provider-resolved values copied when present, including null | Same environment fields on new failures; no historical backfill |
| `deadline_source`, `effective_seconds` | Copied when present; disabled Fleet deadline is explicit `effective_seconds: null` | Same resolved pair on new failures; absent historical fields remain absent |
| `outcome`, `duration_seconds` | `completed` and one measured duration from the record | `failed`/`cancelled`, with duration measured after teardown; old results are not rewritten |
| `trace_id` | Present only for a truthy trace ID | Same on new failures; historical missing/null values remain unchanged |
| `cleanup` | `not_required` for a successful coordinator Turn; Session includes a truthy record value | New failures report `stopped` or `unconfirmed` after teardown; historical values remain unchanged |
| `root_sandbox_id` | Included when the Turn used a live root handle | Captured before failure teardown; historical absence remains absent |
| `ttl_minutes`, `auto_stop_interval`, `maximum_lifespan_minutes`, `provider_owned` | Copied when present, preserving null and false | Provider lifecycle bounds, independent of Fleet deadlines; same fields on new failures |
| `request_id`, `error` | Absent | New failures include the request ID and sanitized message; snapshot refusals and idempotency conflicts retain their bounded caller-safe details |
| Unknown historical result keys | Preserved, including nested data, by replay and status reads | No strict schema or history rewrite introduced |
| `provenance_state` | Derived only by `GET /sessions/{session_id}/turns/{turn_id}` | Existing normalization: `pre-provenance` before deadline/provenance recording; `unresolved` when required snapshot fields are incomplete; otherwise `recorded` |
| Envelope fields | `version`, `session_id`, `agent_id`, `parent_agent_id`, `sandbox_id`, `seq`, `timestamp`, and nested `payload` are transport data | Envelope also mirrors payload fields at top level; compare canonical event plus payload, excluding envelope timing/sequence |

Record-only fields remain on history/tracing/replay inputs: `task`,
`workspace_manifest`, `skills_catalog`, `idempotency_key`, `execution_mode`,
`context_files`, `artifact_changes`, `replay_snapshot_selected`, and
`replay_snapshot_sha256`. They are not new completion fields. Execution evidence
remains a span/host-receipt concern. Arbitrary record fields are not promoted into
new results; unknown fields already stored in a historical result are preserved.

The historical boundary rules are narrow: completed idempotent replay copies the
stored JSON and supplies its enclosing `turn_id`; the status route additionally
derives `provenance_state`. Failed, cancelled and running rows keep the existing
generic idempotency refusal rather than replaying execution or historical errors.
No SQL migration, result version, or bulk backfill is required.

## Commit, observation and delivery

Session copies the record and gives Store its own result copy. The coordinator
records the returned completion before any phase outputs or context exit, and
copies terminal data at delivery. Trace mutations, learned-note writes, span exit,
and client-delivery exceptions cannot send a committed Turn into failure teardown.
Diagnostics record the Turn ID and exception class, without exception messages,
prompts, provider payloads or artifact contents.

A commit exception triggers one coordinator readback using the claimed Session
and Turn ID (in addition to Store's existing bounded connection retry). A completed
row proves success and supplies the result. A readable non-completed row follows
the ordinary terminal failure/cleanup path. If the row is absent or readback is
unavailable, cleanup still stops remote work, but the coordinator leaves durable
intent untouched and reports the existing uncertain-outcome refusal. A later
idempotency request can recover a completed row or refuse an unresolved row; it
never reruns generated code to resolve uncertainty.

The API shares an in-memory commit signal and lock with the coordinator. The lock
serializes disconnect cancellation against commit/readback and signal publication.
Cancellation that wins before commit is checked before persistence; cancellation
after the committed signal preserves success. This adds no durable status. Turn
finalization ends the watchdog before terminal delivery; `done` remains last when
the channel can carry it. A closed or unusable channel may deliver no success;
reconnect recovers it using the same idempotency key.

## Handoffs and rollback

The optional `artifact_paths` argument to `commit_turn` is the one handoff for a
future F3 captured logical artifact view. It reuses path validation and sorting;
when absent, B uses the existing workspace listing. B does not claim filesystem atomicity
or immutable remote bytes, and does not implement F2 publication. C can validate
this result shape while retaining the legacy read rules above.

Roll back Session's return contract, coordinator and transport signal together,
including their test callers. Keep completed rows and additive `turn_id` fields;
older readers accept those extra JSON keys. Do not rewrite success as failure or
claim that history restores live interpreter state. If C introduces stricter
readers later, retain its legacy reader or roll those changes back together.
