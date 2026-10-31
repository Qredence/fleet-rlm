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
| `GET`, `POST` | `/sessions/{session_id}/experiments` | List persisted experiments or submit an idempotent GEPA run request. |
| `GET` | `/sessions/{session_id}/experiments/{run_id}` | Read one experiment snapshot and its bounded saved comparison. |
| `POST` | `/sessions/{session_id}/experiments/{run_id}/cancel` | Request cooperative cancellation of a running experiment. |
| `POST` | `/sessions/{session_id}/experiments/{run_id}/review` | Explicitly approve or reject a candidate. |
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

Workspace listings and Turn input inventories include regular files beneath
`files/`, `artifacts/`, `memory/`, `skills/`, `buffers/`, and `sessions/` only.
Unmanaged top-level scratch files are left intact but are not advertised or
captured in replay snapshots. Symbolic links are not traversed. Invalid managed
paths or malformed inventory responses fail rather than becoming empty listings.

Routes that change or read workspace, context, memory, Skill, mode, or model state take the
session lock. While a Turn holds it they answer `409` after two seconds rather than queueing.
`POST /sessions/{session_id}/turn` takes the same lock and answers `409` the same way when another Turn
or workspace change is running; it holds the lock until the stream ends and the Turn has drained.

## Experiments

The experiments routes do not acquire a Session runtime lease. A `POST` start
body contains the shared optimizer options (`gepa_budget` or `max_metric_calls`,
`threads`, `reflection_minibatch_size`, `max_candidate_proposals`,
`validation_ratio`, and `trace_search`) plus a caller-generated `request_id`.
`validation_ratio` is inclusive 0.1–0.5 (default 0.35); invalid controls return
`422` before run creation. Unusable curated splits, including missing critical
holdout, fail preparation before search.
Repeating the same request ID with the same options returns the existing run;
reusing it with different options returns `409`. The API owns the accepted job
across client disconnection. It runs the existing optimizer as a subprocess,
records lifecycle state in PostgreSQL, and saves traces/evidence to the configured
MLflow experiment and candidates to the configured artifact directory.

There is one active optimization-or-review slot across Sessions and no queue.
An occupied or draining slot returns `409`. List returns persisted Session runs,
`active_run_id`, and whether the manager is accepting new work. Detail returns a
snapshot plus a bounded comparison document when one exists. The TUI polls these
snapshots every second; experiment progress is not an SSE stream.

Cancellation requires a running run and requests a cooperative stop. The worker
checks both its stop signal and the PostgreSQL stop flag at safe boundaries;
terminal status is written only after the worker exits and output drains. A
disconnect has no cancellation effect. The launcher drains API-owned experiments
before a normal service shutdown. If the API process restarts unexpectedly,
abandoned managed work is marked interrupted and is never automatically rerun.
Interrupted approval is reconciled against active artifact digests before the
candidate can be reviewed again.

Approval uses the same artifact, incumbent, configuration, source, and installed
dependency identity checks as standalone `fleet optimize --approve`. Review is an
explicit action; a passing optimization candidate is not installed automatically.
Standalone `fleet optimize` remains a separate synchronous CLI workflow. Legacy
per-Session JSON run files are imported idempotently into PostgreSQL and retained
read-only; PostgreSQL is authoritative for new run lifecycle.

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
`1`–`9`, and values above `3600` return `422` with validation errors for
`timeout_seconds`. OpenAPI describes both permitted integer ranges. The resolved
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
When Daytona returns build metadata, `snapshot_build_digest` records a hash of
the provider-reported image reference, build context hashes, and Dockerfile
content. This is observed provider evidence, not a claim that an arbitrary
custom snapshot matches Fleet's recipe. Custom snapshot names have a null
`snapshot_definition_digest`; only the repository-owned name receives Fleet's
intended recipe digest.
The identity fields appear on the `fleet_turn` trace span, in the stored Turn
record, and in the result returned by `GET /sessions/{session_id}/turns/{turn_id}`;
snapshot name, ID, and resource values come from the provider DTO. The definition
digest comes from Fleet's versioned definition; provider build metadata is hashed
separately. The same `turn_id` from the
terminal envelope joins the stored `fleet_lite_turns` primary key, the status response,
and the `fleet_turn` span, and those surfaces also agree on `route`, `outcome` (or
`answer`), `deadline_source`, `effective_seconds` (an explicit `null` when the Turn was
deadline-free), `duration_seconds`, and `cleanup`. `duration_seconds` is measured once
with a monotonic clock; MLflow derives its span duration from timestamps taken just
outside that measurement, so the two agree within two seconds. A Turn or replay row
with incomplete snapshot fields and no `deadline_source` remains readable as
`provenance_state: "pre-provenance"`, without backfilling absent values. A newer Turn with `deadline_source` but
incomplete required snapshot fields reports `provenance_state: "unresolved"`, for example
when snapshot preflight refused the Turn. All six snapshot fields must be present
for `provenance_state: "recorded"`; the optional provider build digest is
present only when Daytona returns build metadata.
The identity is recorded the
moment preflight resolves it, so a failure in a later preparation step (for example an
oversized replay payload) still reports the same six fields on the span, in the stored
failure, and in the `error` event. A Turn that fails before preflight resolves records
no snapshot fields. A Turn that executed against a root sandbox also reports the live
provider handle id as `root_sandbox_id` on the completion or failure event, on the
`fleet_turn` span, in the stored Turn record, and in the Turn status result. It is
omitted when the Turn never provisioned a sandbox.

