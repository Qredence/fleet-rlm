# HTTP API reference

Fleet exposes a FastAPI application with the following routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Return status, application version, and initialized MLflow destination. |
| `GET` | `/sessions` | List persisted sessions (up to 100). |
| `GET` | `/models?scope=system.ai` | Discover authorized chat-capable Unity Gateway model services (up to 1000). |
| `DELETE` | `/sessions/{session_id}` | Stop the session sandbox and release its runtime and lease; history is kept. 404 without a runtime, 409 during a Turn. |
| `GET` | `/sessions/{session_id}/config` | Inspect non-secret models, mode, dataset context, configuration, and limits. |
| `PUT` | `/sessions/{session_id}/model` | Select and persist an accessible model for the `root` or `sub` role. |
| `GET` | `/skills?session_id=...` | Return the selected session's Skill catalog. |
| `POST` | `/skills/pull?session_id=...` | Export sandbox Skills to the configured local user Skill directory. |
| `GET` | `/sessions/{session_id}/files` | List workspace files and generated artifacts. |
| `POST` | `/sessions/{session_id}/attach` | Attach a multipart file to `files/` in the session workspace. |
| `POST` | `/sessions/{session_id}/turn` | Run a Turn and stream progress as SSE. |
| `GET`, `PUT` | `/sessions/{session_id}/mode` | Read/change persisted `rlm`, `flex`, or `hybrid` mode. |
| `PUT`, `DELETE` | `/sessions/{session_id}/context` | Select a content-addressed dataset or clear the selection. |
| `GET` | `/sessions/{session_id}/history` | Read bounded committed turn history directly from Postgres; no runtime lease. |
| `GET` | `/sessions/{session_id}/turns/{turn_id}` | Recover a durable running/terminal turn outcome directly from Postgres without replaying it; available while another worker owns the Session. |
| `GET` | `/sessions/{session_id}/workspace/tree` | List workspace paths. |
| `GET` | `/sessions/{session_id}/workspace/stat/{rel_path}` | Get a file's size and SHA-256. |
| `GET`, `PUT`, `DELETE` | `/sessions/{session_id}/workspace/files/{rel_path}` | Download/write/delete workspace files. |
| `GET` | `/sessions/{session_id}/memory?q=...` | Retrieve scoped memories. |
| `PUT`, `DELETE` | `/sessions/{session_id}/memory/{memory_id}` | Create/review/update/delete memories. |
| `POST` | `/skills/push?session_id=...` | Synchronize validated local user Skills. |

The `/health` MLflow metadata includes `workspace_host`, the resolved non-secret
Databricks tracking workspace URL (`null` for local tracking). API reuse and
evaluation preflight compare it along with the experiment and trace destination.
Named tracking profiles resolve their own workspace rather than the model gateway host.

Routes that change or read workspace, context, memory, Skill, mode, or model state take the
session lock. While a Turn holds it they answer `409` after two seconds rather than queueing.
`POST /sessions/{session_id}/turn` takes the same lock and answers `409` the same way when another Turn
or workspace change is running; it holds the lock until the stream ends and the Turn has drained.

There is no caller authentication. The service binds to loopback by default;
binding elsewhere does not add authentication. Only use on a trusted host or
behind a separately secured boundary.

Model selection accepts `{"role":"root","model":"openai/system.ai.service"}`.
The service must appear in discovery for its catalog/schema. Selection is
session-scoped; a busy turn returns 409, an inaccessible/non-chat model returns
422, and discovery/persistence failures return a sanitized 503 without replacing
the current engine. Model, mode, and context settings survive independent updates
and server restart. Operator-owned secrets and resource limits are not writable
through this endpoint. Accessible metadata does not guarantee inference readiness.

## Attach a file

Send a `multipart/form-data` body with a `file` field. The upload limit is
controlled by `FLEET_MAX_UPLOAD_BYTES` (default 25,000,000 bytes). The response
contains the mounted path and relative workspace path, for example
`/workspace/files/input.csv` and `files/input.csv`.

## Run a Turn

The JSON request has one required `task` string:

```json
{"task":"Summarize the attached CSV and verify the totals with Python.","idempotency_key":"ledger-review-1"}
```

`record_replay` defaults to `false`. Set it to `true` only when this Turn's
inputs should be retained as a possible reviewed GEPA example. Fleet captures
the exact bounded conversation, memory, selected dataset handle, Skills
catalog/revision, non-secret runtime configuration, and workspace file bytes
(up to 8 MB total). The snapshot
is stored in Lakebase only after the Turn completes successfully and is not
eligible for GEPA until its MLflow trace is explicitly labeled `approved` with
gold expectations. This option can retain sensitive workspace content; use it
only for data approved for that purpose. An oversized workspace fails before
the engine runs. The TUI exposes the same opt-in with `/replay on` and `/replay off`.

