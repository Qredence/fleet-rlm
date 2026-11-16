# Turn results

A successful Turn is assembled once, committed with its record, then returned
through SSE and idempotent replay. `GET /sessions/{id}/turns/{turn_id}` recovers
its durable outcome without executing again.

| Fields | Meaning |
| --- | --- |
| `event`, `turn_id`, `outcome`, `duration_seconds` | Terminal identity, status and measured duration |
| `answer`, `route`, `learned_skill_note`, `artifacts` | Successful answer, selected route, note and Session artifact inventory |
| `lm_calls`, `sandbox_errors`, `submit_rejections`, `budget_breakdown` | Host usage/error counters; `lm_calls` means shared budget units |
| `requested_skills`, `required_skills` | Ordered intent and captured source/digest receipts |
| `replay_snapshot_selected` | Whether this Turn committed immutable replay inputs |
| `trace_id`, `root_sandbox_id` | Trace and provisioned root identity when available |
| `snapshot_name`, `snapshot_id`, `snapshot_cpu`, `snapshot_mem`, `snapshot_disk` | Provider-resolved execution environment |
| `snapshot_definition_digest`, `snapshot_build_digest` | Intended recipe and optional provider build metadata hashes |
| `deadline_source`, `effective_seconds` | Resolved deadline; null seconds means disabled |
| `ttl_minutes`, `auto_stop_interval`, `maximum_lifespan_minutes`, `provider_owned` | Independent provider lifecycle bounds |
| `cleanup` | `not_required`, `stopped` or `unconfirmed` |
| `request_id`, `error` | Failure identity and sanitized explanation |
| `assistant_message_id`, `files` | Durable assistant message and published outputs |

Status reads derive `provenance_state`: `recorded` when required snapshot fields
exist, `unresolved` when incomplete (including sandbox-free Direct), and
`pre-provenance` for older records. Historical missing/null/unknown values are
preserved. Transport headers and nested `payload` are described in [HTTP API](http-api.md#sse-contract).

Success cannot be reversed by tracing or delivery failure. A lost commit
acknowledgment triggers bounded readback; unresolved intent blocks reexecution
and stops remote work. Disconnect is serialized with commit. Recover a lost
completion by status or the same idempotency key; history cannot restore Python
variables. Workspace bytes are outside the SQL transaction.