The default Python 3.13 snapshot includes hash-locked packages for common data,
plotting, Excel/PDF, HTTP/HTML, and Python coding tasks. Git, Node.js/npm, pnpm,
TypeScript, ripgrep, and jq are available as commands. Bundled Skills prepare
specialist document, scientific/ML, and DSPy/GEPA dependencies only when needed,
using isolated uv environments outside the workspace. Optional packages do not
change the recorded base snapshot identity. The interpreter reports installed
modules and available commands as observations. See
[sandbox environment](sandbox-environment.md) for the package list, dependency
isolation, and staged snapshot verification.

Fleet's guarded workspace helper and Python file APIs provide sandbox file
access. Git CLI operations run inside the sandbox. The snapshot does not grant
private repository credentials or enable MarkItDown's cloud integrations;
package installation alone does not configure those services.

A completed Turn keeps its warm
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

Fleet validates the complete version-1 JSON envelope before native SSE framing.
Terminal events (`turn_complete`, `error`, `turn_cancelled`, and `done`) use explicit
payload models; dynamic activity cannot substitute for a malformed terminal event.
The envelope retains nested `payload` data and the existing flattened fields.
Historical replay preserves unknown fields and absent versus explicit null values;
it does not restore a live interpreter. The OpenAPI stream schema describes the
framed text, rather than claiming the whole response is one JSON object.

For example, a terminal `done` frame has the following form (the blank line ends
the frame; sequence and timestamp values vary):

```text
data: {"version":1,"event":"done","session_id":"session-a","turn_id":"turn-a","agent_id":null,"parent_agent_id":null,"sandbox_id":null,"seq":3,"timestamp":0.0,"payload":{}}
id: 3

```

Experiment list responses contain `experiments`, nullable `active_run_id`, and
`accepting`. Start, cancel, and review responses contain `experiment`; detail adds
nullable `comparison`. Public experiment models retain progress, options, failure,
gate, and review information while excluding private worker settings. Setup
experiment status and drain return `accepting`, `busy`, and `experiments`.

Workspace writes require exactly one non-null `content` or `content_base64` field.
An empty string writes an empty file. Neither or both returns the existing safe
422 detail, without echoing file content. OpenAPI expresses this cross-field rule.
Other workspace-write validation errors return only error type, location, and message;
rejected input and validation context are excluded from the response.