`timeout_seconds` is an optional strict integer and the only per-Turn deadline
override. Omitting it or sending `null` (the default) inherits
`turn_timeout_seconds`, or `900` when configuration does not set it. `0` disables
the Fleet wall-clock deadline for this Turn. A positive value must be `10`–`3600`
and sets this Turn's duration. Booleans, floats, numeric strings, negatives,
`1`–`9`, and values above `3600` return `422` with an error whose `loc` ends in
`timeout_seconds` and whose message names the `0 or 10..3600` rule. The resolved
source is reported as `request`, `configuration`, or `default`, never `command`.
A deadline-free Turn reports `effective_seconds: null`; it still reports the
provider-owned `ttl_minutes` and `auto_stop_interval` bounds, which Fleet does not
remove or shorten.

The response uses `text/event-stream`. Each data event is JSON prefixed by
`data: ` and separated by a blank line. The server initially emits a
`turn_status` preparation event, then DSPy callback events such as
`module_start`, `lm_end`, `tool_start`, and `tool_end`. An `lm_end` event carries a `usage` object with
numeric `prompt_tokens`, `completion_tokens`, `total_tokens`, and, when the provider reports them,
`reasoning_tokens`; it is omitted when the provider reports no usage. Completion is reported as
`turn_complete` with `answer`, `route`, optional `learned_skill_note`, and an
`artifacts` list. When MLflow tracing is configured, the completion event also
includes the recorded `trace_id`. A Turn that provisioned its sandbox also reports
the preflight-resolved environment identity as `snapshot_name`, `snapshot_id`,
`snapshot_cpu`, `snapshot_mem`, `snapshot_disk`, and `snapshot_definition_digest`.
The same six fields appear on the `fleet_turn` trace span, in the stored Turn
record, and in the result returned by `GET /sessions/{session_id}/turns/{turn_id}`;
they are read from the provider DTO, never re-derived. The same `turn_id` from the
terminal envelope joins the stored `fleet_lite_turns` primary key, the status response,
and the `fleet_turn` span, and those surfaces also agree on `route`, `outcome` (or
`answer`), `deadline_source`, `effective_seconds` (an explicit `null` when the Turn was
deadline-free), `duration_seconds`, and `cleanup`. `duration_seconds` is measured once
with a monotonic clock; MLflow derives its span duration from timestamps taken just
outside that measurement, so the two agree within two seconds. A Turn or replay row
with incomplete snapshot fields and no `deadline_source` remains readable as
`provenance_state: "pre-provenance"`, without backfilling absent values. A newer Turn with `deadline_source` but
incomplete snapshot fields reports `provenance_state: "unresolved"`, for example
when snapshot preflight refused the Turn. All six snapshot fields must be present
for `provenance_state: "recorded"`.
The identity is recorded the
moment preflight resolves it, so a failure in a later preparation step (for example an
oversized replay payload) still reports the same six fields on the span, in the stored
failure, and in the `error` event. A Turn that fails before preflight resolves records
no snapshot fields. A Turn that executed against a root sandbox also reports the live
provider handle id as `root_sandbox_id` on the completion or failure event, on the
`fleet_turn` span, in the stored Turn record, and in the Turn status result. It is
omitted when the Turn never provisioned a sandbox. A completed Turn keeps its warm
root for the next Turn and reports the same id; after a failed or cancelled Turn the
stopped root is discarded, so the next Turn provisions a new sandbox with a new id and
does not recover the terminal Turn's interpreter state. Failures produce a sanitized `error` event
(which also carries the `trace_id` when tracing is configured) followed by stream completion.
The one surfaced exception is a snapshot preflight refusal: its event and stored failure carry
the repository-owned snapshot name, the observed state/resources, the required floors, and the
build command, because that text is constructed by Fleet rather than taken from an arbitrary
exception. Every other failure text remains sanitized. Every Turn's stream
ends with exactly one outcome event followed by the terminal `done` envelope, which
carries the same `session_id`, `turn_id`, `seq`, and SSE `id` as the rest of the
stream and is delivered after teardown. If the Turn's worker stops without reporting
an outcome, the server sends a sanitized `error` and closes the stream. The connection
may emit SSE comment keep-alives.

Sessions run one Turn at a time; a second Turn on a busy Session gets `409` after two seconds. Session IDs are
validated by the runtime; use simple URL-safe identifiers.
An optional idempotency key persists intent before execution. Completed requests
replay their recorded outcome; failed/uncertain requests do not replay side effects.
Replay outcome events carry the original persisted `turn_id`, so the status route
can recover that same outcome rather than looking up the retry's preparation ID.
A completed outcome commits in the same transaction as files and turn history;
a lost response or later delivery failure cannot downgrade that durable completion.
A PostgreSQL advisory lease rejects a second worker owning the same session.
The lease covers Turn execution and mutations. History and Turn status are
durable reads that use the shared pool and do not take it, so they remain
available from any worker. A worker that loses its lease evicts the stale
runtime and rebuilds it on the next request once any draining Turn finishes.

Events include `version`, `session_id`, `turn_id`, agent/parent/sandbox IDs,
monotonic per-stream `seq`, timestamp, and `payload`. Flat payload fields remain
for compatibility. Generated code, bounded outputs, allocation/deletion, routing,
and call-budget events are available. Queues apply producer backpressure.
Module/model/tool activity has paired `*_start` and `*_end` events with `call_id`;
DSPy callbacks also include `parent_call_id`. End events report `ok`, `error_type`,
and `duration_ms`. Tool `inputs` and `output` are bounded, redacted text previews;
`source` distinguishes `dspy` callbacks from `sandbox_bridge` operations. These
may be nested observations, not additional model calls. Predictor payloads are
omitted rather than exposing private reasoning. Previews redact sensitive keys
and labelled credentials but cannot detect every secret in arbitrary text.
`repl_code`/`repl_output` share an `execution_id`, namespace, and `phase`
(`bootstrap` or `task`); output includes timing, stdout/stderr, a bounded `value`
preview, and error status. `triage_decision.summary` describes the selected route
or enforced fallback; it is not a model's private chain-of-thought. These are
live activity events, not a durable replay log; `/history` retains turn outcomes.
Long-running Python and host-tool calls have no independent 120-second bridge
or 180-second execution-read limit. Host-call waiting derives from the Turn's
remaining deadline, so a bridged call never outlives a bounded Turn, and only a
bounded Turn arms the watchdog. A deadline-free Turn sends `run_code(timeout=0)`
explicitly and no Fleet component substitutes a fallback bound, so the call waits
until it returns or the Turn is cancelled; Daytona's provider TTL and idle bounds
still apply. Closing a stream before the server observes an outcome requests remote
cancellation and drains the worker. After an observed outcome, disconnecting
preserves a successful Turn's warm root while the worker finishes. Reconnection
is not an automatic side-effect retry.

New completion events and their persisted idempotent outcomes also include
host-owned `lm_calls`, `sandbox_errors`, `submit_rejections`, and
`budget_breakdown`. The legacy `lm_calls` name means shared budget units rather
than visible model requests. Breakdown keys are `generation_query`, `routing`,
`skill_selection`, and `other_decisions`; their values sum to `lm_calls`.
`sandbox_errors` counts remote Python failures, while `submit_rejections` counts
blocking host rejections. Missing counters in historical outcomes remain unknown.
`budget_update` events include the same current counters and breakdown.
One `sandbox_environment_incomplete` event per durable Session lists `missing`:
the probed modules absent from the snapshot. The marker is stored on the Session
row, so `DELETE /sessions/{id}` and a rebuilt runtime do not repeat the event.
It is observational only. The model-visible environment string still reports the
installed and missing sets, and a missing optional package never fails a Turn or
increments `sandbox_errors`.
`repl_output.outcome` distinguishes `remote_code_error` from
`ungrounded_submit_rejected`, so a host rejection is not displayed as a remote
Python failure during recovery.

`learned_note_warning` carries a `namespace`, `field` (`learned_skill_note`),
and bounded `figures` list. It does not block submission. Existing
`ungrounded_submit` events describe substantive-output rejections or acceptance
after the retry cap. Learned-note warnings do not establish note correctness.

Workspace writes accept exactly one of `content` (UTF-8 text) or
`content_base64`. Reads return bytes with an attachment disposition. Mode and
dataset selection persist across server restarts. Dataset hashes are checked
again at execution, so changing a source requires selecting its new revision.
Memory writes accept `content`, `kind`, source path-to-digest mappings, and an
explicit `reviewed` flag; only reviewed retrieval is included as turn evidence.

## Skills and files

`GET /skills` and `POST /skills/pull` accept an optional `session_id` query
parameter, defaulting to `default`. Local user Skills are read from
`.fleet/skills/` by default. Developer skills in `.agents/skills/` are not
synced as user Skills.

`GET /sessions/{session_id}/files` returns `files` and an `artifacts` subset;
it does not download file contents. The workspace is session-scoped and
persisted on the Session's Daytona Volume.