Sessions run one Turn at a time; a second Turn on a busy Session gets `409` after two seconds. Session IDs are
validated by the runtime; use simple URL-safe identifiers.
An optional idempotency key persists intent before execution. Completed requests
replay their recorded outcome; failed/uncertain requests do not replay side effects.
Replay outcome events carry the original persisted `turn_id`, so the status route
can recover that same outcome rather than looking up the retry's preparation ID.
A successful completion is assembled once and committed with the Turn record,
memory note and optional replay snapshot in one transaction. New result JSON includes
`turn_id`; historical completion reads supply it from the enclosing row without
rewriting history. Live completion and idempotent replay use the same committed
payload; the status route additionally derives `provenance_state`. See the
[result field and legacy inventory](turn-results.md).
Post-commit tracing, learned-note and delivery errors cannot downgrade success or
request cancellation. Disconnect cancellation is serialized with commit/readback;
a lost connection may deliver no completion, which a reconnect can recover. A lost
commit acknowledgment triggers a bounded readback of the claimed Turn. If it cannot
establish the outcome, Fleet stops remote work, leaves durable intent untouched and
blocks reexecution. Workspace file bytes do not participate in the SQL transaction.
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
still apply. Closing a stream before durable completion requests remote
cancellation and drains the worker, serialized against commit/readback. After durable completion, disconnecting
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

## Private local setup API

Setup is enabled only for a CLI-owned loopback server with a private per-launch
bearer token. Every setup route requires this token and a loopback connection.
Ordinary `fleet serve`, existing API reuse, and remote `--url` clients do not gain
setup privileges. Disabled setup returns 404; missing/invalid credentials return
401 and non-loopback callers return 403. Setup does not add authentication to the
ordinary Session API.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/setup/status` | Sanitized fields/sources, readiness results, operation availability. |
| `PATCH` | `/setup/config` | Stage supported edits with `{"values":{"ENV_NAME":"value"}}`; never echo secret input. |
| `DELETE` | `/setup/config` | Discard unsaved edits and reload configuration. |
| `POST` | `/setup/save` | Validate and atomically save staged edits to the fixed project `.env`. |
| `POST` | `/setup/verify` | Run read-only checks of Models, Database, MLflow, and Daytona. |
| `GET` | `/setup/daytona/volumes` | List safe Volume identities for the configured organization. |
| `POST` | `/setup/daytona/volumes` | Explicitly create a named workspace Volume. |
| `POST` | `/setup/daytona/snapshot/build` | Build the versioned packaged recipe; refuse an existing snapshot name. |
| `POST` | `/setup/daytona/snapshot/activate` | Explicitly activate and verify an existing snapshot. |
| `POST` | `/setup/models/test` | Make one optional, potentially billed root-model request. |
| `GET` | `/setup/operations/{operation_id}/events?after=0` | Replay retained progress and stream SSE until `complete` or `error`. |
| `GET` | `/setup/experiments/status` | Read whether the owned experiment is active and draining. |
| `POST` | `/setup/experiments/drain` | Stop admitting jobs and wait for API-owned experiments to finish. |
| `POST` | `/setup/apply` | Require saved/verified settings, fence new Turns, and request a launcher restart. |

Background actions take a `request_id`; Volume creation also takes `name`.
Verification accepts optional `sections` (`models`, `database`, `mlflow`,
`daytona`), defaulting to all sections. Results from other sections are reused
only for the same effective configuration. They
return `operation_id`. Reusing a request ID for the same action returns the
existing operation; another active operation returns 409. Events have `seq`,
`event`, and a bounded redacted `message`, with a safe result on completion.
Provider failures are sanitized. Disconnect does not cancel remote work or start
it again. A process exit can leave an unknown provider outcome: recheck the
snapshot/Volume before issuing another mutation. Progress retention is bounded
and process-local; completed operation IDs are not durable job identifiers.

Configuration edits are drafts until Save. Only supported fields are accepted;
process environment overrides are read-only. File conflicts and running operations
return 409. Save preserves unrelated dotenv entries and restricts file permissions.
`apply` returns `{"restart_required":true}`; the owning CLI performs the restart.
It returns 409 while any Turn, workspace operation, or API-owned experiment is
active. On normal terminal-client exit, the owning launcher uses the protected
experiment drain/status routes and waits for the worker and API/MLflow cleanup
before stopping services it owns. Once accepted,
apply is idempotent: status reports `restart_required=true`, and repeating apply
recovers the same restart request without repeating provider work.

Setup-mode `/health` returns `status="setup"` and `mlflow=null`; it must not be
reused as a ready reasoning API. Setup mode exposes no Session routes and opens no
database or tracking resources. After the controlled restart, normal startup
initializes the schema and tracing destination under the existing lifecycle.
